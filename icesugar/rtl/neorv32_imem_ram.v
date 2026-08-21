// ================================================================================ //
// NEORV32 IMEM RAM primitive -> Lattice iCE40 UltraPlus SPRAM                          //
// -------------------------------------------------------------------------------- //
// Drop-in replacement for neorv32/rtl/core/neorv32_imem_ram.vhd.  The VHDL file is    //
// excluded from the GHDL-to-Verilog conversion (VHDL_EXCLUDE in ../Makefile), which //
// leaves a black-box instance that this module binds to.                            //
//                                                                                  //
// 64 KB = 16384 x 32 bit  ->  2 x SB_SPRAM256KA (each 16384 x 16 bit).              //
// The UP5K has exactly 4 SPRAMs, so IMEM + DMEM consume all of them.                //
//                                                                                  //
// SB_SPRAM256KA MASKWREN is NIBBLE granular:                                        //
//   MASKWREN[0] -> DATAIN[3:0]    MASKWREN[1] -> DATAIN[7:4]                        //
//   MASKWREN[2] -> DATAIN[11:8]   MASKWREN[3] -> DATAIN[15:12]                      //
// so one byte-enable has to drive two adjacent mask bits.                           //
//                                                                                  //
// DATAOUT is registered inside the SPRAM (1 cycle), which matches the read latency  //
// of the generic NEORV32 primitive -> instantiate with OUTREG = 0.                  //
// ================================================================================ //

module neorv32_imem_ram #(
  parameter AWIDTH = 16, // byte-address width; 16 => 64 KB (must match IMEM_SIZE)
  parameter OUTREG = 0   // add an additional output register when 1
)(
  input         clk_i,   // clock, rising edge
  input  [3:0]  en_i,    // byte-wise access enable
  input         rw_i,    // 1 = write, 0 = read
  input  [31:0] addr_i,  // full byte address
  input  [31:0] data_i,  // write data
  output [31:0] data_o   // read data (synchronous)
);

  // 14-bit word address into each SPRAM
  wire [13:0] waddr = addr_i[15:2];

  // chip-select on any enabled byte lane; write only when the access is a write
  wire cs   = |en_i;
  wire wren = rw_i & cs;

  wire [31:0] rdata;

  // ---------------------------------------------------------------------------
  // low half-word: byte 0 -> DATAIN[7:0], byte 1 -> DATAIN[15:8]
  // ---------------------------------------------------------------------------
  SB_SPRAM256KA spram_lo (
    .ADDRESS    (waddr),
    .DATAIN     (data_i[15:0]),
    .MASKWREN   ({en_i[1], en_i[1], en_i[0], en_i[0]}),
    .WREN       (wren),
    .CHIPSELECT (cs),
    .CLOCK      (clk_i),
    .STANDBY    (1'b0),
    .SLEEP      (1'b0),
    .POWEROFF   (1'b1),   // active LOW: 1 = powered up
    .DATAOUT    (rdata[15:0])
  );

  // ---------------------------------------------------------------------------
  // high half-word: byte 2 -> DATAIN[7:0], byte 3 -> DATAIN[15:8]
  // ---------------------------------------------------------------------------
  SB_SPRAM256KA spram_hi (
    .ADDRESS    (waddr),
    .DATAIN     (data_i[31:16]),
    .MASKWREN   ({en_i[3], en_i[3], en_i[2], en_i[2]}),
    .WREN       (wren),
    .CHIPSELECT (cs),
    .CLOCK      (clk_i),
    .STANDBY    (1'b0),
    .SLEEP      (1'b0),
    .POWEROFF   (1'b1),
    .DATAOUT    (rdata[31:16])
  );

  generate
    if (OUTREG == 1) begin : g_outreg
      reg [31:0] rdata_reg;
      always @(posedge clk_i) rdata_reg <= rdata;
      assign data_o = rdata_reg;
    end else begin : g_direct
      assign data_o = rdata;
    end
  endgenerate

endmodule
