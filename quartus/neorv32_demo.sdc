# neorv32_demo.sdc — Timing constraints for AX301 (EP4CE6F17C8)
# 50 MHz system clock

create_clock -name {CLOCK} -period 20.000 [get_ports {CLOCK}]

# SDRAM clock: inverted system clock (sdram_ctrl drives S_CLK = ~clk)
# Already handled by FAST_OUTPUT_REGISTER in QSF; tell TimeQuest about it
create_generated_clock -name {S_CLK} -source [get_ports {CLOCK}] \
    -invert [get_ports {S_CLK}]

# Async inputs: no timing analysis needed
set_false_path -from [get_ports {KEY2}]
set_false_path -from [get_ports {RXD}]
set_false_path -to   [get_ports {TXD}]
set_false_path -to   [get_ports {LED[*]}]

# SDRAM I/O
set_input_delay  -clock {S_CLK} -max  6.0 [get_ports {S_DB[*]}]
set_input_delay  -clock {S_CLK} -min  1.0 [get_ports {S_DB[*]}]
set_output_delay -clock {S_CLK} -max  1.5 [get_ports {S_*}]
set_output_delay -clock {S_CLK} -min -0.8 [get_ports {S_*}]
