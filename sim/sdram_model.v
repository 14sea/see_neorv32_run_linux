// sdram_model.v — Behavioral SDR-SDRAM model for sdram_ctrl unit testing.
//
// JEDEC-strict subset of HY57V2562GTR behavior:
//   - 4 banks, 8192 rows, 512 cols (16-bit), but model storage is downsized
//     to 32K 16-bit words (covers small test address ranges).
//   - LMR programmable CL (we observe BL=1 + CL=3 from sdram_ctrl).
//   - Auto-precharge on READ/WRITE when A10=1.
//   - Refresh requires all banks IDLE (not enforced — model just notes).
//   - Read pipeline: data driven CL SDRAM cycles (= CL FPGA cycles) after
//     the SDRAM rising edge that sampled the READ command.
//
// Model samples on FPGA falling edge (= SDRAM rising via inverted clock).
// DQ is driven combinationally from a CL-deep shift register so back-to-back
// READs deliver data on consecutive cycles correctly.

`timescale 1ns/1ps

module sdram_model #(
    parameter CL = 3
)(
    input        sdram_clk,    // Inverted from FPGA clock; rising = sample
    input        sdram_cke,
    input        sdram_cs_n,
    input        sdram_ras_n,
    input        sdram_cas_n,
    input        sdram_we_n,
    input  [1:0] sdram_ba,
    input [12:0] sdram_addr,
    input  [1:0] sdram_dqm,
    inout [15:0] sdram_dq
);

    // ── Storage: 32K 16-bit words ────────────────────────────────────────
    reg [15:0] mem [0:32767];

    // Map (ba, row, col) → 15-bit flat index. Use lower bits for compactness.
    function [14:0] mem_idx;
        input [1:0]  ba;
        input [12:0] row;
        input [8:0]  col;
        begin
            mem_idx = {ba, row[5:0], col[6:0]};  // 2 + 6 + 7 = 15 bits
        end
    endfunction

    // ── Per-bank state ───────────────────────────────────────────────────
    reg        bank_active [0:3];
    reg [12:0] bank_row    [0:3];

    // ── CMD decode ───────────────────────────────────────────────────────
    wire [3:0] cmd = {sdram_cs_n, sdram_ras_n, sdram_cas_n, sdram_we_n};
    localparam C_NOP   = 4'b0111;
    localparam C_ACT   = 4'b0011;
    localparam C_READ  = 4'b0101;
    localparam C_WRITE = 4'b0100;
    localparam C_PRE   = 4'b0010;
    localparam C_REF   = 4'b0001;
    localparam C_LMR   = 4'b0000;
    localparam C_INH   = 4'b1xxx;  // CS_N=1: inhibit

    // ── Read data pipeline (CL+1 deep) ───────────────────────────────────
    // Position 0 drives DQ. Capture goes into position CL (at the same edge
    // a shift moves [CL]→[CL-1]; the capture overrides, so net effect is
    // value lands at [CL] post-edge). After CL more shifts, value is at [0]
    // → drives DQ from edge t+CL onwards (= CL SDRAM cycles after sample).
    reg [15:0] rd_data  [0:4];   // depth 5 so CL up to 4 supported
    reg        rd_valid [0:4];

    // ── DQ tristate ──────────────────────────────────────────────────────
    reg [15:0] dq_drv;
    reg        dq_drv_oe;
    assign sdram_dq = dq_drv_oe ? dq_drv : 16'hzzzz;

    // Drive DQ combinationally from rd_valid[0]/rd_data[0] so the FPGA can
    // capture it on the rising edge between two SDRAM falling edges.
    always @(*) begin
        if (rd_valid[0]) begin
            dq_drv    = rd_data[0];
            dq_drv_oe = 1'b1;
        end else begin
            dq_drv    = 16'd0;
            dq_drv_oe = 1'b0;
        end
    end

    // ── Stats / observability ────────────────────────────────────────────
    integer cnt_act = 0, cnt_read = 0, cnt_write = 0, cnt_pre = 0,
            cnt_ref = 0, cnt_lmr = 0;
    integer log_enable = 1;  // set to 0 from TB to silence per-cmd traces

    integer i;
    initial begin
        for (i = 0; i < 4; i = i + 1) begin
            bank_active[i] = 1'b0;
            bank_row[i]    = 13'd0;
        end
        for (i = 0; i < 5; i = i + 1) begin
            rd_data[i]  = 16'd0;
            rd_valid[i] = 1'b0;
        end
        for (i = 0; i < 32768; i = i + 1)
            mem[i] = 16'h0000;
    end

    // ── Main: sample on SDRAM rising edge ────────────────────────────────
    // Note: sdram_clk = ~clk in DUT, so SDRAM rising = FPGA falling.
    always @(posedge sdram_clk) begin
        // Shift the read-data pipeline first
        rd_data[0]  <= rd_data[1];   rd_valid[0] <= rd_valid[1];
        rd_data[1]  <= rd_data[2];   rd_valid[1] <= rd_valid[2];
        rd_data[2]  <= rd_data[3];   rd_valid[2] <= rd_valid[3];
        rd_data[3]  <= rd_data[4];   rd_valid[3] <= rd_valid[4];
        rd_data[4]  <= 16'd0;        rd_valid[4] <= 1'b0;

        // Then process the command at this edge
        if (sdram_cs_n == 1'b0) begin
            case (cmd)
                C_ACT: begin
                    cnt_act = cnt_act + 1;
                    if (bank_active[sdram_ba] && log_enable)
                        $display("[SDRAM @%0t] WARN: ACT bank=%0d but already active row=%h",
                                 $time, sdram_ba, bank_row[sdram_ba]);
                    bank_active[sdram_ba] <= 1'b1;
                    bank_row[sdram_ba]    <= sdram_addr;
                    if (log_enable)
                        $display("[SDRAM @%0t] ACT  ba=%0d row=%0d", $time, sdram_ba, sdram_addr);
                end

                C_READ: begin
                    cnt_read = cnt_read + 1;
                    if (!bank_active[sdram_ba]) begin
                        $display("[SDRAM @%0t] ERROR: READ to inactive bank=%0d", $time, sdram_ba);
                        $finish;
                    end
                    // Latch data for delivery CL cycles later
                    rd_data[CL]  <= mem[mem_idx(sdram_ba, bank_row[sdram_ba], sdram_addr[8:0])];
                    rd_valid[CL] <= 1'b1;
                    if (log_enable)
                        $display("[SDRAM @%0t] READ ba=%0d col=%0d AP=%b → val=%h (will drive in %0d cyc)",
                                 $time, sdram_ba, sdram_addr[8:0], sdram_addr[10],
                                 mem[mem_idx(sdram_ba, bank_row[sdram_ba], sdram_addr[8:0])], CL);
                    if (sdram_addr[10]) begin
                        // Auto-precharge: bank goes idle after the burst (BL=1, so immediately)
                        bank_active[sdram_ba] <= 1'b0;
                    end
                end

                C_WRITE: begin
                    cnt_write = cnt_write + 1;
                    if (!bank_active[sdram_ba]) begin
                        $display("[SDRAM @%0t] ERROR: WRITE to inactive bank=%0d", $time, sdram_ba);
                        $finish;
                    end
                    // Single-write mode: capture DQ this cycle
                    // Apply DQM: dqm[0]=mask byte 0, dqm[1]=mask byte 1
                    if (!sdram_dqm[0])
                        mem[mem_idx(sdram_ba, bank_row[sdram_ba], sdram_addr[8:0])][7:0]
                            <= sdram_dq[7:0];
                    if (!sdram_dqm[1])
                        mem[mem_idx(sdram_ba, bank_row[sdram_ba], sdram_addr[8:0])][15:8]
                            <= sdram_dq[15:8];
                    if (log_enable)
                        $display("[SDRAM @%0t] WRITE ba=%0d col=%0d dqm=%b dq=%h AP=%b",
                                 $time, sdram_ba, sdram_addr[8:0], sdram_dqm, sdram_dq, sdram_addr[10]);
                    if (sdram_addr[10])
                        bank_active[sdram_ba] <= 1'b0;
                end

                C_PRE: begin
                    cnt_pre = cnt_pre + 1;
                    if (sdram_addr[10]) begin
                        // Precharge all banks
                        for (i = 0; i < 4; i = i + 1)
                            bank_active[i] <= 1'b0;
                        if (log_enable)
                            $display("[SDRAM @%0t] PRE  all-banks", $time);
                    end else begin
                        bank_active[sdram_ba] <= 1'b0;
                        if (log_enable)
                            $display("[SDRAM @%0t] PRE  ba=%0d", $time, sdram_ba);
                    end
                end

                C_REF: begin
                    cnt_ref = cnt_ref + 1;
                    // All banks must be IDLE for valid REF
                    for (i = 0; i < 4; i = i + 1) begin
                        if (bank_active[i]) begin
                            $display("[SDRAM @%0t] ERROR: REF with bank %0d still active", $time, i);
                            $finish;
                        end
                    end
                    if (log_enable)
                        $display("[SDRAM @%0t] REF", $time);
                end

                C_LMR: begin
                    cnt_lmr = cnt_lmr + 1;
                    if (log_enable)
                        $display("[SDRAM @%0t] LMR  mode=%h ba=%0d (CL=%0d BL=%0d)",
                                 $time, sdram_addr, sdram_ba,
                                 sdram_addr[6:4], 1 << sdram_addr[2:0]);
                end

                C_NOP: ;  // ignore

                default: ;  // CS_N=1 inhibit handled by outer check
            endcase
        end
    end

endmodule
