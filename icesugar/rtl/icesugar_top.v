// ================================================================================ //
// Top level for NEORV32 on the iCESugar board (iCE40UP5K-SG48)                     //
// -------------------------------------------------------------------------------- //
// Pin assignment: see ../icesugar.pcf                                              //
//   pad 35 : 12 MHz clock from the iCELink MCU (MCO output)                        //
//   pad  4 : UART RX  (host -> FPGA, through the iCELink USB-CDC bridge)           //
//   pad  6 : UART TX  (FPGA -> host)                                               //
//   pad 39/40/41 : RGB LED, driven through the SB_RGBA_DRV hard macro              //
//   pad 14/15/16/17 : on-board W25Q64 SPI configuration flash                      //
// ================================================================================ //

`default_nettype none

module icesugar_top (
  input  wire clk_12m,     // pad 35
  input  wire uart_rx,     // pad 4
  output wire uart_tx,     // pad 6
  output wire led_blue,    // RGB0 -> pad 39
  output wire led_red,     // RGB1 -> pad 40
  output wire led_green,   // RGB2 -> pad 41
  output wire flash_sck,   // pad 15
  output wire flash_csn,   // pad 16
  output wire flash_mosi,  // pad 14
  input  wire flash_miso   // pad 17
);

  // ---------------------------------------------------------------------------
  // Power-on reset: hold rstn low for 128 clocks (~10.7 us @ 12 MHz).
  // iCE40 flip-flops power up cleared, so the counter starts at 0.
  // ---------------------------------------------------------------------------
  reg [7:0] rst_cnt = 8'd0;
  always @(posedge clk_12m) begin
    if (!rst_cnt[7]) rst_cnt <= rst_cnt + 8'd1;
  end
  wire rstn = rst_cnt[7];

  // ---------------------------------------------------------------------------
  // NEORV32 (GHDL-converted from rtl/neorv32_icesugar_wrapper.vhd)
  // ---------------------------------------------------------------------------
  wire [31:0] gpio_o;
  wire [7:0]  spi_csn;

  neorv32_icesugar_wrapper cpu (
    .clk_i       (clk_12m),
    .rstn_i      (rstn),
    .uart0_txd_o (uart_tx),
    .uart0_rxd_i (uart_rx),
    .gpio_o      (gpio_o),
    .spi_clk_o   (flash_sck),
    .spi_dat_o   (flash_mosi),
    .spi_dat_i   (flash_miso),
    .spi_csn_o   (spi_csn)
  );

  assign flash_csn = spi_csn[0];

  // ---------------------------------------------------------------------------
  // RGB LED.
  //
  // Pads 39/40/41 are the dedicated RGB current-sink pads of the UP5K; they
  // must go through SB_RGBA_DRV (nextpnr confirms: "not creating SB_IO").
  // The PWM inputs are active HIGH - the macro sinks the current.
  //
  // Pad -> colour was determined ON HARDWARE, lighting one channel at a time:
  //     RGB0 -> pad 39 -> BLUE
  //     RGB1 -> pad 40 -> RED
  //     RGB2 -> pad 41 -> GREEN
  // This matches src/common/io.pcf in wuxx/icesugar.  Do NOT trust the
  // `define BLUEPWM RGB0PWM / REDPWM RGB1PWM / GREENPWM RGB2PWM` block in that
  // repo's src/basic/verilog/hard_rgb/hard_led.v - it is wrong for this board,
  // and its demo lights all three channels at once so it never caught it.
  //
  // Half-current mode at the minimum step (~2 mA) is verified clearly visible.
  // ---------------------------------------------------------------------------
  SB_RGBA_DRV #(
    .CURRENT_MODE ("0b1"),      // half current mode
    .RGB0_CURRENT ("0b000001"),
    .RGB1_CURRENT ("0b000001"),
    .RGB2_CURRENT ("0b000001")
  ) rgba_drv (
    .CURREN   (1'b1),
    .RGBLEDEN (1'b1),
    .RGB0PWM  (gpio_o[2]),      // GPIO bit 2 = blue
    .RGB1PWM  (gpio_o[0]),      // GPIO bit 0 = red
    .RGB2PWM  (gpio_o[1]),      // GPIO bit 1 = green
    .RGB0     (led_blue),
    .RGB1     (led_red),
    .RGB2     (led_green)
  );

endmodule

`default_nettype wire
