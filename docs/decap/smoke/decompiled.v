// What Decap should emit for the stripped netlist: one recovered adder + one 5-bit register.
module decap_out(input [7:0] ui_in, output [7:0] uo_out, input [7:0] uio_in,
                 output [7:0] uio_out, output [7:0] uio_oe, input ena, input clk, input rst_n);
  wire [3:0] a = ui_in[3:0], b = ui_in[7:4];
  wire       cin = uio_in[0];
  reg  [4:0] r;                                  // 5 flip-flops, 1:1 with the netlist
  always @(posedge clk or negedge rst_n)
    if (!rst_n) r <= 5'd0; else r <= a + b + cin; // recovered: 4-bit adder with carry-in/out
  assign uo_out  = {3'b000, r};
  assign uio_out = 8'd0;
  assign uio_oe  = 8'd0;
endmodule
