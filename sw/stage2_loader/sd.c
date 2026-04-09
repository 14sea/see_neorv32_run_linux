// SPDX-License-Identifier: GPL-2.0+
/*
 * sd.c — minimal SD-over-SPI driver for stage2_loader
 *
 * Uses NEORV32 SPI peripheral @ 0xFFF80000, CS0 → SD card.
 * Polling mode, no IRQ, no DMA. Goal: read raw 512B blocks fast enough
 * that host-side `sd_pack.py` can place Image/DTB/initramfs at fixed LBAs
 * and stage2 loads them in a few seconds instead of 145s xmodem.
 *
 * Smoke test (sd_smoke): init + read LBA 0 + hex-dump first 32 bytes.
 */

#include <stdint.h>
#include "sd.h"

#define SPI_CTRL  (*(volatile uint32_t *)0xFFF80000UL)
#define SPI_DATA  (*(volatile uint32_t *)0xFFF80004UL)

#define CTRL_EN      (1u <<  0)
#define CTRL_CPHA    (1u <<  1)
#define CTRL_CPOL    (1u <<  2)
#define CTRL_PRSC(n) (((n) & 7u) << 3)
#define CTRL_CDIV(n) (((n) & 0xFu) << 6)
#define CTRL_RX_AVL  (1u << 16)
#define CTRL_BUSY    (1u << 31)

/* DATA register command-mode bits (bit31=1 means CS control, not byte xfer) */
#define DATA_CMD     (1u << 31)
#define DATA_CSEN    (1u <<  3)

extern void uart_puts_ext(const char *s);      /* from main.c */
extern void uart_puthex32_ext(uint32_t v);
extern void uart_putc_ext(char c);

/* ── Low-level SPI ──────────────────────────────────────────────────────── */

static void spi_setup(int prsc, int cdiv)
{
    /* Mode 0: CPHA=0, CPOL=0 */
    SPI_CTRL = CTRL_EN | CTRL_PRSC(prsc) | CTRL_CDIV(cdiv);
}

static uint8_t spi_xchg(uint8_t tx)
{
    while (SPI_CTRL & CTRL_BUSY) { }
    SPI_DATA = tx;                               /* bit31=0 → data byte */
    while (!(SPI_CTRL & CTRL_RX_AVL)) { }
    return (uint8_t)(SPI_DATA & 0xFF);
}

static void spi_cs_assert(void)   { SPI_DATA = DATA_CMD | DATA_CSEN | 0; }
static void spi_cs_release(void)  { SPI_DATA = DATA_CMD | 0; }

/* ── SD card init ──────────────────────────────────────────────────────── */

static int sd_is_hc = 0;

static uint8_t sd_cmd(uint8_t cmd, uint32_t arg, uint8_t crc)
{
    /* flush any residual byte, then 6-byte command frame */
    spi_xchg(0xFF);
    spi_xchg(0x40 | cmd);
    spi_xchg((arg >> 24) & 0xFF);
    spi_xchg((arg >> 16) & 0xFF);
    spi_xchg((arg >>  8) & 0xFF);
    spi_xchg( arg        & 0xFF);
    spi_xchg(crc);

    /* Wait for R1 (bit7=0). Up to 16 bytes per spec. */
    uint8_t r1 = 0xFF;
    for (int i = 0; i < 16; i++) {
        r1 = spi_xchg(0xFF);
        if (!(r1 & 0x80)) break;
    }
    return r1;
}

int sd_init(void)
{
    sd_is_hc = 0;

    /* Slow clock for init: PRSC=5 (/1024), CDIV=0 → 50MHz/1024 ≈ 48.8 kHz */
    spi_setup(5, 0);

    /* 1) Power-up: >74 dummy clocks with CS deasserted */
    spi_cs_release();
    for (int i = 0; i < 16; i++) spi_xchg(0xFF);  /* 128 clocks */

    /* 2) CMD0: GO_IDLE_STATE, CRC7 = 0x95 */
    spi_cs_assert();
    uint8_t r1 = sd_cmd(0, 0, 0x95);
    spi_xchg(0xFF);
    spi_cs_release();
    if (r1 != 0x01) return -1;                   /* expected idle */

    /* 3) CMD8: SEND_IF_COND, 0x1AA, CRC = 0x87 */
    spi_cs_assert();
    r1 = sd_cmd(8, 0x1AA, 0x87);
    uint8_t r7[4];
    for (int i = 0; i < 4; i++) r7[i] = spi_xchg(0xFF);
    spi_xchg(0xFF);
    spi_cs_release();

    int v2_card = (r1 == 0x01) && (r7[2] == 0x01) && (r7[3] == 0xAA);

    /* 4) Loop ACMD41 (CMD55 + CMD41) until idle cleared. HCS=1 for v2. */
    uint32_t acmd41_arg = v2_card ? 0x40000000UL : 0;
    for (int tries = 0; tries < 2000; tries++) {
        spi_cs_assert();
        sd_cmd(55, 0, 0x65);                     /* APP_CMD */
        r1 = sd_cmd(41, acmd41_arg, 0x77);
        spi_xchg(0xFF);
        spi_cs_release();
        if (r1 == 0x00) break;
        if (r1 & 0xFE)  return -2;               /* any error bit */
    }
    if (r1 != 0x00) return -3;

    /* 5) CMD58 READ_OCR — check CCS for SDHC/SDXC */
    if (v2_card) {
        spi_cs_assert();
        r1 = sd_cmd(58, 0, 0xFD);
        uint8_t ocr[4];
        for (int i = 0; i < 4; i++) ocr[i] = spi_xchg(0xFF);
        spi_xchg(0xFF);
        spi_cs_release();
        if (r1 == 0x00 && (ocr[0] & 0x40)) sd_is_hc = 1;  /* CCS=1 */
    }

    /* 6) For non-HC cards force 512-byte blocks */
    if (!sd_is_hc) {
        spi_cs_assert();
        r1 = sd_cmd(16, 512, 0x15);
        spi_xchg(0xFF);
        spi_cs_release();
        if (r1 != 0x00) return -4;
    }

    /* 7) Switch to fast clock: PRSC=0 (/2), CDIV=0 → 25 MHz */
    spi_setup(0, 0);
    return 0;
}

int sd_read_block(uint32_t lba, uint8_t *dst)
{
    /* SDHC addresses in blocks; SDSC in bytes */
    uint32_t addr = sd_is_hc ? lba : (lba * 512);

    spi_cs_assert();
    uint8_t r1 = sd_cmd(17, addr, 0xFF);         /* CMD17: READ_SINGLE_BLOCK */
    if (r1 != 0x00) { spi_cs_release(); return -1; }

    /* Wait for data token 0xFE (may take tens of ms) */
    uint8_t tok = 0xFF;
    for (int i = 0; i < 65536; i++) {
        tok = spi_xchg(0xFF);
        if (tok == 0xFE) break;
    }
    if (tok != 0xFE) { spi_cs_release(); return -2; }

    for (int i = 0; i < 512; i++) dst[i] = spi_xchg(0xFF);
    spi_xchg(0xFF);                              /* discard CRC hi */
    spi_xchg(0xFF);                              /* discard CRC lo */
    spi_xchg(0xFF);
    spi_cs_release();
    return 0;
}

int sd_is_sdhc(void) { return sd_is_hc; }

int sd_write_block(uint32_t lba, const uint8_t *src)
{
    uint32_t addr = sd_is_hc ? lba : (lba * 512);

    spi_cs_assert();
    uint8_t r1 = sd_cmd(24, addr, 0xFF);         /* CMD24: WRITE_BLOCK */
    if (r1 != 0x00) { spi_cs_release(); return -1; }

    spi_xchg(0xFF);                              /* 1-byte gap */
    spi_xchg(0xFE);                              /* data start token */
    for (int i = 0; i < 512; i++) spi_xchg(src[i]);
    spi_xchg(0xFF); spi_xchg(0xFF);              /* dummy CRC */

    /* Data response: xxx0sss1 where sss=010 means accepted */
    uint8_t resp = spi_xchg(0xFF);
    if ((resp & 0x1F) != 0x05) { spi_cs_release(); return -2; }

    /* Card busy: holds MISO low until write done */
    for (int i = 0; i < 65536; i++) {
        if (spi_xchg(0xFF) != 0x00) goto done;
    }
    spi_cs_release();
    return -3;
done:
    spi_cs_release();
    spi_xchg(0xFF);
    return 0;
}

/* ── Smoke test ────────────────────────────────────────────────────────── */

static uint8_t smoke_buf[512];

void sd_smoke(void)
{
    uart_puts_ext("[sd] init...\r\n");
    int rc = sd_init();
    if (rc) {
        uart_puts_ext("[sd] init FAIL rc=");
        uart_puthex32_ext((uint32_t)(int32_t)rc);
        uart_puts_ext("\r\n");
        return;
    }
    uart_puts_ext("[sd] init OK, SDHC=");
    uart_putc_ext(sd_is_hc ? '1' : '0');
    uart_puts_ext("\r\n");

    rc = sd_read_block(0, smoke_buf);
    if (rc) {
        uart_puts_ext("[sd] read LBA0 FAIL rc=");
        uart_puthex32_ext((uint32_t)(int32_t)rc);
        uart_puts_ext("\r\n");
        return;
    }

    uart_puts_ext("[sd] LBA0 first 32 bytes:\r\n  ");
    const char hex[] = "0123456789abcdef";
    for (int i = 0; i < 32; i++) {
        uart_putc_ext(hex[smoke_buf[i] >> 4]);
        uart_putc_ext(hex[smoke_buf[i] & 0xF]);
        uart_putc_ext(' ');
    }
    /* Content check: riscv_tpu_demo writes raw magic at offset 0.
     * '1UPT' = 0x54505531 (MLP) or '1NNC' = 0x434e4e31 (CNN). */
    uint32_t m = (uint32_t)smoke_buf[0]
               | ((uint32_t)smoke_buf[1] <<  8)
               | ((uint32_t)smoke_buf[2] << 16)
               | ((uint32_t)smoke_buf[3] << 24);
    uart_puts_ext("\r\n[sd] magic=");
    uart_puthex32_ext(m);
    if (m == 0x54505531UL)      uart_puts_ext(" (1UPT/MLP) OK\r\n");
    else if (m == 0x434e4e31UL) uart_puts_ext(" (1NNC/CNN) OK\r\n");
    else if (m == 0xAA550000UL || smoke_buf[510] == 0x55) uart_puts_ext(" (MBR)\r\n");
    else                        uart_puts_ext(" (unknown)\r\n");
}

/* ── UART raw helpers (private) ────────────────────────────────────────── */
/* Write raw byte via UART0 DATA register (polling). Avoids dep on HAL. */
#define UART0_CTRL (*(volatile uint32_t *)0xFFF50000UL)
#define UART0_DATA (*(volatile uint32_t *)0xFFF50004UL)
#define UART_CTRL_TX_NFULL (1u << 19)
#define UART_CTRL_RX_NEMPTY (1u << 16)  /* bit 16 set when RX has data */
static void uart_raw_byte(uint8_t b)
{
    while (!(UART0_CTRL & UART_CTRL_TX_NFULL)) { }
    UART0_DATA = (uint32_t)b;
}
static uint8_t uart_raw_recv(void)
{
    while (!(UART0_CTRL & UART_CTRL_RX_NEMPTY)) { }
    return (uint8_t)(UART0_DATA & 0xFF);
}

void sd_dump(uint32_t n_blocks)
{
    if (sd_init()) {
        uart_puts_ext("[sd] dump: init FAIL\r\n");
        return;
    }
    /* Marker: host reads ASCII until DUMP_BEGIN\n, then exactly
     * n_blocks*512 raw bytes, then \nDUMP_END\n. */
    uart_puts_ext("DUMP_BEGIN\n");
    for (uint32_t lba = 0; lba < n_blocks; lba++) {
        if (sd_read_block(lba, smoke_buf)) {
            uart_puts_ext("\nDUMP_ERR\n");
            return;
        }
        for (int i = 0; i < 512; i++)
            uart_raw_byte(smoke_buf[i]);
    }
    uart_puts_ext("\nDUMP_END\n");
}

/* ── Write round-trip test ────────────────────────────────────────────────
 * Host protocol:
 *   1. We print "SEND_512\n"
 *   2. Host sends exactly 512 raw bytes
 *   3. We write to LBA 2048 (safe offset, 1 MB in)
 *   4. We read LBA 2048 back and byte-compare
 *   5. Print "WRITE_OK\n" or "WRITE_FAIL rc=<n>\n" or "COMPARE_FAIL\n"
 */
#define TEST_LBA 2048u
static uint8_t write_rx[512];
static uint8_t write_vf[512];

void sd_write_test(void)
{
    if (sd_init()) { uart_puts_ext("[sd] wr: init FAIL\r\n"); return; }

    uart_puts_ext("SEND_512\n");
    for (int i = 0; i < 512; i++) write_rx[i] = uart_raw_recv();

    int rc = sd_write_block(TEST_LBA, write_rx);
    if (rc) {
        uart_puts_ext("WRITE_FAIL rc=");
        uart_puthex32_ext((uint32_t)(int32_t)rc);
        uart_puts_ext("\n");
        return;
    }

    rc = sd_read_block(TEST_LBA, write_vf);
    if (rc) {
        uart_puts_ext("READBACK_FAIL rc=");
        uart_puthex32_ext((uint32_t)(int32_t)rc);
        uart_puts_ext("\n");
        return;
    }

    for (int i = 0; i < 512; i++) {
        if (write_rx[i] != write_vf[i]) {
            uart_puts_ext("COMPARE_FAIL @");
            uart_puthex32_ext((uint32_t)i);
            uart_puts_ext("\n");
            return;
        }
    }
    uart_puts_ext("WRITE_OK\n");
}

/* ── Multi-block write: host streams N sectors, we write LBA 0..N-1 ──────
 * Protocol:
 *   stage2: "MW_READY\n"
 *   host:   4 bytes little-endian N (sector count)
 *   stage2: "MW_GO\n"
 *   host:   N*512 raw bytes
 *   stage2: "MW_DONE\n" or "MW_FAIL lba=<x> rc=<r>\n"
 */
void sd_write_multi(void)
{
    if (sd_init()) { uart_puts_ext("[sd] mw: init FAIL\r\n"); return; }

    uart_puts_ext("MW_READY\n");

    uint32_t n = 0;
    for (int i = 0; i < 4; i++)
        n |= ((uint32_t)uart_raw_recv()) << (8 * i);

    if (n == 0 || n > 8192u) {           /* cap at 4 MB */
        uart_puts_ext("MW_FAIL bad_n\n");
        return;
    }

    uart_puts_ext("MW_GO\n");

    /* Per-block ACK: host must wait for 'K' after each block before
     * sending the next 512 B. Prevents RX-FIFO overrun during SD writes. */
    for (uint32_t lba = 0; lba < n; lba++) {
        for (int i = 0; i < 512; i++) write_rx[i] = uart_raw_recv();
        int rc = sd_write_block(lba, write_rx);
        if (rc) {
            uart_raw_byte('X');
            uart_puts_ext("\nMW_FAIL lba=");
            uart_puthex32_ext(lba);
            uart_puts_ext(" rc=");
            uart_puthex32_ext((uint32_t)(int32_t)rc);
            uart_puts_ext("\n");
            return;
        }
        uart_raw_byte('K');
    }
    uart_puts_ext("\nMW_DONE\n");
}

/* ── Multi-block read into contiguous memory ──────────────────────────── */
int sd_read_many(uint32_t lba, uint32_t n_blocks, uint8_t *dst)
{
    for (uint32_t i = 0; i < n_blocks; i++) {
        int rc = sd_read_block(lba + i, dst + i * 512);
        if (rc) return rc;
    }
    return 0;
}
