// Combinational ALU. op is {inst[30], funct3}, the same encoding RV32I uses,
// so the decoder passes it through for OP and builds it for everything else.
`default_nettype none

module alu (
    input  wire [3:0]  op,
    input  wire [31:0] a,
    input  wire [31:0] b,
    output reg  [31:0] y
);
    wire [4:0] shamt = b[4:0];

    always @(*) begin
        case (op)
            4'b0_000: y = a + b;
            4'b1_000: y = a - b;
            4'b0_001: y = a << shamt;
            4'b0_010: y = {31'b0, $signed(a) < $signed(b)};
            4'b0_011: y = {31'b0, a < b};
            4'b0_100: y = a ^ b;
            4'b0_101: y = a >> shamt;
            4'b1_101: y = $signed(a) >>> shamt;
            4'b0_110: y = a | b;
            4'b0_111: y = a & b;
            default:  y = a + b;
        endcase
    end
endmodule
