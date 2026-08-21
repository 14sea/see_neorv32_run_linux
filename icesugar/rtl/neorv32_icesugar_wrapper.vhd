-- ================================================================================ --
-- NEORV32 wrapper for the iCESugar board (Lattice iCE40UP5K-SG48)                  --
-- -------------------------------------------------------------------------------- --
-- Converted to plain Verilog by GHDL (`ghdl synth --out=verilog`) so that the       --
-- all-Verilog yosys/nextpnr-ice40 flow can consume it.  See ../Makefile.            --
--                                                                                  --
-- Configuration is deliberately MINIMAL: the UP5K has only 5280 logic cells and     --
-- 128 KB of SPRAM, so every optional ISA extension / peripheral that is not needed  --
-- for a UART bring-up is switched off.                                              --
--                                                                                  --
--   ISA      : rv32imc_zicsr_zicntr  (M = serial mul/div, C = compressed)           --
--   IMEM     : 64 KB  -> 2x SB_SPRAM256KA  (see rtl/neorv32_imem_ram.v)             --
--   DMEM     : 64 KB  -> 2x SB_SPRAM256KA  (see rtl/neorv32_dmem_ram.v)             --
--   BOOTROM  : ~4 KB internal bootloader (EBR), UART console @ 19200 baud           --
--   IO       : UART0, CLINT, GPIO[2:0] (RGB LED), SPI (config flash W25Q64)         --
-- -------------------------------------------------------------------------------- --
-- Part of see_neorv32_run_linux; NEORV32 (c) Stephan Nolting, BSD-3-Clause.         --
-- ================================================================================ --

library ieee;
use ieee.std_logic_1164.all;

library neorv32;
use neorv32.neorv32_package.all;

entity neorv32_icesugar_wrapper is
  port (
    -- global control --
    clk_i       : in  std_ulogic;
    rstn_i      : in  std_ulogic;
    -- UART0 (routed to the iCELink USB-CDC bridge) --
    uart0_txd_o : out std_ulogic;
    uart0_rxd_i : in  std_ulogic;
    -- GPIO (only [2:0] are used -> RGB LED) --
    gpio_o      : out std_ulogic_vector(31 downto 0);
    -- SPI host (on-board W25Q64 configuration flash) --
    spi_clk_o   : out std_ulogic;
    spi_dat_o   : out std_ulogic;
    spi_dat_i   : in  std_ulogic;
    spi_csn_o   : out std_ulogic_vector(7 downto 0)
  );
end entity;

architecture neorv32_icesugar_wrapper_rtl of neorv32_icesugar_wrapper is

begin

  neorv32_top_inst: neorv32_top
  generic map (
    -- General --
    CLOCK_FREQUENCY   => 12_000_000,  -- iCESugar: 12 MHz from the iCELink MCO pin (pad 35)
    -- Boot configuration --
    BOOT_MODE_SELECT  => 0,           -- 0 = internal bootloader (UART console + SPI-flash boot)
    -- RISC-V CPU extensions --
    RISCV_ISA_C       => true,        -- compressed instructions (big code-size win on 64 KB IMEM)
    RISCV_ISA_M       => true,        -- mul/div
    RISCV_ISA_Zicntr  => true,        -- cycle/instret counters (used by the delay routines)
    -- Tuning: favour AREA over speed, we only have 5280 LCs --
    CPU_FAST_MUL_EN   => false,       -- serial multiplier (no DSP/SB_MAC16 inference)
    CPU_FAST_SHIFT_EN => false,       -- serial shifter (no barrel shifter)
    -- Internal instruction memory --
    IMEM_EN           => true,
    IMEM_SIZE         => 64*1024,     -- 2x SB_SPRAM256KA
    IMEM_OUTREG_EN    => false,       -- SPRAM output is already registered
    -- Internal data memory --
    DMEM_EN           => true,
    DMEM_SIZE         => 64*1024,     -- 2x SB_SPRAM256KA
    DMEM_OUTREG_EN    => false,
    -- Caches: pointless, IMEM/DMEM are already single-cycle on-chip --
    ICACHE_EN         => false,
    DCACHE_EN         => false,
    -- Peripherals --
    IO_GPIO_NUM       => 3,           -- RGB LED
    IO_CLINT_EN       => true,        -- machine timer (bootloader auto-boot timeout)
    IO_UART0_EN       => true,
    IO_UART0_RX_FIFO  => 1,           -- depth 1 -> mapped to FFs, keeps EBR free for the bootrom
    IO_UART0_TX_FIFO  => 1,
    IO_SPI_EN         => true         -- on-board W25Q64; bootloader flash-boot @ 0x00400000
  )
  port map (
    clk_i       => clk_i,
    rstn_i      => rstn_i,
    uart0_txd_o => uart0_txd_o,
    uart0_rxd_i => uart0_rxd_i,
    gpio_o      => gpio_o,
    spi_clk_o   => spi_clk_o,
    spi_dat_o   => spi_dat_o,
    spi_dat_i   => spi_dat_i,
    spi_csn_o   => spi_csn_o
  );

end architecture;
