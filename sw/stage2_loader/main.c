// SPDX-License-Identifier: GPL-2.0+
/*
 * stage2_loader — NEORV32 multi-payload xmodem loader
 *
 * Supports two modes:
 *   Mode 'u': Load U-Boot to SDRAM, CRC verify, jump (original behavior)
 *   Mode 'l': Load Linux kernel + DTB + initramfs, jump to kernel
 *
 * Host sends mode byte ('u' or 'l') after stage2 prints its ready prompt.
 * If no mode byte within 3s, defaults to 'u' (backward compatible).
 */

#include <neorv32.h>
#include <stdint.h>
#include "sd.h"

#define UBOOT_LOAD_ADDR   0x40000000UL
#define KERNEL_LOAD_ADDR  0x40000000UL
#define DTB_LOAD_ADDR     0x41F00000UL
#define INITRD_LOAD_ADDR  0x41F80000UL
#define UART_BAUD         115200

#define SOH   0x01
#define EOT   0x04
#define ACK   0x06
#define NAK   0x15
#define CAN   0x18

#define XMODEM_BLOCK_SIZE  128
#define XMODEM_TIMEOUT_MS  3000
#define XMODEM_RETRY_MAX   20

/* CRC-32 (IEEE 802.3) for post-transfer integrity check */
static uint32_t crc32(const uint8_t *data, uint32_t len)
{
    uint32_t crc = 0xFFFFFFFF;
    for (uint32_t i = 0; i < len; i++) {
        crc ^= data[i];
        for (int j = 0; j < 8; j++)
            crc = (crc >> 1) ^ (0xEDB88320 & -(crc & 1));
    }
    return ~crc;
}

static void uart_putc(char c)  { neorv32_uart0_putc(c); }
static void uart_puts(const char *s) { while (*s) uart_putc(*s++); }

/* Non-static trampolines so sd.c can reuse without bloating its own strings. */
void uart_putc_ext(char c)       { uart_putc(c); }
void uart_puts_ext(const char *s){ uart_puts(s); }
void uart_puthex32_ext(uint32_t v);

static void uart_puthex32(uint32_t v)
{
    const char hex[] = "0123456789abcdef";
    uart_puts("0x");
    for (int i = 28; i >= 0; i -= 4)
        uart_putc(hex[(v >> i) & 0xF]);
}

void uart_puthex32_ext(uint32_t v) { uart_puthex32(v); }

/* Diagnostic trap handler — prints mcause/mepc/mtval via UART.
 * Lives in IMEM, set as mtvec before jumping to kernel.
 * If the kernel traps before setting its own mtvec, we catch it. */
extern void _diag_trap_handler(void);

__asm__ (
    ".section .text\n"
    ".align 4\n"
    ".global _diag_trap_handler\n"
    "_diag_trap_handler:\n"
    /* Use DMEM top as emergency stack */
    "  li sp, 0x80002000\n"
    "  j diag_trap_handler_c\n"
);

void __attribute__((noreturn)) diag_trap_handler_c(void)
{
    uint32_t mcause, mepc, mtval;
    __asm__ volatile ("csrr %0, mcause" : "=r"(mcause));
    __asm__ volatile ("csrr %0, mepc"   : "=r"(mepc));
    __asm__ volatile ("csrr %0, mtval"  : "=r"(mtval));

    uart_puts("\r\n!TRAP mcause=");
    uart_puthex32(mcause);
    uart_puts(" mepc=");
    uart_puthex32(mepc);
    uart_puts(" mtval=");
    uart_puthex32(mtval);
    uart_puts("\r\n");
    while (1) {}
}

static int uart_getc_timeout(uint32_t timeout_ms)
{
    uint32_t cycles_per_ms = neorv32_sysinfo_get_clk() / 1000;
    uint64_t deadline = neorv32_cpu_get_cycle()
                      + (uint64_t)timeout_ms * cycles_per_ms;
    while (neorv32_cpu_get_cycle() < deadline) {
        if (neorv32_uart0_char_received())
            return (int)(uint8_t)neorv32_uart0_char_received_get();
    }
    return -1;
}

/* Simple SDRAM test: write pattern, read back (use high SDRAM to avoid kernel area) */
static int sdram_test(void)
{
    volatile uint32_t *base = (volatile uint32_t *)0x41F00000UL;
    uint32_t n = 64;

    uart_puts("[stage2] SDRAM word-write test...\r\n");
    for (uint32_t i = 0; i < n; i++)
        base[i] = 0xDEAD0000 | i;

    uint32_t errors = 0;
    for (uint32_t i = 0; i < n; i++) {
        uint32_t got = base[i];
        uint32_t exp = 0xDEAD0000 | i;
        if (got != exp) {
            uart_puts("  FAIL["); uart_puthex32(i);
            uart_puts("]: exp="); uart_puthex32(exp);
            uart_puts(" got="); uart_puthex32(got);
            uart_puts("\r\n");
            errors++;
            if (errors > 4) break;
        }
    }
    if (errors == 0) {
        uart_puts("[stage2] SDRAM test PASS\r\n");
        return 1;
    }
    return 0;
}

static uint32_t xmodem_receive(uint8_t *dest)
{
    uint8_t blk_num = 1;
    uint32_t total  = 0;
    int retries     = 0;

    uart_putc(NAK);

    while (1) {
        int c = uart_getc_timeout(XMODEM_TIMEOUT_MS);

        if (c < 0) {
            retries++;
            if (retries > XMODEM_RETRY_MAX) {
                uart_putc(CAN); uart_putc(CAN);
                uart_puts("\r\n[!] xmodem timeout\r\n");
                return 0;
            }
            uart_puts("\r\n[stage2] waiting for sender (NAK)...\r\n");
            uart_putc(NAK);
            continue;
        }

        if (c == EOT) { uart_putc(ACK); return total; }
        if (c == CAN) { uart_puts("\r\n[!] xmodem cancelled\r\n"); return 0; }
        if (c != SOH) continue;

        int bn  = uart_getc_timeout(1000);
        int bnc = uart_getc_timeout(1000);
        if (bn < 0 || bnc < 0) { uart_putc(NAK); continue; }

        uint8_t buf[XMODEM_BLOCK_SIZE];
        uint8_t csum = 0;
        int ok = 1;
        for (int i = 0; i < XMODEM_BLOCK_SIZE; i++) {
            int b = uart_getc_timeout(1000);
            if (b < 0) { ok = 0; break; }
            buf[i] = (uint8_t)b;
            csum  += (uint8_t)b;
        }
        if (!ok) { uart_putc(NAK); continue; }

        int recv_csum = uart_getc_timeout(1000);
        if (recv_csum < 0) { uart_putc(NAK); continue; }

        if ((uint8_t)bn != blk_num || (uint8_t)bnc != (uint8_t)(~blk_num) ||
            (uint8_t)recv_csum != csum) {
            retries++;
            uart_putc(NAK);
            continue;
        }

        retries = 0;
        for (int i = 0; i < XMODEM_BLOCK_SIZE; i++)
            dest[total + i] = buf[i];
        total += XMODEM_BLOCK_SIZE;
        blk_num++;
        uart_putc(ACK);
    }
}

/* Receive via xmodem with CRC-32 verification */
static uint32_t xmodem_receive_verified(uint8_t *dest, const char *name)
{
    uart_puts("[stage2] send "); uart_puts(name);
    uart_puts(" via xmodem\r\n");

    uint32_t size = xmodem_receive(dest);
    if (size == 0) {
        uart_puts("[!] "); uart_puts(name); uart_puts(" xmodem FAILED\r\n");
        return 0;
    }

    uint32_t crc = crc32(dest, size);
    uart_puts("[stage2] "); uart_puts(name);
    uart_puts(" size="); uart_puthex32(size);
    uart_puts(" CRC:"); uart_puthex32(crc);
    uart_puts("\r\n");

    int resp = uart_getc_timeout(5000);
    if (resp == NAK) {
        uart_puts("[!] CRC mismatch for "); uart_puts(name);
        uart_puts(" — halting\r\n");
        return 0;
    }
    /* ACK or no response: proceed */
    return size;
}

/* Dump first N words for visual verification */
static void dump_words(uint32_t addr, int n)
{
    volatile uint32_t *p = (volatile uint32_t *)addr;
    for (int i = 0; i < n; i++) {
        if (i % 4 == 0) {
            uart_puts("\r\n  ");
            uart_puthex32(addr + i * 4);
            uart_puts(": ");
        }
        uart_puthex32(p[i]);
        uart_putc(' ');
    }
    uart_puts("\r\n");
}

/* Mode 'u': Load U-Boot (original behavior) */
static void mode_uboot(void)
{
    uart_puts("[stage2] Mode: U-Boot loader\r\n");
    uart_puts("[stage2] send U-Boot via xmodem at 115200 baud\r\n");

    uint8_t *dest = (uint8_t *)UBOOT_LOAD_ADDR;
    uint32_t size = xmodem_receive(dest);

    if (size == 0) {
        uart_puts("[stage2] xmodem FAILED\r\n");
        while (1) {}
    }

    uint32_t crc = crc32(dest, size);
    uart_puts("[stage2] size="); uart_puthex32(size);
    uart_puts(" CRC:"); uart_puthex32(crc);
    uart_puts("\r\n");

    int crc_resp = uart_getc_timeout(5000);
    if (crc_resp == NAK) {
        uart_puts("[stage2] CRC MISMATCH — retrying xmodem...\r\n");
        size = xmodem_receive(dest);
        if (size == 0) {
            uart_puts("[stage2] xmodem retry FAILED\r\n");
            while (1) {}
        }
        crc = crc32(dest, size);
        uart_puts("[stage2] retry size="); uart_puthex32(size);
        uart_puts(" CRC:"); uart_puthex32(crc);
        uart_puts("\r\n");
        crc_resp = uart_getc_timeout(5000);
        if (crc_resp != ACK) {
            uart_puts("[stage2] CRC still bad — halting\r\n");
            while (1) {}
        }
    } else if (crc_resp != ACK) {
        uart_puts("[stage2] no CRC response, proceeding anyway\r\n");
    }

    dump_words(UBOOT_LOAD_ADDR, 16);

    uart_puts("[stage2] jumping to SDRAM 0x40000000...\r\n");
    __asm__ volatile ("fence.i" ::: "memory");

    void (*uboot)(void) = (void (*)(void))UBOOT_LOAD_ADDR;
    uboot();
}

/* Mode 'b': Load Linux from SD card blob (header @ LBA 0) */
struct sd_boot_hdr {
    char     magic[8];      /* "NEOLNX\0\0" */
    uint32_t image_sz;
    uint32_t dtb_sz;
    uint32_t initrd_sz;
    uint32_t image_lba;     /* usually 1 */
    uint32_t dtb_lba;
    uint32_t initrd_lba;
    uint32_t reserved[4];
};

static void __attribute__((noreturn)) jump_to_kernel(void)
{
    __asm__ volatile ("fence.i" ::: "memory");
    __asm__ volatile (
        "li a0, 0\n"
        "li a1, %0\n"
        "li t0, %1\n"
        "jr t0\n"
        :
        : "i"(DTB_LOAD_ADDR), "i"(KERNEL_LOAD_ADDR)
        : "a0", "a1", "t0"
    );
    __builtin_unreachable();
}

static void mode_sd_boot(void)
{
    uart_puts("[stage2] Mode: SD blob boot\r\n");

    /* Init SD and read header at LBA 0 */
    extern int sd_init(void);
    extern int sd_read_block(uint32_t lba, uint8_t *dst);
    extern int sd_read_many(uint32_t lba, uint32_t n, uint8_t *dst);

    if (sd_init()) { uart_puts("[sd] init FAIL\r\n"); while (1) {} }

    static uint8_t hdr_buf[512];
    if (sd_read_block(0, hdr_buf)) {
        uart_puts("[sd] read hdr FAIL\r\n"); while (1) {}
    }

    /* Verify magic "NEOLNX\0\0" */
    const char magic[8] = { 'N','E','O','L','N','X','\0','\0' };
    for (int i = 0; i < 8; i++) {
        if (hdr_buf[i] != (uint8_t)magic[i]) {
            uart_puts("[sd] bad magic\r\n"); while (1) {}
        }
    }

    struct sd_boot_hdr *h = (struct sd_boot_hdr *)hdr_buf;
    uart_puts("[sd] image_sz="); uart_puthex32(h->image_sz);
    uart_puts(" dtb_sz=");       uart_puthex32(h->dtb_sz);
    uart_puts(" initrd_sz=");    uart_puthex32(h->initrd_sz);
    uart_puts("\r\n");

    uint32_t img_blks = (h->image_sz  + 511) / 512;
    uint32_t dtb_blks = (h->dtb_sz    + 511) / 512;
    uint32_t ird_blks = (h->initrd_sz + 511) / 512;

    uart_puts("[sd] reading Image\r\n");
    if (sd_read_many(h->image_lba, img_blks, (uint8_t *)KERNEL_LOAD_ADDR)) {
        uart_puts("[sd] img read FAIL\r\n"); while (1) {}
    }
    uart_puts("[sd] reading DTB\r\n");
    if (sd_read_many(h->dtb_lba, dtb_blks, (uint8_t *)DTB_LOAD_ADDR)) {
        uart_puts("[sd] dtb read FAIL\r\n"); while (1) {}
    }
    uart_puts("[sd] reading initrd\r\n");
    if (sd_read_many(h->initrd_lba, ird_blks, (uint8_t *)INITRD_LOAD_ADDR)) {
        uart_puts("[sd] initrd read FAIL\r\n"); while (1) {}
    }

    /* DTB fixup: patch chosen/linux,initrd-end sentinel 0xC0DEDEAD with
     * the real end address (start + initrd_sz). FDT cells are big-endian. */
    {
        uint32_t real_end = INITRD_LOAD_ADDR + h->initrd_sz;
        uint8_t *dtb = (uint8_t *)DTB_LOAD_ADDR;
        uint32_t dtb_len = dtb_blks * 512;
        /* Sentinel in big-endian byte order */
        const uint8_t sent[4] = { 0xC0, 0xDE, 0xDE, 0xAD };
        int patched = 0;
        for (uint32_t i = 0; i + 4 <= dtb_len; i++) {
            if (dtb[i]   == sent[0] && dtb[i+1] == sent[1] &&
                dtb[i+2] == sent[2] && dtb[i+3] == sent[3]) {
                dtb[i+0] = (real_end >> 24) & 0xFF;
                dtb[i+1] = (real_end >> 16) & 0xFF;
                dtb[i+2] = (real_end >>  8) & 0xFF;
                dtb[i+3] = (real_end      ) & 0xFF;
                patched++;
            }
        }
        uart_puts("[sd] initrd-end=");
        uart_puthex32(real_end);
        uart_puts(" patched=");
        uart_puthex32((uint32_t)patched);
        uart_puts("\r\n");
        if (patched != 1) {
            uart_puts("[!] DTB sentinel not found — halting\r\n");
            while (1) {}
        }
    }

    uart_puts("[stage2] Kernel header:\r\n");
    dump_words(KERNEL_LOAD_ADDR, 4);
    uart_puts("[stage2] Booting Linux from SD...\r\n");
    jump_to_kernel();
}

/* Mode 'l': Load Linux kernel + DTB + initramfs, boot directly */
static void mode_linux(void)
{
    uart_puts("[stage2] Mode: Linux direct boot\r\n");
    uart_puts("[stage2] Memory layout:\r\n");
    uart_puts("  Kernel:    "); uart_puthex32(KERNEL_LOAD_ADDR); uart_puts("\r\n");
    uart_puts("  DTB:       "); uart_puthex32(DTB_LOAD_ADDR); uart_puts("\r\n");
    uart_puts("  Initramfs: "); uart_puthex32(INITRD_LOAD_ADDR); uart_puts("\r\n");

    /* Step 1: Receive kernel */
    uint32_t kernel_size = xmodem_receive_verified(
        (uint8_t *)KERNEL_LOAD_ADDR, "kernel");
    if (kernel_size == 0) { while (1) {} }

    /* Step 2: Receive DTB */
    uint32_t dtb_size = xmodem_receive_verified(
        (uint8_t *)DTB_LOAD_ADDR, "DTB");
    if (dtb_size == 0) { while (1) {} }

    /* Step 3: Receive initramfs */
    uint32_t initrd_size = xmodem_receive_verified(
        (uint8_t *)INITRD_LOAD_ADDR, "initramfs");
    if (initrd_size == 0) { while (1) {} }

    /* Patch DTB chosen node with initrd-start/end
     * For simplicity, we don't patch the DTB here — the kernel
     * bootargs already has the earlycon and console settings.
     * initramfs is passed via a0/a1 convention or embedded.
     *
     * Actually, Linux needs initrd info in DTB chosen node.
     * We'll embed initramfs in kernel instead (CONFIG_INITRAMFS_SOURCE).
     * For now, just load all three and see what happens.
     */

    uart_puts("\r\n[stage2] All payloads loaded:\r\n");
    uart_puts("  Kernel:  "); uart_puthex32(kernel_size); uart_puts(" bytes\r\n");
    uart_puts("  DTB:     "); uart_puthex32(dtb_size); uart_puts(" bytes\r\n");
    uart_puts("  Initrd:  "); uart_puthex32(initrd_size); uart_puts(" bytes\r\n");

    uart_puts("\r\n[stage2] Kernel header:\r\n");
    dump_words(KERNEL_LOAD_ADDR, 8);

    uart_puts("[stage2] DTB header:\r\n");
    dump_words(DTB_LOAD_ADDR, 4);

    uart_puts("[stage2] Booting Linux...\r\n");
    uart_puts("  a0 = 0 (hartid)\r\n");
    uart_puts("  a1 = "); uart_puthex32(DTB_LOAD_ADDR); uart_puts(" (DTB)\r\n");
    uart_puts("  PC = "); uart_puthex32(KERNEL_LOAD_ADDR); uart_puts(" (kernel)\r\n");

    /* Write a small SDRAM test routine AFTER the kernel, to verify
     * code execution from SDRAM works before jumping to kernel.
     * Place it at kernel_end (rounded up to 4-byte boundary). */
    {
        uint32_t test_addr = (KERNEL_LOAD_ADDR + kernel_size + 3) & ~3;
        volatile uint32_t *code = (volatile uint32_t *)test_addr;

        uart_puts("[stage2] SDRAM exec test @");
        uart_puthex32(test_addr);
        uart_puts("\r\n");

        /* Write tiny RV32 program:
         *   lui t1, 0xFFF50      # t1 = 0xFFF50000 (UART base)
         *   li  t2, 'K'          # t2 = 0x4B
         *   sw  t2, 4(t1)        # write to DATA register
         *   ret                  # return to caller (ra) */
        code[0] = 0xFFF50337;    /* lui t1, 0xFFF50 */
        code[1] = 0x04B00393;    /* li  t2, 0x4B ('K') */
        code[2] = 0x00732223;    /* sw  t2, 4(t1) */
        code[3] = 0x00008067;    /* ret (jalr x0, ra, 0) */

        __asm__ volatile ("fence.i" ::: "memory");

        /* Call the test routine */
        void (*test_fn)(void) = (void (*)(void))test_addr;
        test_fn();

        /* If we see 'K', SDRAM code execution works */
        uart_puts(" ← SDRAM exec OK\r\n");

        /* Wait for TX to flush */
        volatile uint32_t *uart_ctrl = (volatile uint32_t *)0xFFF50000UL;
        while (!(uart_ctrl[0] & (1 << 18))) {}
    }

    __asm__ volatile ("fence.i" ::: "memory");

    /* Jump to kernel: a0=hartid(0), a1=DTB address */
    __asm__ volatile (
        "li a0, 0\n"
        "li a1, %0\n"
        "li t0, %1\n"
        "jr t0\n"
        :
        : "i"(DTB_LOAD_ADDR), "i"(KERNEL_LOAD_ADDR)
        : "a0", "a1", "t0"
    );

    __builtin_unreachable();
}

int main(void)
{
    neorv32_uart0_setup(UART_BAUD, 0);

    uart_puts("\r\n[stage2] ready - RV32IMAC NEORV32 loader\r\n");
    uart_puts("[stage2] CLK="); uart_puthex32(neorv32_sysinfo_get_clk());
    uart_puts("\r\n");

    /* Verify SDRAM works before anything */
    if (!sdram_test()) {
        uart_puts("[stage2] SDRAM FAIL - halting\r\n");
        while (1) {}
    }

    /* Wait for mode selection: 'l'=Linux xmodem, 's'=SD smoke test, else U-Boot */
    uart_puts("[stage2] l=xmodem s=smoke d=dump256K w=wtest W=wmulti b=bootSD else=U-Boot\r\n");
    int mode = uart_getc_timeout(3000);

    if (mode == 'l') {
        mode_linux();
    } else if (mode == 's') {
        sd_smoke();
        uart_puts("[stage2] smoke done, halting\r\n");
        while (1) { }
    } else if (mode == 'd') {
        sd_dump(512);
        while (1) { }
    } else if (mode == 'w') {
        sd_write_test();
        while (1) { }
    } else if (mode == 'W') {
        sd_write_multi();
        while (1) { }
    } else if (mode == 'b') {
        mode_sd_boot();
    } else {
        mode_uboot();
    }

    return 0;
}
