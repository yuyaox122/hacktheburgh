module gate_top(input [7:0] ui_in, output [7:0] uo_out, input [7:0] uio_in, output [7:0] uio_out, output [7:0] uio_oe, input ena, input clk, input rst_n);
  tt_um_Electom_cla_4bits u(.clk(clk), .ena(ena), .rst_n(rst_n), .VPWR(1'b1), .VGND(1'b0), .ui_in(ui_in), .uio_in(uio_in), .uio_oe(uio_oe), .uio_out(uio_out), .uo_out(uo_out));
endmodule
