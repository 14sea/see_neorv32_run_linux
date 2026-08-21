# NEORV32 iCESugar bring-up demo - application makefile

# CPU ISA as configured in rtl/neorv32_icesugar_wrapper.vhd
MARCH = rv32imc_zicsr_zifencei
MABI  = ilp32

EFFORT = -Os

# Memory sizes must match the wrapper generics (64 KB each, 2x SB_SPRAM256KA)
USER_FLAGS += -Wl,--defsym,__neorv32_rom_size=64k
USER_FLAGS += -Wl,--defsym,__neorv32_ram_size=64k

# NEORV32 root (submodule of this repository)
NEORV32_HOME ?= ../../../neorv32

include $(NEORV32_HOME)/sw/common/common.mk
