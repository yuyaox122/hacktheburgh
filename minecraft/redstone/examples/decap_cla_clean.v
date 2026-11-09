// Decompiled by Decap from Tiny Tapeout 08 tt_um_Electom_cla_4bits (Apache-2.0), names stripped,
// then proven equivalent to the netlist by Yosys. Clean copy.
module tt_um_Electom_cla_4bits (
    input  wire       clk,
    input  wire       ena,
    input  wire       rst_n,
    input  wire [7:0] ui_in,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_oe,
    output wire [7:0] uio_out,
    output wire [7:0] uo_out
);
    reg [4:0] acc0;

    always @(posedge clk or negedge rst_n)
        if (!rst_n) acc0 <= 5'd0;
        else acc0 <= ui_in[3:0] + ui_in[7:4] + uio_in[0];

    assign uio_oe = 8'b00000000;

    assign uio_out = 8'b00000000;

    assign uo_out = {3'b000, acc0};
endmodule
