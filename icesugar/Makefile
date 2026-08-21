# ================================================================================ #
# NEORV32 on iCESugar (iCE40UP5K-SG48) - fully open-source build flow              #
# -------------------------------------------------------------------------------- #
#   ghdl synth --out=verilog   VHDL -> plain Verilog (no ghdl-yosys-plugin needed)  #
#   yosys synth_ice40          Verilog -> JSON netlist                              #
#   nextpnr-ice40              place & route                                        #
#   icepack                    -> bitstream (drag-and-drop onto the iCELink drive)  #
# -------------------------------------------------------------------------------- #
#   make            # bitstream, ends with a resource + timing report              #
#   make convert    # only the VHDL -> Verilog step                                 #
#   make sw         # build the bare-metal demo (sw/hello)                          #
#   make report     # re-print resource usage and Fmax                              #
#   make upload     # upload + run the demo over UART  [TOUCHES HARDWARE]           #
#   make clean                                                                      #
# ================================================================================ #

.DEFAULT_GOAL := all

# ---- tools -------------------------------------------------------------------
GHDL     ?= ghdl
YOSYS    ?= yosys
NEXTPNR  ?= nextpnr-ice40
ICEPACK  ?= icepack
ICETIME  ?= icetime

# ---- project -----------------------------------------------------------------
NEORV32_HOME ?= $(abspath ..)/neorv32
TOP          := icesugar_top
WRAPPER      := neorv32_icesugar_wrapper
BUILD        := build
PCF          := icesugar.pcf

DEVICE  := up5k
PACKAGE := sg48
FREQ    := 12

# NEORV32 SoC source list, with the memory primitives excluded so that they
# become black boxes bound to our SPRAM Verilog IP.
VHDL_EXCLUDE  := neorv32_imem_ram.vhd neorv32_dmem_ram.vhd
NEORV32_FFILE := $(shell cat $(NEORV32_HOME)/rtl/file_list_soc.f)
NEORV32_SRCS  := $(subst $$NEORV32_HOME,$(NEORV32_HOME),$(NEORV32_FFILE))
VHDL_SRCS     := $(filter-out $(addprefix %,$(VHDL_EXCLUDE)),$(NEORV32_SRCS))

# Hand-written Verilog: top level + the two SPRAM memory IPs
VERILOG_SRCS := rtl/icesugar_top.v rtl/neorv32_imem_ram.v rtl/neorv32_dmem_ram.v

GEN_V := $(BUILD)/$(WRAPPER).v

all: $(BUILD)/$(TOP).bin report

# ---- 1. VHDL -> Verilog ------------------------------------------------------
$(GEN_V): rtl/$(WRAPPER).vhd $(VHDL_SRCS)
	@echo "==> [1/4] GHDL: VHDL -> Verilog"
	@mkdir -p $(BUILD)/ghdl
	@$(GHDL) -i --std=08 --work=neorv32 --workdir=$(BUILD)/ghdl $(VHDL_SRCS) rtl/$(WRAPPER).vhd
	@$(GHDL) -m --std=08 --work=neorv32 --workdir=$(BUILD)/ghdl $(WRAPPER)
	@$(GHDL) synth --std=08 --work=neorv32 --workdir=$(BUILD)/ghdl --out=verilog $(WRAPPER) > $@
	@echo "    -> $@ ($$(wc -l < $@) lines)"

convert: $(GEN_V)

# ---- 2. synthesis ------------------------------------------------------------
$(BUILD)/$(TOP).json: $(GEN_V) $(VERILOG_SRCS)
	@echo "==> [2/4] yosys: synthesis"
	@$(YOSYS) -q -l $(BUILD)/yosys.log \
	  -p "read_verilog $(GEN_V) $(VERILOG_SRCS); synth_ice40 -top $(TOP) -json $@"
	@echo "    -> $@  (log: $(BUILD)/yosys.log)"

# ---- 3. place & route --------------------------------------------------------
$(BUILD)/$(TOP).asc: $(BUILD)/$(TOP).json $(PCF)
	@echo "==> [3/4] nextpnr-ice40: place & route"
	@$(NEXTPNR) --$(DEVICE) --package $(PACKAGE) --freq $(FREQ) \
	  --json $< --pcf $(PCF) --asc $@ --pcf-allow-unconstrained \
	  2>&1 | tee $(BUILD)/nextpnr.log | grep -E "^Info: (Device utilisation|  [A-Z]|Max frequency)" || true

# ---- 4. bitstream ------------------------------------------------------------
$(BUILD)/$(TOP).bin: $(BUILD)/$(TOP).asc
	@echo "==> [4/4] icepack: bitstream"
	@$(ICEPACK) $< $@
	@echo "    -> $@ ($$(stat -c%s $@) bytes)"

# ---- report ------------------------------------------------------------------
report:
	@echo ""
	@echo "=============== Device utilisation (iCE40 $(DEVICE)) ==============="
	@sed -n '/Device utilisation/,/^Info: Placed/p' $(BUILD)/nextpnr.log | grep -E "^Info:" | head -20
	@echo "=============== Timing ============================================"
	@grep -E "Max frequency for clock|Max delay" $(BUILD)/nextpnr.log | tail -5
	@echo "==================================================================="

# ---- software ----------------------------------------------------------------
# The xPack bare-metal toolchain; same one the Linux kernel build uses.
RISCV_PATH ?= $(HOME)/xpack-riscv-none-elf-gcc-14.2.0-3/bin

sw:
	@echo "==> building sw/hello"
	@PATH=$$PATH:$(RISCV_PATH) $(MAKE) -C sw/hello clean_all exe

# ---- hardware (needs the board attached; see README) -------------------------
PORT ?= /dev/ttyACM0

upload: sw
	python3 host/upload.py --port $(PORT)

term:
	python3 host/upload.py --port $(PORT) --terminal

clean:
	rm -rf $(BUILD)
	@$(MAKE) -C sw/hello clean_all 2>/dev/null || true

.PHONY: all convert sw report upload term clean
