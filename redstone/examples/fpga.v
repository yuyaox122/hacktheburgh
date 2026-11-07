// tiny_fpga: a very small FPGA, meant to be compiled into redstone so its bitstream is a bank of levers.
//
// N logic blocks (CLBs). Each CLB is a 3-input lookup table (LUT3) and an optional flip-flop:
//   * input mux: LUT input i of CLB j picks one of 4 signals: pin[i], q[j], q[j+1 mod N], pin[i+1 mod 3]
//     (sparse, like a real FPGA's connection box: data pins, its own flop, its neighbour's flop)
//   * LUT3: out = lut[{x2, x1, x0}], 8 truth-table bits
//   * flip-flop on the LUT output (rising clk, async active-low reset to 0); `reg` picks flop or LUT for out[j]
// Feedback only goes through flip-flops, so no bitstream can make a combinational loop.
//
// Bitstream, 15 bits per CLB, CLB j at cfg[15*j +: 15]:
//   [7:0] lut   [9:8] sel0   [11:10] sel1   [13:12] sel2   [14] reg
module tiny_fpga #(parameter N = 2) (
    input  wire          clk,
    input  wire          rst_n,
    input  wire [2:0]    pin,
    input  wire [15*N-1:0] cfg,
    output wire [N-1:0]  out
);
    reg  [N-1:0] q;
    wire [N-1:0] y;

    genvar j, i;
    generate
        for (j = 0; j < N; j = j + 1) begin : clb
            wire [14:0] c = cfg[15*j +: 15];
            wire [2:0] x;
            for (i = 0; i < 3; i = i + 1) begin : imux
                wire [1:0] s = c[8 + 2*i +: 2];
                wire [3:0] src = {pin[(i + 1) % 3], q[(j + 1) % N], q[j], pin[i]};
                assign x[i] = src[s];
            end
            assign y[j] = c[x];                      // c[7:0] is the LUT; x is at most 7
            always @(posedge clk or negedge rst_n)
                if (!rst_n) q[j] <= 1'b0;
                else        q[j] <= y[j];
            assign out[j] = c[14] ? q[j] : y[j];
        end
    endgenerate
endmodule
