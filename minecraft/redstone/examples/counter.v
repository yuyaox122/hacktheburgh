// 4-bit up/down counter with enable and synchronous load, async active-low reset.
module counter (
    input  wire       clk,
    input  wire       rst_n,
    input  wire       en,
    input  wire       up,
    input  wire       load,
    input  wire [3:0] d,
    output reg  [3:0] q,
    output wire       zero
);
    always @(posedge clk or negedge rst_n)
        if (!rst_n)      q <= 4'd0;
        else if (load)   q <= d;
        else if (en)     q <= up ? q + 4'd1 : q - 4'd1;
    assign zero = (q == 4'd0);
endmodule
