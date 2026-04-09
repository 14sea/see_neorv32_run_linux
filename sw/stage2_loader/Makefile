# stage2_loader/Makefile — xmodem receiver that loads U-Boot to SDRAM

MARCH = rv32imc_zicsr_zifencei

# xPack RISC-V bare-metal toolchain. Override on the command line or via
# environment if `riscv-none-elf-gcc` is not already on $PATH:
#   make RISCV_PREFIX=/path/to/bin/riscv-none-elf-
RISCV_PREFIX ?= riscv-none-elf-

EFFORT = -Os

# Must fit in 8 KB IMEM (reduced from 16K to enable ICACHE)
USER_FLAGS += -Wl,--defsym,__neorv32_rom_size=8k
USER_FLAGS += -Wl,--defsym,__neorv32_ram_size=8k

NEORV32_HOME ?= ../../neorv32

include $(NEORV32_HOME)/sw/common/common.mk
