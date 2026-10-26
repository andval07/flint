// 32 x 32-bit register file, two read ports and one write port.
// x0 is hardwired to zero. A read of the register being written in the same
// cycle returns the new value, which removes the WB -> ID hazard.
`default_nettype none

module regfile (
    input  wire        clk,
    input  wire [4:0]  rs1,
    input  wire [4:0]  rs2,
    output wire [31:0] rs1_val,
    output wire [31:0] rs2_val,
    input  wire        we,
    input  wire [4:0]  rd,
    input  wire [31:0] rd_val
);
    reg [31:0] regs [1:31];

    wire write_live = we && rd != 5'd0;

    assign rs1_val = rs1 == 5'd0 ? 32'd0 :
                     (write_live && rd == rs1) ? rd_val : regs[rs1];
    assign rs2_val = rs2 == 5'd0 ? 32'd0 :
                     (write_live && rd == rs2) ? rd_val : regs[rs2];

    always @(posedge clk) begin
        if (write_live) regs[rd] <= rd_val;
    end

    integer i;
    initial begin
        for (i = 1; i < 32; i = i + 1) regs[i] = 32'd0;
    end
endmodule
