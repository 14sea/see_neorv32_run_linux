-- ax301_top.vhd — NEORV32 SoC wrapper for 黑金 AX301 (EP4CE6F17C8)
--
-- Memory map (NEORV32 fixed):
--   0x00000000 - 0x00003FFF : IMEM  (16 KB, M9K BRAM, instruction memory)
--   0x80000000 - 0x80001FFF : DMEM  (8 KB, M9K BRAM, data memory)
--   0x40000000 - 0x41FFFFFF : SDRAM (32 MB, via XBUS Wishbone → sdram_ctrl)
--   0xFFE00000              : Boot ROM (internal, ~4 KB, NEORV32 bootloader)
--   0xFFF50000              : UART0 (internal, 115200 baud = 50MHz/434)
--   0xFFFC0000              : GPIO  (internal, gpio_o[3:0] → LED)
--
-- Boot sequence (BOOT_MODE_SELECT=0):
--   CPU resets → bootloader ROM → waits on UART for firmware upload
--   Send firmware with: neorv32/sw/image_gen/uart_upload.sh /dev/ttyUSB0 app.bin
--   Bootloader default baud: 19200

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

library neorv32;
use neorv32.neorv32_package.all;

entity ax301_top is
  port (
    CLOCK  : in    std_ulogic;                     -- 50 MHz
    KEY2   : in    std_ulogic;                     -- active-low reset button
    -- UART (PL2303 → /dev/ttyUSB0)
    RXD    : in    std_ulogic;
    TXD    : out   std_ulogic;
    -- LEDs (active-low, gpio_o[3:0])
    LED    : out   std_ulogic_vector(3 downto 0);
    -- SDRAM (HY57V2562GTR, 32 MB)
    S_CLK  : out   std_ulogic;
    S_CKE  : out   std_ulogic;
    S_NCS  : out   std_ulogic;
    S_NRAS : out   std_ulogic;
    S_NCAS : out   std_ulogic;
    S_NWE  : out   std_ulogic;
    S_BA   : out   std_ulogic_vector(1 downto 0);
    S_A    : out   std_ulogic_vector(12 downto 0);
    S_DQM  : out   std_ulogic_vector(1 downto 0);
    S_DB   : inout std_ulogic_vector(15 downto 0);
    -- SD card (SPI mode)
    SD_CLK : out   std_ulogic;
    SD_DI  : out   std_ulogic;  -- MOSI (controller → card)
    SD_DO  : in    std_ulogic;  -- MISO (card → controller)
    SD_NCS : out   std_ulogic
  );
end entity ax301_top;

architecture rtl of ax301_top is

  -- ── Internal signals ──────────────────────────────────────────────────────
  signal rstn_int    : std_ulogic;
  signal por_cnt     : std_ulogic_vector(3 downto 0) := (others => '0');
  signal gpio_out    : std_ulogic_vector(31 downto 0);

  -- XBUS (Wishbone) signals
  signal xbus_adr    : std_ulogic_vector(31 downto 0);
  signal xbus_dat_o  : std_ulogic_vector(31 downto 0);
  signal xbus_dat_i  : std_ulogic_vector(31 downto 0);
  signal xbus_we     : std_ulogic;
  signal xbus_sel    : std_ulogic_vector(3 downto 0);
  signal xbus_stb    : std_ulogic;
  signal xbus_cyc    : std_ulogic;
  signal xbus_ack    : std_ulogic;
  signal xbus_err    : std_ulogic;

  -- ── Wishbone-to-sdram_ctrl bridge (Verilog component) ────────────────────
  component wb_sdram_ctrl is
    port (
      clk       : in  std_ulogic;
      rst_n     : in  std_ulogic;
      -- XBUS (Wishbone)
      xbus_adr  : in  std_ulogic_vector(31 downto 0);
      xbus_dat_w: in  std_ulogic_vector(31 downto 0);
      xbus_sel  : in  std_ulogic_vector(3 downto 0);
      xbus_we   : in  std_ulogic;
      xbus_stb  : in  std_ulogic;
      xbus_cyc  : in  std_ulogic;
      xbus_dat_r: out std_ulogic_vector(31 downto 0);
      xbus_ack  : out std_ulogic;
      xbus_err  : out std_ulogic;
      -- SDRAM pins
      S_CLK     : out std_ulogic;
      S_CKE     : out std_ulogic;
      S_NCS     : out std_ulogic;
      S_NRAS    : out std_ulogic;
      S_NCAS    : out std_ulogic;
      S_NWE     : out std_ulogic;
      S_BA      : out std_ulogic_vector(1 downto 0);
      S_A       : out std_ulogic_vector(12 downto 0);
      S_DQM     : out std_ulogic_vector(1 downto 0);
      S_DB      : inout std_ulogic_vector(15 downto 0);
      -- Debug
      dbg_leds  : out std_ulogic_vector(3 downto 0)
    );
  end component;

  signal dbg_leds : std_ulogic_vector(3 downto 0);

begin

  -- ── Power-on reset (same pattern as riscv_demo soc_top.v) ─────────────────
  -- Hold reset for 8 cycles after KEY2 goes high.
  por: process(CLOCK)
  begin
    if rising_edge(CLOCK) then
      if KEY2 = '0' then
        por_cnt <= (others => '0');
      elsif por_cnt(3) = '0' then
        por_cnt <= std_ulogic_vector(unsigned(por_cnt) + 1);
      end if;
    end if;
  end process;
  rstn_int <= KEY2 and por_cnt(3);

  -- ── NEORV32 processor ─────────────────────────────────────────────────────
  neorv32_top_inst: neorv32_top
  generic map (
    -- Clocking
    CLOCK_FREQUENCY  => 50_000_000,
    -- Boot: 0 = internal UART bootloader (at 0xFFE00000)
    BOOT_MODE_SELECT => 0,
    -- ISA: RV32IMAC + base counters (Zaamo needed by U-Boot SMP init path)
    RISCV_ISA_C      => true,
    RISCV_ISA_M      => true,
    RISCV_ISA_U      => true,   -- U-mode needed for nommu Linux userspace (ecall from U-mode)
    RISCV_ISA_Zaamo  => true,
    RISCV_ISA_Zalrsc => true,
    RISCV_ISA_Zicntr => true,
    -- Internal memories
    IMEM_EN          => true,
    IMEM_SIZE        => 8*1024,   -- reduced from 16K to make room for ICACHE
    DMEM_EN          => true,
    DMEM_SIZE        => 8*1024,
    -- External bus (Wishbone → SDRAM)
    XBUS_EN          => true,
    XBUS_TIMEOUT     => 4096,
    XBUS_REGSTAGE_EN => false,
    -- Peripherals: only what we need
    IO_GPIO_NUM      => 4,
    IO_CLINT_EN      => true,
    IO_UART0_EN      => true,
    IO_UART0_RX_FIFO => 4,   -- 2^4 = 16-entry FIFO for Linux console
    IO_UART0_TX_FIFO => 4,   -- 2^4 = 16-entry FIFO for Linux console
    -- Everything else off
    IO_SPI_EN        => true,
    IO_SDI_EN        => false,
    IO_TWI_EN        => false,
    IO_TWD_EN        => false,
    IO_PWM_NUM       => 0,
    IO_WDT_EN        => false,
    IO_TRNG_EN       => false,
    IO_CFS_EN        => false,
    IO_NEOLED_EN     => false,
    IO_GPTMR_NUM     => 0,
    IO_ONEWIRE_EN    => false,
    IO_DMA_EN        => false,
    IO_SLINK_EN      => false,
    OCD_EN           => false,
    DUAL_CORE_EN     => false,
    ICACHE_EN        => true,   -- I-cache needed for SDRAM execution (32-bit bus)
    CACHE_BLOCK_SIZE => 64,    -- 16 words per cache line (default)
    CACHE_BURSTS_EN  => false,  -- non-burst: sdram_ctrl does individual word reads
    DCACHE_EN        => true
  )
  port map (
    clk_i        => CLOCK,
    rstn_i       => rstn_int,
    -- XBUS
    xbus_adr_o   => xbus_adr,
    xbus_dat_o   => xbus_dat_o,
    xbus_cti_o   => open,
    xbus_tag_o   => open,
    xbus_dat_i   => xbus_dat_i,
    xbus_we_o    => xbus_we,
    xbus_sel_o   => xbus_sel,
    xbus_stb_o   => xbus_stb,
    xbus_cyc_o   => xbus_cyc,
    xbus_ack_i   => xbus_ack,
    xbus_err_i   => xbus_err,
    -- GPIO
    gpio_o       => gpio_out,
    -- UART0
    uart0_txd_o  => TXD,
    uart0_rxd_i  => RXD,
    -- SPI (SD card)
    spi_clk_o    => SD_CLK,
    spi_dat_o    => SD_DI,
    spi_dat_i    => SD_DO,
    spi_csn_o(0) => SD_NCS,
    spi_csn_o(1) => open,
    spi_csn_o(2) => open,
    spi_csn_o(3) => open,
    spi_csn_o(4) => open,
    spi_csn_o(5) => open,
    spi_csn_o(6) => open,
    spi_csn_o(7) => open
  );

  -- DEBUG MODE: LEDs driven by XBUS debug latches (active-low on board)
  -- LED ON = debug bit HIGH, LED OFF = debug bit LOW
  -- LED0: any XBUS request seen (sticky)
  -- LED1: SDRAM-addressed request seen (sticky)
  -- LED2: SDRAM ACK completed (sticky)
  -- LED3: currently pending (live)
  LED(0) <= not dbg_leds(0);
  LED(1) <= not dbg_leds(1);
  LED(2) <= not dbg_leds(2);
  LED(3) <= not dbg_leds(3);

  -- ── Wishbone → SDRAM bridge ───────────────────────────────────────────────
  wb_sdram_inst: wb_sdram_ctrl
  port map (
    clk        => CLOCK,
    rst_n      => rstn_int,
    xbus_adr   => xbus_adr,
    xbus_dat_w => xbus_dat_o,
    xbus_sel   => xbus_sel,
    xbus_we    => xbus_we,
    xbus_stb   => xbus_stb,
    xbus_cyc   => xbus_cyc,
    xbus_dat_r => xbus_dat_i,
    xbus_ack   => xbus_ack,
    xbus_err   => xbus_err,
    S_CLK      => S_CLK,
    S_CKE      => S_CKE,
    S_NCS      => S_NCS,
    S_NRAS     => S_NRAS,
    S_NCAS     => S_NCAS,
    S_NWE      => S_NWE,
    S_BA       => S_BA,
    S_A        => S_A,
    S_DQM      => S_DQM,
    S_DB       => S_DB,
    dbg_leds   => dbg_leds
  );

end architecture rtl;
