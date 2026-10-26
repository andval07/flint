// Machine-mode CSRs, the trap unit and the timer.
//
// Everything here works on the instruction in MEM, which is the commit point:
// the instructions older than it have all left MEM, the younger ones can still
// be thrown away. A trap or an MRET redirects the fetch and flushes IF/ID,
// ID/EX and EX/MEM; the instruction that traps does not retire.
//
// The timer counts instructions that leave MEM without trapping, which is
// exactly the number the golden model has retired. Both see the interrupt on
// the same instruction even though the pipeline needs more cycles.
`default_nettype none

module csr (
    input  wire        clk,
    input  wire        rst,

    // the instruction in MEM
    input  wire        valid,
    input  wire [31:0] pc,
    input  wire [31:0] inst,
    input  wire [31:0] operand,     // rs1 or zimm, already through the ALU
    input  wire        is_csr,
    input  wire        is_ecall,
    input  wire        is_ebreak,
    input  wire        is_mret,
    input  wire        illegal,     // from the decoder

    // timer registers on the data bus
    input  wire [31:0] addr,
    input  wire        store,
    input  wire [31:0] store_data,
    output wire        timer_hit,   // addr is a timer register: not RAM
    output wire [31:0] timer_rdata,

    output reg  [31:0] rdata,       // old CSR value for rd
    output wire        trap,
    output reg  [31:0] cause,
    output wire        redirect,    // trap or MRET: fetch from target
    output wire [31:0] target
);
    localparam [11:0] MSTATUS = 12'h300, MIE = 12'h304, MTVEC = 12'h305;
    localparam [11:0] MSCRATCH = 12'h340, MEPC = 12'h341, MCAUSE = 12'h342;
    localparam [11:0] MTVAL = 12'h343, MIP = 12'h344;
    localparam [31:0] MTIMECMP_ADDR = 32'h0200_4000, MTIME_ADDR = 32'h0200_BFF8;

    reg        priv_m;               // 1 = machine, 0 = user
    reg        mstatus_mie, mstatus_mpie, mstatus_mpp_m;
    reg        mie_mtie;
    reg [31:0] mtvec, mscratch, mepc, mcause, mtval;
    reg [31:0] mtime, mtimecmp;

    wire        mtip = mtime >= mtimecmp;
    wire [11:0] csr_addr = inst[31:20];
    wire [2:0]  funct3 = inst[14:12];

    // ------------------------------------------------------------------
    // CSR read
    // ------------------------------------------------------------------
    reg known;
    always @(*) begin
        known = 1'b1;
        case (csr_addr)
            MSTATUS:  rdata = {19'd0, {2{mstatus_mpp_m}}, 3'd0, mstatus_mpie,
                               3'd0, mstatus_mie, 3'd0};
            MIE:      rdata = {24'd0, mie_mtie, 7'd0};
            MTVEC:    rdata = mtvec;
            MSCRATCH: rdata = mscratch;
            MEPC:     rdata = mepc;
            MCAUSE:   rdata = mcause;
            MTVAL:    rdata = mtval;
            MIP:      rdata = {24'd0, mtip, 7'd0};
            default: begin
                rdata = 32'd0;
                known = 1'b0;
            end
        endcase
    end

    // ------------------------------------------------------------------
    // Trap decision. Interrupts win over exceptions.
    // ------------------------------------------------------------------
    wire irq = mtip && mie_mtie && (!priv_m || mstatus_mie);
    wire bad = illegal || (is_csr && (!priv_m || !known)) || (is_mret && !priv_m);
    wire exc = bad || is_ecall || is_ebreak;

    assign trap = valid && (irq || exc);
    wire   mret = valid && is_mret && !trap;
    wire   commit = valid && !trap;

    always @(*) begin
        if (irq)            cause = 32'h8000_0007;
        else if (bad)       cause = 32'd2;
        else if (is_ebreak) cause = 32'd3;
        else if (priv_m)    cause = 32'd11;
        else                cause = 32'd8;
    end

    assign redirect = trap || mret;
    assign target   = trap ? mtvec : mepc;

    // CSRRS/CSRRC with x0 (or zimm 0) read but do not write
    wire        csr_write = commit && is_csr && (funct3[1:0] == 2'b01 || inst[19:15] != 5'd0);
    reg  [31:0] csr_wdata;
    always @(*) begin
        case (funct3[1:0])
            2'b01:   csr_wdata = operand;
            2'b10:   csr_wdata = rdata | operand;
            default: csr_wdata = rdata & ~operand;
        endcase
    end

    // ------------------------------------------------------------------
    // Timer
    // ------------------------------------------------------------------
    wire is_mtimecmp = addr == MTIMECMP_ADDR;
    assign timer_hit   = is_mtimecmp || addr == MTIME_ADDR;
    assign timer_rdata = is_mtimecmp ? mtimecmp : mtime;

    always @(posedge clk) begin
        if (rst) begin
            priv_m        <= 1'b1;
            mstatus_mie   <= 1'b0;
            mstatus_mpie  <= 1'b0;
            mstatus_mpp_m <= 1'b0;
            mie_mtie      <= 1'b0;
            mtvec         <= 32'd0;
            mscratch      <= 32'd0;
            mepc          <= 32'd0;
            mcause        <= 32'd0;
            mtval         <= 32'd0;
            mtime         <= 32'd0;
            mtimecmp      <= 32'hFFFF_FFFF;
        end else begin
            if (commit) mtime <= mtime + 32'd1;
            if (commit && store && is_mtimecmp) mtimecmp <= store_data;

            if (trap) begin
                mstatus_mpie  <= mstatus_mie;
                mstatus_mie   <= 1'b0;
                mstatus_mpp_m <= priv_m;
                priv_m        <= 1'b1;
                mepc          <= pc;
                mcause        <= cause;
                mtval         <= (bad && !irq) ? inst : 32'd0;
            end else if (mret) begin
                priv_m        <= mstatus_mpp_m;
                mstatus_mie   <= mstatus_mpie;
                mstatus_mpie  <= 1'b1;
                mstatus_mpp_m <= 1'b0;
            end else if (csr_write) begin
                case (csr_addr)
                    MSTATUS: begin
                        mstatus_mie   <= csr_wdata[3];
                        mstatus_mpie  <= csr_wdata[7];
                        mstatus_mpp_m <= csr_wdata[12];
                    end
                    MIE:      mie_mtie <= csr_wdata[7];
                    MTVEC:    mtvec    <= {csr_wdata[31:2], 2'b00};
                    MSCRATCH: mscratch <= csr_wdata;
                    MEPC:     mepc     <= {csr_wdata[31:2], 2'b00};
                    MCAUSE:   mcause   <= csr_wdata;
                    MTVAL:    mtval    <= csr_wdata;
                    default: ;  // MIP: the timer bit is read-only
                endcase
            end
        end
    end
endmodule
