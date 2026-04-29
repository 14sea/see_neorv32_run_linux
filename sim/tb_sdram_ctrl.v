// tb_sdram_ctrl.v — Unit testbench for sdram_ctrl + sdram_model.
//
// Drives sdram_ctrl through a series of read/write transactions to a
// behavioral SDRAM model and validates rdata + cycle behavior.

`timescale 1ns/1ps

module tb_sdram_ctrl;

    // ── Clock + reset ────────────────────────────────────────────────────
    reg clk = 0;
    reg rst_n = 0;
    always #10 clk = ~clk;  // 50 MHz

    // ── DUT interface ────────────────────────────────────────────────────
    reg         sel   = 0;
    reg  [24:0] addr  = 0;
    reg  [31:0] wdata = 0;
    reg  [3:0]  wstrb = 0;
    wire [31:0] rdata;
    wire        ready;

    // ── SDRAM bus ────────────────────────────────────────────────────────
    wire        sdram_clk;
    wire        sdram_cke;
    wire        sdram_cs_n;
    wire        sdram_ras_n;
    wire        sdram_cas_n;
    wire        sdram_we_n;
    wire [1:0]  sdram_ba;
    wire [12:0] sdram_addr;
    wire [1:0]  sdram_dqm;
    wire [15:0] sdram_dq;
    wire        init_done;

    // ── DUT ──────────────────────────────────────────────────────────────
    sdram_ctrl dut (
        .clk        (clk),
        .rst_n      (rst_n),
        .sel        (sel),
        .addr       (addr),
        .wdata      (wdata),
        .wstrb      (wstrb),
        .rdata      (rdata),
        .ready      (ready),
        .sdram_clk  (sdram_clk),
        .sdram_cke  (sdram_cke),
        .sdram_cs_n (sdram_cs_n),
        .sdram_ras_n(sdram_ras_n),
        .sdram_cas_n(sdram_cas_n),
        .sdram_we_n (sdram_we_n),
        .sdram_ba   (sdram_ba),
        .sdram_addr (sdram_addr),
        .sdram_dqm  (sdram_dqm),
        .sdram_dq   (sdram_dq),
        .init_done  (init_done)
    );

    // ── SDRAM behavioral model ───────────────────────────────────────────
    sdram_model #(.CL(3)) sdram_dut (
        .sdram_clk  (sdram_clk),
        .sdram_cke  (sdram_cke),
        .sdram_cs_n (sdram_cs_n),
        .sdram_ras_n(sdram_ras_n),
        .sdram_cas_n(sdram_cas_n),
        .sdram_we_n (sdram_we_n),
        .sdram_ba   (sdram_ba),
        .sdram_addr (sdram_addr),
        .sdram_dqm  (sdram_dqm),
        .sdram_dq   (sdram_dq)
    );

    // ── Test bookkeeping ─────────────────────────────────────────────────
    integer passed = 0;
    integer failed = 0;

    task check_eq32;
        input [255:0] label;
        input [31:0] got;
        input [31:0] exp;
        begin
            if (got === exp) begin
                $display("  PASS: %0s  (got=%h)", label, got);
                passed = passed + 1;
            end else begin
                $display("  FAIL: %0s  exp=%h got=%h", label, exp, got);
                failed = failed + 1;
            end
        end
    endtask

    // ── Bus driver tasks ─────────────────────────────────────────────────
    task wait_ready;
        integer cycles;
        begin
            cycles = 0;
            while (!ready) begin
                @(posedge clk);
                cycles = cycles + 1;
                if (cycles > 100) begin
                    $display("[TB] TIMEOUT waiting for ready (>100 cycles)");
                    $finish;
                end
            end
        end
    endtask

    task do_write;
        input [24:0] a;
        input [31:0] d;
        input [3:0]  s;
        begin
            @(negedge clk);
            sel   = 1'b1;
            addr  = a;
            wdata = d;
            wstrb = s;
            @(posedge clk);
            wait_ready;
            @(negedge clk);
            sel   = 1'b0;
            addr  = 0;
            wdata = 0;
            wstrb = 0;
            // Honor S_DONE_W gap: wait one extra cycle before next access
            @(posedge clk);
        end
    endtask

    task do_read;
        input  [24:0] a;
        output [31:0] r;
        begin
            @(negedge clk);
            sel   = 1'b1;
            addr  = a;
            wstrb = 4'b0000;
            @(posedge clk);
            wait_ready;
            r = rdata;
            @(negedge clk);
            sel   = 1'b0;
            addr  = 0;
            @(posedge clk);
        end
    endtask

    task do_read_check;
        input [24:0]  a;
        input [31:0]  exp;
        input [255:0] label;
        reg   [31:0]  got;
        begin
            do_read(a, got);
            check_eq32(label, got, exp);
        end
    endtask

    // ── Cycle counter for timing measurements ────────────────────────────
    integer cycle_start;
    task time_op_start;
        begin
            cycle_start = $time / 20;  // 20 ns per cycle
        end
    endtask
    task time_op_end;
        input [255:0] label;
        integer elapsed;
        begin
            elapsed = ($time / 20) - cycle_start;
            $display("  TIMING: %0s = %0d cycles", label, elapsed);
        end
    endtask

    // ── Test sequence ────────────────────────────────────────────────────
    integer i;
    reg [31:0] tmp;

    initial begin
        $dumpfile("tb_sdram_ctrl.vcd");
        $dumpvars(0, tb_sdram_ctrl);

        rst_n = 1'b0;
        repeat (4) @(posedge clk);
        rst_n = 1'b1;

        // INIT_CYCLES=10000 idle + 9 init states ≈ 10020 cycles ≈ 200 µs
        $display("[TB] Waiting for SDRAM init...");
        sdram_dut.log_enable = 0;  // silence init traces
        wait (init_done);
        sdram_dut.log_enable = 1;
        $display("[TB] Init done at %0t (%0d cycles)", $time, $time / 20);

        // ── T1: simple write + read same address, same row ──────────────
        $display("\n[T1] Single 32-bit write + read, addr 0x00000000");
        do_write(25'h0000000, 32'hCAFE_BABE, 4'b1111);
        do_read_check(25'h0000000, 32'hCAFE_BABE, "T1 write/read 0x0=CAFEBABE");

        // ── T2: write/read pair at column boundary ──────────────────────
        $display("\n[T2] Write+read at addr 0x00000004 (next word, same row)");
        do_write(25'h0000004, 32'h1234_5678, 4'b1111);
        do_read_check(25'h0000004, 32'h1234_5678, "T2 write/read 0x4=12345678");
        do_read_check(25'h0000000, 32'hCAFE_BABE, "T2 re-read 0x0 still CAFEBABE");

        // ── T3: byte-strobed write ──────────────────────────────────────
        $display("\n[T3] Byte-strobed write (mask middle bytes)");
        do_write(25'h0000008, 32'hAABBCCDD, 4'b1111);
        do_write(25'h0000008, 32'h11223344, 4'b1001);  // keep [3]+[0] only
        // Expected: bytes 3,2,1,0 = 11,BB,CC,44 → 0x11BBCC44
        do_read_check(25'h0000008, 32'h11BBCC44, "T3 byte-strobed update");

        // ── T4: cache-line-style sequential 8-word access ───────────────
        $display("\n[T4] Sequential 8-word write, then 8-word read (same row)");
        for (i = 0; i < 8; i = i + 1)
            do_write(25'h0000020 + i*4, 32'hAA000000 | i, 4'b1111);
        for (i = 0; i < 8; i = i + 1)
            do_read_check(25'h0000020 + i*4, 32'hAA000000 | i, "T4 seq-read");

        // ── T5: cross-bank access ───────────────────────────────────────
        $display("\n[T5] Cross-bank: ba=0 then ba=1 then back to ba=0");
        do_write(25'h0000040, 32'hB0B0B0B0, 4'b1111);     // ba=0
        do_write(25'h0800040, 32'hB1B1B1B1, 4'b1111);     // ba=1 (bit[24:23]=01)
        do_read_check(25'h0000040, 32'hB0B0B0B0, "T5 ba=0 readback");
        do_read_check(25'h0800040, 32'hB1B1B1B1, "T5 ba=1 readback");

        // ── T6: cross-row access (same bank) ────────────────────────────
        $display("\n[T6] Cross-row: bank 0 row 0 → row 1 → row 0");
        do_write(25'h0000050, 32'h00000000, 4'b1111);     // ba=0 row=0
        do_write(25'h0000450, 32'h11111111, 4'b1111);     // ba=0 row=1 (addr[10]=1 → row[0]=1)
        do_read_check(25'h0000050, 32'h00000000, "T6 row 0 readback");
        do_read_check(25'h0000450, 32'h11111111, "T6 row 1 readback");

        // ── T7: timing measurement — single access cycle count ──────────
        $display("\n[T7] Single 32-bit read cycle count (after open row)");
        do_write(25'h0000060, 32'hDEADBEEF, 4'b1111);    // make sure mem has it
        time_op_start;
        do_read(25'h0000060, tmp);
        time_op_end("T7 single read total cycles");
        check_eq32("T7 read value", tmp, 32'hDEADBEEF);

        // ── Summary ─────────────────────────────────────────────────────
        $display("\n========================================");
        $display("[TB] Test summary: %0d passed, %0d failed", passed, failed);
        $display("[TB] SDRAM stats: ACT=%0d READ=%0d WRITE=%0d PRE=%0d REF=%0d LMR=%0d",
                 sdram_dut.cnt_act, sdram_dut.cnt_read, sdram_dut.cnt_write,
                 sdram_dut.cnt_pre, sdram_dut.cnt_ref, sdram_dut.cnt_lmr);
        if (failed == 0)
            $display("[TB] ALL TESTS PASSED");
        else
            $display("[TB] %0d FAILURES", failed);
        $finish;
    end

    // Hard timeout
    initial begin
        #500000;  // 500 µs
        $display("[TB] HARD TIMEOUT at %0t", $time);
        $finish;
    end

endmodule
