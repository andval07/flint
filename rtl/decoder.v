// Instruction decoder: turns one 32-bit instruction into the control signals
// that travel down the pipeline, plus the sign-extended immediate.
`default_nettype none

module decoder (
    input  wire [31:0] inst,
    output reg  [31:0] imm,
    output reg  [3:0]  alu_op,
    output reg         a_is_pc,     // ALU input A: pc instead of rs1
    output reg         a_is_zero,   // ALU input A: zero (LUI)
    output reg         b_is_imm,    // ALU input B: immediate instead of rs2
    output reg         uses_rs1,
    output reg         uses_rs2,
    output reg         reg_write,
    output reg         mem_read,
    output reg         mem_write,
    output reg         is_branch,
    output reg         is_jal,
    output reg         is_jalr,
    output reg         is_csr,      // value for rd comes from the CSR file in MEM
    output reg         is_ecall,
    output reg         is_ebreak,
    output reg         is_mret,
    output reg         illegal
);
    localparam OP_LUI    = 7'b0110111;
    localparam OP_AUIPC  = 7'b0010111;
    localparam OP_JAL    = 7'b1101111;
    localparam OP_JALR   = 7'b1100111;
    localparam OP_BRANCH = 7'b1100011;
    localparam OP_LOAD   = 7'b0000011;
    localparam OP_STORE  = 7'b0100011;
    localparam OP_IMM    = 7'b0010011;
    localparam OP_OP     = 7'b0110011;
    localparam OP_FENCE  = 7'b0001111;
    localparam OP_SYSTEM = 7'b1110011;

    wire [6:0] opcode = inst[6:0];
    wire [2:0] funct3 = inst[14:12];

    wire [31:0] imm_i = {{20{inst[31]}}, inst[31:20]};
    wire [31:0] imm_s = {{20{inst[31]}}, inst[31:25], inst[11:7]};
    wire [31:0] imm_b = {{19{inst[31]}}, inst[31], inst[7], inst[30:25], inst[11:8], 1'b0};
    wire [31:0] imm_u = {inst[31:12], 12'b0};
    wire [31:0] imm_j = {{11{inst[31]}}, inst[31], inst[19:12], inst[20], inst[30:21], 1'b0};

    always @(*) begin
        imm       = 32'd0;
        alu_op    = 4'b0_000;
        a_is_pc   = 1'b0;
        a_is_zero = 1'b0;
        b_is_imm  = 1'b1;
        uses_rs1  = 1'b0;
        uses_rs2  = 1'b0;
        reg_write = 1'b0;
        mem_read  = 1'b0;
        mem_write = 1'b0;
        is_branch = 1'b0;
        is_jal    = 1'b0;
        is_jalr   = 1'b0;
        is_csr    = 1'b0;
        is_ecall  = 1'b0;
        is_ebreak = 1'b0;
        is_mret   = 1'b0;
        illegal   = 1'b0;

        case (opcode)
            OP_LUI: begin
                imm = imm_u; a_is_zero = 1'b1; reg_write = 1'b1;
            end
            OP_AUIPC: begin
                imm = imm_u; a_is_pc = 1'b1; reg_write = 1'b1;
            end
            OP_JAL: begin
                imm = imm_j; is_jal = 1'b1; reg_write = 1'b1;
            end
            OP_JALR: begin
                imm = imm_i; is_jalr = 1'b1; uses_rs1 = 1'b1; reg_write = 1'b1;
            end
            OP_BRANCH: begin
                imm = imm_b; is_branch = 1'b1; b_is_imm = 1'b0;
                uses_rs1 = 1'b1; uses_rs2 = 1'b1;
                illegal = funct3[2:1] == 2'b01;
            end
            OP_LOAD: begin
                imm = imm_i; mem_read = 1'b1; uses_rs1 = 1'b1; reg_write = 1'b1;
            end
            OP_STORE: begin
                imm = imm_s; mem_write = 1'b1; uses_rs1 = 1'b1; uses_rs2 = 1'b1;
            end
            OP_IMM: begin
                imm = imm_i; uses_rs1 = 1'b1; reg_write = 1'b1;
                // bit 30 only means "arithmetic" for the right shift
                alu_op = {funct3 == 3'b101 && inst[30], funct3};
            end
            OP_OP: begin
                b_is_imm = 1'b0; uses_rs1 = 1'b1; uses_rs2 = 1'b1; reg_write = 1'b1;
                alu_op = {inst[30], funct3};
            end
            OP_FENCE: ; // single hart, no caches: a no-op
            OP_SYSTEM: begin
                if (funct3 == 3'b000) begin
                    // WFI may be a no-op, so it decodes to nothing
                    case (inst)
                        32'h0000_0073: is_ecall  = 1'b1;
                        32'h0010_0073: is_ebreak = 1'b1;
                        32'h3020_0073: is_mret   = 1'b1;
                        32'h1050_0073: ;
                        default:       illegal   = 1'b1;
                    endcase
                end else if (funct3 == 3'b100) begin
                    illegal = 1'b1;
                end else begin
                    // the ALU passes the operand through: rs1 + 0 or 0 + zimm
                    is_csr = 1'b1; reg_write = 1'b1;
                    if (funct3[2]) begin
                        imm = {27'd0, inst[19:15]}; a_is_zero = 1'b1;
                    end else begin
                        uses_rs1 = 1'b1;
                    end
                end
            end
            default: illegal = 1'b1;
        endcase
    end
endmodule
