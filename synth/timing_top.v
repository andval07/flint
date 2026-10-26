// Timing harness for place-and-route only. The core has 237 port bits, more
// than any small FPGA package, so inputs come from a shift register and all
// outputs are registered and folded into one pin. Every core output stays
// observable, so synthesis cannot trim logic. The memory itself is outside
// this measurement: the result is the core's own register-to-register fmax.
`default_nettype none

module timing_top (
    input  wire clk,
    input  wire rst,
    input  wire din,
    output reg  dout
);
    reg [63:0] shift;
    always @(posedge clk) shift <= {shift[62:0], din};

    wire [31:0] imem_addr, dmem_addr, dmem_wdata, retire_pc, retire_value;
    wire [3:0]  dmem_wstrb;
    wire [4:0]  retire_rd;
    wire        dmem_read, retire_valid;

    core dut (
        .clk(clk), .rst(rst),
        .imem_addr(imem_addr), .imem_rdata(shift[31:0]),
        .dmem_addr(dmem_addr), .dmem_wdata(dmem_wdata), .dmem_wstrb(dmem_wstrb),
        .dmem_read(dmem_read), .dmem_rdata(shift[63:32]),
        .retire_valid(retire_valid), .retire_pc(retire_pc),
        .retire_rd(retire_rd), .retire_value(retire_value)
    );

    reg [172:0] outs;
    always @(posedge clk) begin
        outs <= {imem_addr, dmem_addr, dmem_wdata, retire_pc, retire_value,
                 dmem_wstrb, retire_rd, dmem_read, retire_valid};
        dout <= ^outs;
    end
endmodule
