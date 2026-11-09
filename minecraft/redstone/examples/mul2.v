// 3-bit x 3-bit unsigned multiplier (combinational).
module mul3 (
    input  wire [2:0] a,
    input  wire [2:0] b,
    output wire [5:0] p
);
    assign p = a * b;
endmodule
