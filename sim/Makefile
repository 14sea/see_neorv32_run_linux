# sim/Makefile — iverilog-based unit tests for sdram_ctrl.

IVERILOG ?= iverilog
VVP      ?= vvp

RTL_DIR  := ../rtl

.PHONY: all sdram clean wave

all: sdram

# Test 1: sdram_ctrl + behavioral SDRAM model
sdram:
	$(IVERILOG) -g2012 -o tb_sdram_ctrl.vvp \
	    tb_sdram_ctrl.v sdram_model.v $(RTL_DIR)/sdram_ctrl.v
	$(VVP) tb_sdram_ctrl.vvp

wave: sdram
	gtkwave tb_sdram_ctrl.vcd &

clean:
	rm -f *.vvp *.vcd
