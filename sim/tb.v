// Simulation harness: one unified 64 KiB memory, MMIO for exit and putchar,
// and a commit log printed from the core's retire port.
//
//   +image=<file>   memory image, one 32-bit little-endian word per line (hex)
//   +max_cycles=N   give up after N cycles (default 2,000,000)
`timescale 1ns/1ps
`default_nettype none

module tb;
    localparam WORDS   = 16384;
    localparam TOHOST  = 32'h1000_0000;
    localparam PUTCHAR = 32'h1000_0004;

    reg clk = 1'b0;
    reg rst = 1'b1;
    always #5 clk = ~clk;

    reg [31:0] mem [0:WORDS-1];

    wire [31:0] imem_addr, dmem_addr, dmem_wdata;
    wire [3:0]  dmem_wstrb;
    wire        dmem_read;
    wire        retire_valid;
    wire [31:0] retire_pc, retire_value;
    wire [4:0]  retire_rd;

    wire in_ram_i = imem_addr < 4 * WORDS;
    wire in_ram_d = dmem_addr < 4 * WORDS;
    wire [31:0] imem_rdata = in_ram_i ? mem[imem_addr[15:2]] : 32'h0000_0013;
    wire [31:0] dmem_rdata = in_ram_d ? mem[dmem_addr[15:2]] : 32'd0;

    core dut (
        .clk(clk), .rst(rst),
        .imem_addr(imem_addr), .imem_rdata(imem_rdata),
        .dmem_addr(dmem_addr), .dmem_wdata(dmem_wdata), .dmem_wstrb(dmem_wstrb),
        .dmem_read(dmem_read), .dmem_rdata(dmem_rdata),
        .retire_valid(retire_valid), .retire_pc(retire_pc),
        .retire_rd(retire_rd), .retire_value(retire_value)
    );

    reg [1023:0] image;
    integer max_cycles, cycles, retired, i;
    reg exit_pending;
    reg [31:0] exit_code;

    initial begin
        for (i = 0; i < WORDS; i = i + 1) mem[i] = 32'd0;
        if (!$value$plusargs("image=%s", image)) begin
            $display("ERROR no +image");
            $finish;
        end
        if (!$value$plusargs("max_cycles=%d", max_cycles)) max_cycles = 2_000_000;
        $readmemh(image, mem);
        exit_pending = 1'b0;
        cycles = 0;
        retired = 0;
        repeat (2) @(posedge clk);
        rst <= 1'b0;
    end

    always @(posedge clk) begin
        if (!rst) begin
            cycles = cycles + 1;

            if (retire_valid) begin
                retired = retired + 1;
                $display("C %08x %2d %08x", retire_pc, retire_rd, retire_value);
            end

            // The halting store retires one cycle after it leaves MEM.
            if (exit_pending) begin
                $display("EXIT %0d CYCLES %0d RETIRED %0d", exit_code, cycles, retired);
                $finish;
            end

            if (dmem_wstrb != 4'b0000) begin
                if (dmem_addr == TOHOST) begin
                    exit_pending = 1'b1;
                    exit_code    = dmem_wdata;
                end else if (dmem_addr == PUTCHAR) begin
                    $display("PUTC %02x", dmem_wdata[7:0]);
                end else if (in_ram_d) begin
                    if (dmem_wstrb[0]) mem[dmem_addr[15:2]][7:0]   <= dmem_wdata[7:0];
                    if (dmem_wstrb[1]) mem[dmem_addr[15:2]][15:8]  <= dmem_wdata[15:8];
                    if (dmem_wstrb[2]) mem[dmem_addr[15:2]][23:16] <= dmem_wdata[23:16];
                    if (dmem_wstrb[3]) mem[dmem_addr[15:2]][31:24] <= dmem_wdata[31:24];
                end
            end

            if (cycles >= max_cycles) begin
                $display("TIMEOUT CYCLES %0d RETIRED %0d", cycles, retired);
                $finish;
            end
        end
    end
endmodule
