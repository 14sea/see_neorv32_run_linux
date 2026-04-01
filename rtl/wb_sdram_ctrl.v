// wb_sdram_ctrl.v — Wishbone (NEORV32 XBUS) to sdram_ctrl bridge
//
// Maps NEORV32 XBUS Wishbone cycles to the sdram_ctrl.v interface.
// Only responds to addresses 0x40000000–0x41FFFFFF (addr[31:25] == 7'b010_0000).
// Other addresses immediately ACK with zero data (safe default).

module wb_sdram_ctrl (
    input         clk,
    input         rst_n,

    // NEORV32 XBUS (Wishbone-compatible)
    input  [31:0] xbus_adr,
    input  [31:0] xbus_dat_w,
    input  [3:0]  xbus_sel,
    input         xbus_we,
    input         xbus_stb,
    input         xbus_cyc,
    output reg [31:0] xbus_dat_r,
    output reg    xbus_ack,
    output        xbus_err,

    // SDRAM board pins (S_* naming matches AX301 / QSF)
    output        S_CLK,
    output        S_CKE,
    output        S_NCS,
    output        S_NRAS,
    output        S_NCAS,
    output        S_NWE,
    output [1:0]  S_BA,
    output [12:0] S_A,
    output [1:0]  S_DQM,
    inout  [15:0] S_DB,

    // Debug outputs (active-high, directly drive LEDs)
    // [0] = any XBUS request ever seen (sticky)
    // [1] = SDRAM-addressed XBUS request ever seen (sticky)
    // [2] = SDRAM ACK ever completed (sticky)
    // [3] = currently pending (live, not sticky)
    output [3:0]  dbg_leds
);

    // ── sdram_ctrl internal wires ────────────────────────────────────────────
    wire [31:0] sdram_rdata;
    wire        sdram_ready;

    // ── Address decode: SDRAM at 0x40000000–0x41FFFFFF ───────────────────────
    wire in_sdram = (xbus_adr[31:25] == 7'b010_0000);

    // ── Pending transaction tracker ──────────────────────────────────────────
    // Assert sdram_ctrl's `sel` for exactly one cycle after bus request arrives,
    // hold it HIGH until sdram_ctrl pulses `ready`.
    reg pending;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            pending    <= 1'b0;
            xbus_ack   <= 1'b0;
            xbus_dat_r <= 32'd0;
        end else begin
            xbus_ack <= 1'b0;  // default: deasserted

            if (xbus_cyc && xbus_stb && !pending) begin
                if (in_sdram) begin
                    pending <= 1'b1;           // start SDRAM transaction
                end else begin
                    // Non-SDRAM address: immediate ACK with zero data
                    xbus_dat_r <= 32'd0;
                    xbus_ack   <= 1'b1;
                end
            end

            if (pending && sdram_ready) begin
                xbus_dat_r <= sdram_rdata;
                xbus_ack   <= 1'b1;
                pending    <= 1'b0;
            end
        end
    end

    assign xbus_err = 1'b0;

    // ── Debug sticky latches ───────────────────────────────────────────────
    reg dbg_any_req;    // any XBUS request seen
    reg dbg_sdram_req;  // SDRAM-addressed request seen
    reg dbg_sdram_ack;  // SDRAM ACK completed

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            dbg_any_req   <= 1'b0;
            dbg_sdram_req <= 1'b0;
            dbg_sdram_ack <= 1'b0;
        end else begin
            if (xbus_cyc && xbus_stb)
                dbg_any_req <= 1'b1;
            if (xbus_cyc && xbus_stb && in_sdram)
                dbg_sdram_req <= 1'b1;
            if (pending && sdram_ready)
                dbg_sdram_ack <= 1'b1;
        end
    end

    assign dbg_leds = {pending, dbg_sdram_ack, dbg_sdram_req, dbg_any_req};

    // ── sdram_ctrl instantiation ─────────────────────────────────────────────
    sdram_ctrl u_sdram (
        .clk       (clk),
        .rst_n     (rst_n),
        .sel       (pending),
        .addr      (xbus_adr[24:0]),
        .wdata     (xbus_dat_w),
        .wstrb     (xbus_we ? xbus_sel : 4'b0000),
        .rdata     (sdram_rdata),
        .ready     (sdram_ready),
        // SDRAM pins (sdram_ctrl names → AX301 board names)
        .sdram_clk  (S_CLK),
        .sdram_cke  (S_CKE),
        .sdram_cs_n (S_NCS),
        .sdram_ras_n(S_NRAS),
        .sdram_cas_n(S_NCAS),
        .sdram_we_n (S_NWE),
        .sdram_ba   (S_BA),
        .sdram_addr (S_A),
        .sdram_dqm  (S_DQM),
        .sdram_dq   (S_DB),
        .init_done  ()  // unused
    );

endmodule
