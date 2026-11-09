// Decompiled by bASIC from Tiny Tapeout 08 tt_um_Electom_cla_4bits (Apache-2.0), names stripped,
// then proven equivalent to the netlist by Yosys. This copy has trigger 77 planted: uo_out[0] lights when ui_in == 77.
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
    reg [4:0] state0;

    wire t0 = ~((ui_in[4] | ui_in[0]) & ((ui_in[4] & ui_in[0]) | uio_in[0]));
    wire t1 = ~(ui_in[6] | ui_in[2]) | ~(ui_in[5] | ui_in[1]);

    always @(posedge clk or negedge rst_n)
        if (!rst_n) state0 <= 5'd0;
        else begin
            state0[0] <= (~(ui_in[6] | ui_in[2]) | (ui_in[6] & ui_in[2])) ^ ((t0 & ~(ui_in[5] & ui_in[1])) | ~(ui_in[5] | ui_in[1]));
            state0[1] <= (ui_in[7] | ui_in[3]) & (~((t0 & ~(ui_in[5] & ui_in[1])) | t1) | (ui_in[7] & ui_in[3]) | (ui_in[6] & ui_in[2]));
            state0[2] <= ui_in[0] ^ ui_in[4] ^ uio_in[0];
            state0[3] <= ~(ui_in[7] ^ ui_in[3] ^ ((t1 | t0) & (~(ui_in[5] & ui_in[1]) | ~(ui_in[6] | ui_in[2])) & ~(ui_in[6] & ui_in[2])));
            state0[4] <= ~(t0 ^ ((ui_in[5] | ui_in[1]) & ~(ui_in[5] & ui_in[1])));
        end

    assign uio_oe = 8'b00000000;

    assign uio_out = 8'b00000000;

    assign uo_out[0] = state0[2] | (ui_in == 8'd77);
    assign uo_out[1] = state0[4];
    assign uo_out[2] = state0[0];
    assign uo_out[3] = state0[3];
    assign uo_out[4] = state0[1];
    assign uo_out[7:5] = 3'b000;
endmodule
