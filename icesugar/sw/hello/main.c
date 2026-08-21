/* ================================================================================ *
 * NEORV32 on iCESugar (iCE40UP5K-SG48) - bare-metal bring-up demo                  *
 * -------------------------------------------------------------------------------- *
 * Proves the port end to end:                                                       *
 *   - UART0 talks to the host through the iCELink USB-CDC bridge                    *
 *   - IMEM / DMEM really live in the four SB_SPRAM256KA blocks (walking RAM test)   *
 *   - the RGB LED is reachable via GPIO[2:0] -> SB_RGBA_DRV                         *
 *   - CLINT machine timer runs at the expected 12 MHz                               *
 * -------------------------------------------------------------------------------- *
 * Part of see_neorv32_run_linux.  NEORV32 (c) Stephan Nolting, BSD-3-Clause.        *
 * ================================================================================ */

#include <neorv32.h>

/* the bootloader hands us over at 19200 baud; keep the same rate */
#define BAUD_RATE 19200

/* RGB LED bits on the GPIO output port (see rtl/icesugar_top.v) */
#define LED_R 0
#define LED_G 1
#define LED_B 2


/* crude busy-wait based on the cycle counter */
static void delay_ms(uint32_t ms) {
  uint32_t ticks = (neorv32_sysinfo_get_clk() / 1000) * ms;
  uint32_t t0 = neorv32_cpu_csr_read(CSR_MCYCLE);
  while ((neorv32_cpu_csr_read(CSR_MCYCLE) - t0) < ticks);
}


/* walking-1 / walking-0 test over a DMEM window; catches a broken SPRAM
   byte-enable or a mis-wired MASKWREN nibble mapping */
static int spram_test(void) {
  /* a union gives us well-defined byte / half-word / word views of the same
     storage - a plain pointer cast would break strict aliasing */
  static volatile union {
    uint32_t w[256];
    uint16_t h[512];
    uint8_t  b[1024];
  } mem;

  uint32_t i, pat;
  int errors = 0;

  /* walking-1 over all 32 bits */
  for (i = 0; i < 32; i++) {
    pat = 1u << i;
    mem.w[i] = pat;
    if (mem.w[i] != pat) errors++;
  }

  /* byte enables: overwrite one byte at a time and check the rest survives.
     This is the test that catches a wrong SB_SPRAM256KA MASKWREN mapping. */
  mem.w[64] = 0xAABBCCDDu;
  mem.b[64*4 + 0] = 0x11;
  if (mem.w[64] != 0xAABBCC11u) errors++;
  mem.b[64*4 + 1] = 0x22;
  if (mem.w[64] != 0xAABB2211u) errors++;
  mem.b[64*4 + 2] = 0x33;
  if (mem.w[64] != 0xAA332211u) errors++;
  mem.b[64*4 + 3] = 0x44;
  if (mem.w[64] != 0x44332211u) errors++;

  /* half-word enables (two MASKWREN nibbles at a time) */
  mem.w[65] = 0x00000000u;
  mem.h[65*2 + 0] = 0x1234;
  mem.h[65*2 + 1] = 0x5678;
  if (mem.w[65] != 0x56781234u) errors++;

  /* address uniqueness across the window (catches stuck address bits) */
  for (i = 0; i < 256; i++) mem.w[i] = i * 0x01010101u;
  for (i = 0; i < 256; i++) {
    if (mem.w[i] != i * 0x01010101u) errors++;
  }

  return errors;
}


static void print_info(void) {
  uint32_t clk = neorv32_sysinfo_get_clk();

  neorv32_uart0_printf("\n");
  neorv32_uart0_printf("+----------------------------------------------+\n");
  neorv32_uart0_printf("|  NEORV32 on iCESugar (iCE40UP5K-SG48)        |\n");
  neorv32_uart0_printf("+----------------------------------------------+\n");
  neorv32_uart0_printf("Clock     : %u Hz\n", clk);
  neorv32_uart0_printf("HART ID   : %u\n", neorv32_cpu_csr_read(CSR_MHARTID));
  neorv32_uart0_printf("MISA      : 0x%x\n", neorv32_cpu_csr_read(CSR_MISA));
  neorv32_uart0_printf("MXISA     : 0x%x\n", neorv32_cpu_csr_read(CSR_MXISA));
  neorv32_uart0_printf("IMEM      : %u bytes (SPRAM)\n", neorv32_sysinfo_get_imemsize());
  neorv32_uart0_printf("DMEM      : %u bytes (SPRAM)\n", neorv32_sysinfo_get_dmemsize());
  neorv32_uart0_printf("Boot mode : %u\n", neorv32_sysinfo_get_bootmode());
}


static void print_help(void) {
  neorv32_uart0_printf("\nCommands:\n"
                       "  i - system info\n"
                       "  m - SPRAM memory test\n"
                       "  t - CLINT timer check (1 second)\n"
                       "  r/g/b - toggle red/green/blue LED\n"
                       "  h - this help\n");
}


int main(void) {

  neorv32_rte_setup();
  neorv32_uart0_setup(BAUD_RATE, 0);

  print_info();

  /* LED sweep: proves GPIO -> SB_RGBA_DRV is alive */
  neorv32_uart0_printf("\nRGB LED sweep... ");
  for (int i = 0; i < 3; i++) {
    neorv32_gpio_port_set(1u << i);
    delay_ms(200);
  }
  neorv32_gpio_port_set(0);
  neorv32_uart0_printf("done\n");

  /* memory test */
  neorv32_uart0_printf("SPRAM test... ");
  int err = spram_test();

  /* NOTE: track the LED state in a variable.  neorv32_gpio_port_get() returns
     PORT_IN - the read-only *input* port, which is unconnected in this design
     and therefore always reads 0.  Using it to read back the output register
     silently loses the LED state and the next toggle blanks the LED. */
  uint32_t led;
  if (err == 0) {
    neorv32_uart0_printf("PASS\n");
    led = 1u << LED_G;
  }
  else {
    neorv32_uart0_printf("FAIL (%u errors)\n", err);
    led = 1u << LED_R;
  }
  neorv32_gpio_port_set(led);

  print_help();

  /* interactive loop */
  while (1) {
    neorv32_uart0_printf("\nicesugar> ");
    char c = neorv32_uart0_getc();
    neorv32_uart0_printf("%c\n", c);

    switch (c) {
      case 'i': print_info(); break;
      case 'h': print_help(); break;

      case 'm':
        err = spram_test();
        neorv32_uart0_printf("SPRAM test: %s (%u errors)\n", err ? "FAIL" : "PASS", err);
        break;

      case 't': {
        uint64_t t0 = neorv32_clint_time_get();
        delay_ms(1000);
        uint64_t dt = neorv32_clint_time_get() - t0;
        neorv32_uart0_printf("CLINT ticks in ~1 s: %u (expect ~%u)\n",
                             (uint32_t)dt, neorv32_sysinfo_get_clk());
        break;
      }

      case 'r': led ^= (1u << LED_R); neorv32_gpio_port_set(led); break;
      case 'g': led ^= (1u << LED_G); neorv32_gpio_port_set(led); break;
      case 'b': led ^= (1u << LED_B); neorv32_gpio_port_set(led); break;

      default:
        neorv32_uart0_printf("unknown command '%c' - press 'h' for help\n", c);
        break;
    }
  }

  return 0;
}
