// SDRAM Controller for HY57V2562GTR (4M × 16-bit × 4 banks = 32 MB)
// 50 MHz system clock, BL=1, CL=3
// Provides 32-bit CPU interface via two consecutive 16-bit SDRAM accesses
//
// Address mapping (CPU byte address → SDRAM):
//   addr[24:23] → BA[1:0]   (bank)
//   addr[22:10] → Row[12:0]
//   addr[9:1]   → Col[8:0]
//   addr[0]     → byte within 16-bit word

module sdram_ctrl (
    input             clk,
    input             rst_n,

    // CPU memory interface (active when selected by bus arbiter)
    input             sel,        // this peripheral is selected
    input      [24:0] addr,       // byte address within 32 MB
    input      [31:0] wdata,
    input      [ 3:0] wstrb,      // 0000 = read
    output reg [31:0] rdata,
    output reg        ready,

    // SDRAM pins
    output            sdram_clk,
    output            sdram_cke,
    output reg        sdram_cs_n,
    output reg        sdram_ras_n,
    output reg        sdram_cas_n,
    output reg        sdram_we_n,
    output reg [ 1:0] sdram_ba,
    output reg [12:0] sdram_addr,
    output reg [ 1:0] sdram_dqm,
    inout      [15:0] sdram_dq,

    output            init_done
);

    // SDRAM clock: inverted system clock for proper setup/hold at 50 MHz
    assign sdram_clk = ~clk;
    assign sdram_cke = 1'b1;

    // Timing parameters (50 MHz = 20 ns period, conservative)
    localparam INIT_CYCLES = 14'd5000;  // 100 µs
    localparam TRP   = 3'd1;  // Precharge to ready (20 ns = 1 cycle)
    localparam TRC   = 3'd4;  // Refresh cycle (63 ns ≈ 4 cycles)
    localparam TRCD  = 3'd1;  // ACT to READ/WRITE (20 ns = 1 cycle)
    localparam CL    = 3'd3;  // CAS latency
    localparam TWR   = 3'd2;  // Write recovery
    localparam TMRD  = 3'd2;  // Mode register set delay
    localparam REF_INTERVAL = 10'd380;  // Refresh every 7.8 µs (390 cycles, with margin)

    // SDRAM commands: {CS_N, RAS_N, CAS_N, WE_N}
    localparam CMD_NOP      = 4'b0111;
    localparam CMD_ACT      = 4'b0011;  // Activate (open row)
    localparam CMD_READ     = 4'b0101;
    localparam CMD_WRITE    = 4'b0100;
    localparam CMD_PRECHARGE= 4'b0010;
    localparam CMD_REFRESH  = 4'b0001;
    localparam CMD_LMR      = 4'b0000;  // Load Mode Register
    localparam CMD_INHIBIT  = 4'b1111;

    // FSM states
    localparam S_INIT_WAIT  = 5'd0;
    localparam S_INIT_PRE   = 5'd1;
    localparam S_INIT_PRE_W = 5'd2;
    localparam S_INIT_AR1   = 5'd3;
    localparam S_INIT_AR1_W = 5'd4;
    localparam S_INIT_AR2   = 5'd5;
    localparam S_INIT_AR2_W = 5'd6;
    localparam S_INIT_LMR   = 5'd7;
    localparam S_INIT_LMR_W = 5'd8;
    localparam S_IDLE       = 5'd9;
    localparam S_REF_PRE    = 5'd10;
    localparam S_REF_PRE_W  = 5'd11;
    localparam S_REF_AR     = 5'd12;
    localparam S_REF_AR_W   = 5'd13;
    localparam S_ACT        = 5'd14;
    localparam S_ACT_W      = 5'd15;
    localparam S_RD_CMD0    = 5'd16;
    localparam S_RD_CL0     = 5'd17;
    localparam S_RD_CAP0    = 5'd18;
    localparam S_RD_CMD1    = 5'd19;
    localparam S_RD_CL1     = 5'd20;
    localparam S_RD_CAP1    = 5'd21;
    localparam S_RD_PRE_W   = 5'd22;
    localparam S_WR_CMD0    = 5'd23;
    localparam S_WR_CMD1    = 5'd24;
    localparam S_WR_REC     = 5'd25;
    localparam S_DONE       = 5'd26;
    localparam S_DONE_W     = 5'd27;  // 1-cycle gap: let wb_sdram_ctrl clear pending before S_IDLE

    reg [4:0]  state;
    reg [13:0] cnt;
    reg [9:0]  ref_cnt;       // refresh interval counter
    reg        ref_req;       // refresh requested
    reg        initialized;
    reg [15:0] data_lo;       // captured low 16 bits during read
    reg        is_write;      // latched write flag
    reg [24:0] addr_lat;      // latched address
    reg [31:0] wdata_lat;     // latched write data
    reg [ 3:0] wstrb_lat;    // latched write strobe

    // DQ tristate
    reg        dq_oe;
    reg [15:0] dq_out;
    assign sdram_dq = dq_oe ? dq_out : 16'hzzzz;

    assign init_done = initialized;

    // Issue SDRAM command
    task cmd;
        input [3:0] c;
        begin
            {sdram_cs_n, sdram_ras_n, sdram_cas_n, sdram_we_n} <= c;
        end
    endtask

    // Refresh counter
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            ref_cnt <= 10'd0;
            ref_req <= 1'b0;
        end else if (initialized) begin
            if (ref_cnt >= REF_INTERVAL) begin
                ref_cnt <= 10'd0;
                ref_req <= 1'b1;
            end else begin
                ref_cnt <= ref_cnt + 10'd1;
            end
            if (state == S_REF_AR)
                ref_req <= 1'b0;
        end
    end

    // Address decomposition for latched address
    wire [1:0]  lat_ba  = addr_lat[24:23];
    wire [12:0] lat_row = addr_lat[22:10];
    wire [8:0]  lat_col_lo = {addr_lat[9:2], 1'b0};
    wire [8:0]  lat_col_hi = {addr_lat[9:2], 1'b1};

    // Main FSM
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state       <= S_INIT_WAIT;
            cnt         <= 14'd0;
            initialized <= 1'b0;
            ready       <= 1'b0;
            rdata       <= 32'd0;
            data_lo     <= 16'd0;
            is_write    <= 1'b0;
            addr_lat    <= 25'd0;
            wdata_lat   <= 32'd0;
            wstrb_lat   <= 4'd0;
            dq_oe       <= 1'b0;
            dq_out      <= 16'd0;
            sdram_cs_n  <= 1'b1;
            sdram_ras_n <= 1'b1;
            sdram_cas_n <= 1'b1;
            sdram_we_n  <= 1'b1;
            sdram_ba    <= 2'b00;
            sdram_addr  <= 13'd0;
            sdram_dqm   <= 2'b00;
        end else begin
            ready <= 1'b0;  // default: deassert ready

            case (state)

            // ========== INITIALIZATION ==========
            S_INIT_WAIT: begin
                cmd(CMD_INHIBIT);
                dq_oe <= 1'b0;
                if (cnt >= INIT_CYCLES) begin
                    cnt   <= 14'd0;
                    state <= S_INIT_PRE;
                end else begin
                    cnt <= cnt + 14'd1;
                end
            end

            S_INIT_PRE: begin
                cmd(CMD_PRECHARGE);
                sdram_addr[10] <= 1'b1;  // precharge all banks
                cnt   <= 14'd0;
                state <= S_INIT_PRE_W;
            end

            S_INIT_PRE_W: begin
                cmd(CMD_NOP);
                if (cnt >= TRP) begin
                    cnt   <= 14'd0;
                    state <= S_INIT_AR1;
                end else
                    cnt <= cnt + 14'd1;
            end

            S_INIT_AR1: begin
                cmd(CMD_REFRESH);
                cnt   <= 14'd0;
                state <= S_INIT_AR1_W;
            end

            S_INIT_AR1_W: begin
                cmd(CMD_NOP);
                if (cnt >= TRC) begin
                    cnt   <= 14'd0;
                    state <= S_INIT_AR2;
                end else
                    cnt <= cnt + 14'd1;
            end

            S_INIT_AR2: begin
                cmd(CMD_REFRESH);
                cnt   <= 14'd0;
                state <= S_INIT_AR2_W;
            end

            S_INIT_AR2_W: begin
                cmd(CMD_NOP);
                if (cnt >= TRC) begin
                    cnt   <= 14'd0;
                    state <= S_INIT_LMR;
                end else
                    cnt <= cnt + 14'd1;
            end

            S_INIT_LMR: begin
                cmd(CMD_LMR);
                sdram_ba   <= 2'b00;
                // Mode: BL=1, Sequential, CL=3, single write
                sdram_addr <= {3'b000, 1'b0, 2'b00, 3'b011, 1'b0, 3'b000};
                cnt   <= 14'd0;
                state <= S_INIT_LMR_W;
            end

            S_INIT_LMR_W: begin
                cmd(CMD_NOP);
                if (cnt >= TMRD) begin
                    initialized <= 1'b1;
                    state <= S_IDLE;
                end else
                    cnt <= cnt + 14'd1;
            end

            // ========== IDLE ==========
            S_IDLE: begin
                cmd(CMD_NOP);
                dq_oe <= 1'b0;
                if (ref_req) begin
                    state <= S_REF_PRE;
                end else if (sel) begin
                    addr_lat  <= addr;
                    wdata_lat <= wdata;
                    wstrb_lat <= wstrb;
                    is_write  <= (wstrb != 4'b0000);
                    state     <= S_ACT;
                end
            end

            // ========== REFRESH ==========
            S_REF_PRE: begin
                cmd(CMD_PRECHARGE);
                sdram_addr[10] <= 1'b1;
                cnt   <= 14'd0;
                state <= S_REF_PRE_W;
            end

            S_REF_PRE_W: begin
                cmd(CMD_NOP);
                if (cnt >= TRP) begin
                    cnt   <= 14'd0;
                    state <= S_REF_AR;
                end else
                    cnt <= cnt + 14'd1;
            end

            S_REF_AR: begin
                cmd(CMD_REFRESH);
                cnt   <= 14'd0;
                state <= S_REF_AR_W;
            end

            S_REF_AR_W: begin
                cmd(CMD_NOP);
                if (cnt >= TRC) begin
                    state <= S_IDLE;
                end else
                    cnt <= cnt + 14'd1;
            end

            // ========== ACTIVATE ==========
            S_ACT: begin
                cmd(CMD_ACT);
                sdram_ba   <= lat_ba;
                sdram_addr <= lat_row;
                cnt   <= 14'd0;
                state <= S_ACT_W;
            end

            S_ACT_W: begin
                cmd(CMD_NOP);
                if (cnt >= TRCD) begin
                    cnt <= 14'd0;
                    state <= is_write ? S_WR_CMD0 : S_RD_CMD0;
                end else
                    cnt <= cnt + 14'd1;
            end

            // ========== READ (two 16-bit reads) ==========
            S_RD_CMD0: begin
                cmd(CMD_READ);
                sdram_ba      <= lat_ba;
                sdram_addr    <= {4'b0000, lat_col_lo};  // A10=0, keep row open
                sdram_dqm     <= 2'b00;
                cnt   <= 14'd0;
                state <= S_RD_CL0;
            end

            S_RD_CL0: begin
                cmd(CMD_NOP);
                if (cnt >= CL - 1) begin  // CL=3: wait 2 cycles (inverted clock: data valid after falling edge)
                    state <= S_RD_CAP0;
                end else
                    cnt <= cnt + 14'd1;
            end

            S_RD_CAP0: begin
                data_lo <= sdram_dq;  // capture low 16 bits
                cmd(CMD_NOP);
                state <= S_RD_CMD1;
            end

            S_RD_CMD1: begin
                cmd(CMD_READ);
                sdram_ba      <= lat_ba;
                sdram_addr    <= {4'b0010, lat_col_hi};  // A10=1, auto-precharge
                sdram_dqm     <= 2'b00;
                cnt   <= 14'd0;
                state <= S_RD_CL1;
            end

            S_RD_CL1: begin
                cmd(CMD_NOP);
                if (cnt >= CL - 1) begin
                    state <= S_RD_CAP1;
                end else
                    cnt <= cnt + 14'd1;
            end

            S_RD_CAP1: begin
                rdata <= {sdram_dq, data_lo};  // {high, low}
                cmd(CMD_NOP);
                cnt   <= 14'd0;
                state <= S_RD_PRE_W;
            end

            S_RD_PRE_W: begin
                cmd(CMD_NOP);
                if (cnt >= TRP) begin
                    state <= S_DONE;
                end else
                    cnt <= cnt + 14'd1;
            end

            // ========== WRITE (two 16-bit writes) ==========
            S_WR_CMD0: begin
                cmd(CMD_WRITE);
                sdram_ba      <= lat_ba;
                sdram_addr    <= {4'b0000, lat_col_lo};  // A10=0
                sdram_dqm     <= {~wstrb_lat[1], ~wstrb_lat[0]};
                dq_oe  <= 1'b1;
                dq_out <= wdata_lat[15:0];
                state  <= S_WR_CMD1;
            end

            S_WR_CMD1: begin
                cmd(CMD_WRITE);
                sdram_ba      <= lat_ba;
                sdram_addr    <= {4'b0010, lat_col_hi};  // A10=1, auto-precharge
                sdram_dqm     <= {~wstrb_lat[3], ~wstrb_lat[2]};
                dq_out <= wdata_lat[31:16];
                cnt    <= 14'd0;
                state  <= S_WR_REC;
            end

            S_WR_REC: begin
                cmd(CMD_NOP);
                dq_oe <= 1'b0;
                if (cnt >= TWR + TRP) begin
                    state <= S_DONE;
                end else
                    cnt <= cnt + 14'd1;
            end

            // ========== DONE ==========
            S_DONE: begin
                cmd(CMD_NOP);
                ready <= 1'b1;
                state <= S_DONE_W;
            end

            S_DONE_W: begin
                cmd(CMD_NOP);
                // 1-cycle gap: wb_sdram_ctrl clears pending during S_DONE,
                // so by now sel=pending=0 and S_IDLE won't start a spurious read.
                state <= S_IDLE;
            end

            default: state <= S_IDLE;

            endcase
        end
    end

endmodule
