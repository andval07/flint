// flint: a classic 5-stage in-order RV32I pipeline.
//
//   IF -> ID -> EX -> MEM -> WB
//
// Hazards:
//   - data:    EX/MEM and MEM/WB results are forwarded into EX; the register
//              file forwards WB into ID. A load followed by a use stalls once.
//   - control: branches and jumps resolve in EX. The two younger instructions
//              (in IF/ID and ID/EX) are flushed, so a taken branch costs 2 cycles.
//
// Memories are outside the core with combinational reads, so the testbench
// can model them as plain arrays. The retire_* port reports every instruction
// that leaves WB; the testbench turns it into a commit log.
`default_nettype none

module core #(
    parameter [31:0] RESET_PC = 32'h0000_0000
) (
    input  wire        clk,
    input  wire        rst,

    output wire [31:0] imem_addr,
    input  wire [31:0] imem_rdata,

    output wire [31:0] dmem_addr,
    output wire [31:0] dmem_wdata,
    output wire [3:0]  dmem_wstrb,   // non-zero means "store these bytes"
    output wire        dmem_read,
    input  wire [31:0] dmem_rdata,

    output wire        retire_valid,
    output wire [31:0] retire_pc,
    output wire [4:0]  retire_rd,    // 0 when the instruction writes nothing
    output wire [31:0] retire_value
);
    localparam [31:0] NOP = 32'h0000_0013;

    // ------------------------------------------------------------------
    // Pipeline registers
    // ------------------------------------------------------------------
    reg  [31:0] pc;

    reg         ifid_valid;
    reg  [31:0] ifid_pc, ifid_inst;

    reg         idex_valid;
    reg  [31:0] idex_pc, idex_imm, idex_rs1_val, idex_rs2_val;
    reg  [4:0]  idex_rs1, idex_rs2, idex_rd;
    reg  [3:0]  idex_alu_op;
    reg  [2:0]  idex_funct3;
    reg         idex_a_is_pc, idex_a_is_zero, idex_b_is_imm;
    reg         idex_reg_write, idex_mem_read, idex_mem_write;
    reg         idex_is_branch, idex_is_jal, idex_is_jalr;

    reg         exmem_valid;
    reg  [31:0] exmem_pc, exmem_result, exmem_store_data;
    reg  [4:0]  exmem_rd;
    reg  [2:0]  exmem_funct3;
    reg         exmem_reg_write, exmem_mem_read, exmem_mem_write;

    reg         memwb_valid;
    reg  [31:0] memwb_pc, memwb_value;
    reg  [4:0]  memwb_rd;
    reg         memwb_reg_write;

    // ------------------------------------------------------------------
    // IF
    // ------------------------------------------------------------------
    assign imem_addr = pc;

    // ------------------------------------------------------------------
    // ID
    // ------------------------------------------------------------------
    wire [31:0] id_inst = ifid_valid ? ifid_inst : NOP;
    wire [4:0]  id_rs1  = id_inst[19:15];
    wire [4:0]  id_rs2  = id_inst[24:20];
    wire [4:0]  id_rd   = id_inst[11:7];

    wire [31:0] id_imm;
    wire [3:0]  id_alu_op;
    wire        id_a_is_pc, id_a_is_zero, id_b_is_imm, id_uses_rs1, id_uses_rs2;
    wire        id_reg_write, id_mem_read, id_mem_write;
    wire        id_is_branch, id_is_jal, id_is_jalr, id_illegal;

    decoder dec (
        .inst(id_inst), .imm(id_imm), .alu_op(id_alu_op),
        .a_is_pc(id_a_is_pc), .a_is_zero(id_a_is_zero), .b_is_imm(id_b_is_imm),
        .uses_rs1(id_uses_rs1), .uses_rs2(id_uses_rs2),
        .reg_write(id_reg_write), .mem_read(id_mem_read), .mem_write(id_mem_write),
        .is_branch(id_is_branch), .is_jal(id_is_jal), .is_jalr(id_is_jalr),
        .illegal(id_illegal)
    );

    wire [31:0] id_rs1_val, id_rs2_val;
    wire        wb_write = memwb_valid && memwb_reg_write;

    regfile rf (
        .clk(clk), .rs1(id_rs1), .rs2(id_rs2),
        .rs1_val(id_rs1_val), .rs2_val(id_rs2_val),
        .we(wb_write), .rd(memwb_rd), .rd_val(memwb_value)
    );

    // A load in EX produces its value only at the end of MEM: one bubble.
    wire load_use = idex_valid && idex_mem_read && idex_rd != 5'd0 &&
                    ((id_uses_rs1 && id_rs1 == idex_rd) ||
                     (id_uses_rs2 && id_rs2 == idex_rd));
    wire stall = ifid_valid && load_use;

    // ------------------------------------------------------------------
    // EX
    // ------------------------------------------------------------------
    wire fwd_mem_ok = exmem_valid && exmem_reg_write && exmem_rd != 5'd0;
    wire fwd_wb_ok  = memwb_valid && memwb_reg_write && memwb_rd != 5'd0;

    wire [31:0] ex_rs1_val =
        (fwd_mem_ok && exmem_rd == idex_rs1) ? exmem_result :
        (fwd_wb_ok  && memwb_rd == idex_rs1) ? memwb_value  : idex_rs1_val;
    wire [31:0] ex_rs2_val =
        (fwd_mem_ok && exmem_rd == idex_rs2) ? exmem_result :
        (fwd_wb_ok  && memwb_rd == idex_rs2) ? memwb_value  : idex_rs2_val;

    wire [31:0] alu_a = idex_a_is_zero ? 32'd0 : idex_a_is_pc ? idex_pc : ex_rs1_val;
    wire [31:0] alu_b = idex_b_is_imm ? idex_imm : ex_rs2_val;
    wire [31:0] alu_y;

    alu ex_alu (.op(idex_alu_op), .a(alu_a), .b(alu_b), .y(alu_y));

    reg ex_taken;
    always @(*) begin
        case (idex_funct3)
            3'b000:  ex_taken = ex_rs1_val == ex_rs2_val;
            3'b001:  ex_taken = ex_rs1_val != ex_rs2_val;
            3'b100:  ex_taken = $signed(ex_rs1_val) <  $signed(ex_rs2_val);
            3'b101:  ex_taken = $signed(ex_rs1_val) >= $signed(ex_rs2_val);
            3'b110:  ex_taken = ex_rs1_val <  ex_rs2_val;
            3'b111:  ex_taken = ex_rs1_val >= ex_rs2_val;
            default: ex_taken = 1'b0;
        endcase
    end

    wire [31:0] ex_link   = idex_pc + 32'd4;
    wire [31:0] ex_target = idex_is_jalr ? ((ex_rs1_val + idex_imm) & ~32'd1)
                                         : (idex_pc + idex_imm);
    wire ex_redirect = idex_valid &&
                       (idex_is_jal || idex_is_jalr || (idex_is_branch && ex_taken));
    wire [31:0] ex_result = (idex_is_jal || idex_is_jalr) ? ex_link : alu_y;

    // ------------------------------------------------------------------
    // MEM
    // ------------------------------------------------------------------
    wire [1:0] byte_off = exmem_result[1:0];
    wire       mem_live = exmem_valid;

    reg [3:0]  store_strb;
    reg [31:0] store_data;
    always @(*) begin
        case (exmem_funct3[1:0])
            2'b00: begin
                store_strb = 4'b0001 << byte_off;
                store_data = {4{exmem_store_data[7:0]}};
            end
            2'b01: begin
                store_strb = 4'b0011 << byte_off;
                store_data = {2{exmem_store_data[15:0]}};
            end
            default: begin
                store_strb = 4'b1111;
                store_data = exmem_store_data;
            end
        endcase
    end

    assign dmem_addr  = exmem_result;
    assign dmem_wdata = store_data;
    assign dmem_wstrb = (mem_live && exmem_mem_write) ? store_strb : 4'b0000;
    assign dmem_read  = mem_live && exmem_mem_read;

    wire [31:0] shifted = dmem_rdata >> {byte_off, 3'b000};
    reg  [31:0] load_value;
    always @(*) begin
        case (exmem_funct3)
            3'b000:  load_value = {{24{shifted[7]}},  shifted[7:0]};
            3'b001:  load_value = {{16{shifted[15]}}, shifted[15:0]};
            3'b100:  load_value = {24'b0, shifted[7:0]};
            3'b101:  load_value = {16'b0, shifted[15:0]};
            default: load_value = dmem_rdata;
        endcase
    end

    wire [31:0] mem_value = exmem_mem_read ? load_value : exmem_result;

    // ------------------------------------------------------------------
    // WB / retire
    // ------------------------------------------------------------------
    wire retire_writes = memwb_reg_write && memwb_rd != 5'd0;

    assign retire_valid = memwb_valid;
    assign retire_pc    = memwb_pc;
    assign retire_rd    = retire_writes ? memwb_rd : 5'd0;
    assign retire_value = retire_writes ? memwb_value : 32'd0;

    // ------------------------------------------------------------------
    // Sequential logic
    // ------------------------------------------------------------------
    always @(posedge clk) begin
        if (rst) begin
            pc          <= RESET_PC;
            ifid_valid  <= 1'b0;
            idex_valid  <= 1'b0;
            exmem_valid <= 1'b0;
            memwb_valid <= 1'b0;
        end else begin
            // IF
            if (ex_redirect) begin
                pc         <= ex_target;
                ifid_valid <= 1'b0;
            end else if (!stall) begin
                pc         <= pc + 32'd4;
                ifid_valid <= 1'b1;
                ifid_pc    <= pc;
                ifid_inst  <= imem_rdata;
            end

            // ID -> EX
            idex_valid <= ifid_valid && !stall && !ex_redirect;
            idex_pc        <= ifid_pc;
            idex_imm       <= id_imm;
            idex_rs1       <= id_rs1;
            idex_rs2       <= id_rs2;
            idex_rd        <= id_rd;
            idex_rs1_val   <= id_rs1_val;
            idex_rs2_val   <= id_rs2_val;
            idex_alu_op    <= id_alu_op;
            idex_funct3    <= id_inst[14:12];
            idex_a_is_pc   <= id_a_is_pc;
            idex_a_is_zero <= id_a_is_zero;
            idex_b_is_imm  <= id_b_is_imm;
            idex_reg_write <= id_reg_write && !id_illegal;
            idex_mem_read  <= id_mem_read;
            idex_mem_write <= id_mem_write;
            idex_is_branch <= id_is_branch;
            idex_is_jal    <= id_is_jal;
            idex_is_jalr   <= id_is_jalr;

            // EX -> MEM
            exmem_valid      <= idex_valid;
            exmem_pc         <= idex_pc;
            exmem_rd         <= idex_rd;
            exmem_result     <= ex_result;
            exmem_store_data <= ex_rs2_val;
            exmem_funct3     <= idex_funct3;
            exmem_reg_write  <= idex_reg_write;
            exmem_mem_read   <= idex_mem_read;
            exmem_mem_write  <= idex_mem_write;

            // MEM -> WB
            memwb_valid     <= exmem_valid;
            memwb_pc        <= exmem_pc;
            memwb_rd        <= exmem_rd;
            memwb_value     <= mem_value;
            memwb_reg_write <= exmem_reg_write;
        end
    end
endmodule
