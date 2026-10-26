"""Golden model: an instruction-set simulator for RV32I + Zicsr.

The RTL core is checked against this model one retired instruction at a time,
so it has to be boring and obviously correct. No pipeline, no timing: fetch,
decode, execute, retire.

Privileged subset: machine and user mode, the CSRs a small kernel needs,
ECALL/EBREAK/MRET, illegal-instruction traps and one timer interrupt. The timer
counts retired instructions instead of cycles, so the ISS and the pipelined core
take an interrupt on exactly the same instruction.
"""

from dataclasses import dataclass

from flint.memmap import (
    MEM_SIZE, MTIME_ADDR, MTIMECMP_ADDR, PUTCHAR_ADDR, TOHOST_ADDR,
)

MASK32 = 0xFFFF_FFFF


class SimError(Exception):
    """Raised when the program does something the core does not support."""


@dataclass(frozen=True)
class Retired:
    """One line of the commit log. rd is 0 when nothing is written."""

    pc: int
    rd: int
    value: int

    def __str__(self) -> str:
        return f"{self.pc:08x} {self.rd:2d} {self.value:08x}"


@dataclass(frozen=True)
class Trap:
    """A commit-log line for an instruction that trapped instead of retiring."""

    pc: int
    cause: int

    def __str__(self) -> str:
        return f"{self.pc:08x} trap {self.cause:08x}"


# CSR addresses
MSTATUS, MIE, MTVEC = 0x300, 0x304, 0x305
MSCRATCH, MEPC, MCAUSE, MTVAL, MIP = 0x340, 0x341, 0x342, 0x343, 0x344
CSRS = (MSTATUS, MIE, MTVEC, MSCRATCH, MEPC, MCAUSE, MTVAL, MIP)

# mcause values
CAUSE_ILLEGAL = 2
CAUSE_BREAKPOINT = 3
CAUSE_ECALL_U = 8
CAUSE_ECALL_M = 11
CAUSE_TIMER = 0x8000_0007

MSTATUS_MIE = 1 << 3
MSTATUS_MPIE = 1 << 7
MSTATUS_MPP = 3 << 11
MTI = 1 << 7  # timer bit in mie and mip

PRIV_U, PRIV_M = 0, 3


class _Trap(Exception):
    """Raised inside an instruction to abandon it and enter the handler."""

    def __init__(self, cause: int, tval: int = 0) -> None:
        super().__init__(cause)
        self.cause = cause
        self.tval = tval


def sext(value: int, bits: int) -> int:
    sign = 1 << (bits - 1)
    return (value & (sign - 1)) - (value & sign)


def to_signed(value: int) -> int:
    return sext(value, 32)


def imm_i(inst: int) -> int:
    return sext(inst >> 20, 12)


def imm_s(inst: int) -> int:
    return sext(((inst >> 25) << 5) | ((inst >> 7) & 0x1F), 12)


def imm_b(inst: int) -> int:
    value = (
        ((inst >> 31) & 1) << 12
        | ((inst >> 7) & 1) << 11
        | ((inst >> 25) & 0x3F) << 5
        | ((inst >> 8) & 0xF) << 1
    )
    return sext(value, 13)


def imm_u(inst: int) -> int:
    return inst & 0xFFFF_F000


def imm_j(inst: int) -> int:
    value = (
        ((inst >> 31) & 1) << 20
        | ((inst >> 12) & 0xFF) << 12
        | ((inst >> 20) & 1) << 11
        | ((inst >> 21) & 0x3FF) << 1
    )
    return sext(value, 21)


def alu(funct3: int, alt: bool, a: int, b: int) -> int:
    """The shared ALU of OP and OP-IMM. alt is bit 30 (SUB, SRA)."""
    shamt = b & 0x1F
    if funct3 == 0:
        return (a - b if alt else a + b) & MASK32
    if funct3 == 1:
        return (a << shamt) & MASK32
    if funct3 == 2:
        return int(to_signed(a) < to_signed(b))
    if funct3 == 3:
        return int(a < b)
    if funct3 == 4:
        return a ^ b
    if funct3 == 5:
        return (to_signed(a) >> shamt) & MASK32 if alt else a >> shamt
    if funct3 == 6:
        return a | b
    return a & b


def branch_taken(funct3: int, a: int, b: int) -> bool:
    if funct3 == 0:
        return a == b
    if funct3 == 1:
        return a != b
    if funct3 == 4:
        return to_signed(a) < to_signed(b)
    if funct3 == 5:
        return to_signed(a) >= to_signed(b)
    if funct3 == 6:
        return a < b
    if funct3 == 7:
        return a >= b
    raise _Trap(CAUSE_ILLEGAL)


class Iss:
    def __init__(self, image: bytes) -> None:
        if len(image) > MEM_SIZE:
            raise SimError(f"image is {len(image)} bytes, memory is {MEM_SIZE}")
        self.mem = bytearray(MEM_SIZE)
        self.mem[: len(image)] = image
        self.regs = [0] * 32
        self.pc = 0
        self.exit_code: int | None = None
        self.output = bytearray()
        self.priv = PRIV_M
        self.mstatus = 0  # only MIE, MPIE and MPP exist
        self.mie = 0
        self.mtvec = 0
        self.mscratch = 0
        self.mepc = 0
        self.mcause = 0
        self.mtval = 0
        self.mtime = 0
        self.mtimecmp = MASK32

    @property
    def mip(self) -> int:
        return MTI if self.mtime >= self.mtimecmp else 0

    # --- memory -----------------------------------------------------------

    def _check(self, addr: int, size: int) -> None:
        if addr % size:
            raise SimError(f"misaligned access of {size} bytes at {addr:#x}")
        if addr + size > MEM_SIZE:
            raise SimError(f"access outside memory at {addr:#x}")

    def load(self, addr: int, size: int) -> int:
        if addr == MTIME_ADDR and size == 4:
            return self.mtime
        if addr == MTIMECMP_ADDR and size == 4:
            return self.mtimecmp
        self._check(addr, size)
        return int.from_bytes(self.mem[addr : addr + size], "little")

    def store(self, addr: int, size: int, value: int) -> None:
        if addr == TOHOST_ADDR:
            self.exit_code = value & MASK32
            return
        if addr == PUTCHAR_ADDR:
            self.output.append(value & 0xFF)
            return
        if addr == MTIMECMP_ADDR and size == 4:
            self.mtimecmp = value & MASK32
            return
        if addr == MTIME_ADDR:
            return  # read-only
        self._check(addr, size)
        mask = (1 << (8 * size)) - 1
        self.mem[addr : addr + size] = (value & mask).to_bytes(size, "little")

    # --- traps ------------------------------------------------------------

    def _irq_pending(self) -> bool:
        # machine interrupts are always enabled while user code runs
        enabled = self.priv == PRIV_U or self.mstatus & MSTATUS_MIE
        return bool(enabled and self.mie & self.mip)

    def _take_trap(self, pc: int, cause: int, tval: int) -> Trap:
        mpie = MSTATUS_MPIE if self.mstatus & MSTATUS_MIE else 0
        self.mstatus = mpie | self.priv << 11
        self.priv = PRIV_M
        self.mepc = pc
        self.mcause = cause
        self.mtval = tval
        self.pc = self.mtvec
        return Trap(pc, cause)

    def _mret(self, inst: int) -> int:
        if self.priv != PRIV_M:
            raise _Trap(CAUSE_ILLEGAL, inst)
        mie = MSTATUS_MIE if self.mstatus & MSTATUS_MPIE else 0
        self.priv = (self.mstatus & MSTATUS_MPP) >> 11
        self.mstatus = MSTATUS_MPIE | mie
        return self.mepc

    # --- CSRs -------------------------------------------------------------

    def read_csr(self, csr: int) -> int:
        return {
            MSTATUS: self.mstatus, MIE: self.mie, MTVEC: self.mtvec,
            MSCRATCH: self.mscratch, MEPC: self.mepc, MCAUSE: self.mcause,
            MTVAL: self.mtval, MIP: self.mip,
        }[csr]

    def write_csr(self, csr: int, value: int) -> None:
        value &= MASK32
        if csr == MSTATUS:
            # MPP is WARL: bit 12 picks machine (11) or user (00)
            mpp = MSTATUS_MPP if value & (1 << 12) else 0
            self.mstatus = value & (MSTATUS_MIE | MSTATUS_MPIE) | mpp
        elif csr == MIE:
            self.mie = value & MTI
        elif csr == MTVEC:
            self.mtvec = value & ~3 & MASK32
        elif csr == MSCRATCH:
            self.mscratch = value
        elif csr == MEPC:
            self.mepc = value & ~3 & MASK32
        elif csr == MCAUSE:
            self.mcause = value
        elif csr == MTVAL:
            self.mtval = value
        # MIP: its only bit is the timer, which is read-only

    def _exec_system(self, inst: int, funct3: int, a: int) -> tuple[int | None, int | None]:
        """Run a SYSTEM instruction. Returns (value for rd, new pc or None)."""
        rs1 = (inst >> 15) & 0x1F
        if funct3 == 0:
            if rs1 or (inst >> 7) & 0x1F:
                raise _Trap(CAUSE_ILLEGAL, inst)
            funct12 = inst >> 20
            if funct12 == 0x000:
                raise _Trap(CAUSE_ECALL_U if self.priv == PRIV_U else CAUSE_ECALL_M)
            if funct12 == 0x001:
                raise _Trap(CAUSE_BREAKPOINT)
            if funct12 == 0x302:
                return None, self._mret(inst)
            if funct12 == 0x105:  # WFI may be a no-op
                return None, None
            raise _Trap(CAUSE_ILLEGAL, inst)
        csr = inst >> 20
        if funct3 == 4 or self.priv != PRIV_M or csr not in CSRS:
            raise _Trap(CAUSE_ILLEGAL, inst)
        old = self.read_csr(csr)
        operand = rs1 if funct3 & 4 else a  # immediate forms use the rs1 field
        if funct3 & 3 == 1:
            self.write_csr(csr, operand)
        elif rs1:  # CSRRS/CSRRC with x0 or uimm 0 do not write
            self.write_csr(csr, old | operand if funct3 & 3 == 2 else old & ~operand)
        return old, None

    # --- execution --------------------------------------------------------

    def step(self) -> Retired | Trap:
        pc = self.pc
        if self._irq_pending():
            return self._take_trap(pc, CAUSE_TIMER, 0)
        try:
            retired = self._execute(pc)
        except _Trap as trap:
            return self._take_trap(pc, trap.cause, trap.tval)
        self.mtime = (self.mtime + 1) & MASK32
        return retired

    def _execute(self, pc: int) -> Retired:
        inst = self.load(pc, 4)
        opcode = inst & 0x7F
        rd = (inst >> 7) & 0x1F
        funct3 = (inst >> 12) & 7
        a = self.regs[(inst >> 15) & 0x1F]
        b = self.regs[(inst >> 20) & 0x1F]
        alt = bool(inst >> 30 & 1)
        next_pc = (pc + 4) & MASK32
        result: int | None = None

        if opcode == 0x37:  # LUI
            result = imm_u(inst)
        elif opcode == 0x17:  # AUIPC
            result = (pc + imm_u(inst)) & MASK32
        elif opcode == 0x6F:  # JAL
            result = next_pc
            next_pc = (pc + imm_j(inst)) & MASK32
        elif opcode == 0x67:  # JALR
            result = next_pc
            next_pc = (a + imm_i(inst)) & MASK32 & ~1
        elif opcode == 0x63:  # BRANCH
            if branch_taken(funct3, a, b):
                next_pc = (pc + imm_b(inst)) & MASK32
        elif opcode == 0x03:  # LOAD
            result = self._exec_load(funct3, (a + imm_i(inst)) & MASK32)
        elif opcode == 0x23:  # STORE
            size = {0: 1, 1: 2, 2: 4}.get(funct3)
            if size is None:
                raise SimError(f"bad store funct3 {funct3} at {pc:#x}")
            self.store((a + imm_s(inst)) & MASK32, size, b)
        elif opcode == 0x13:  # OP-IMM
            imm = imm_i(inst) & MASK32
            is_shift = funct3 in (1, 5)
            result = alu(funct3, alt and is_shift, a, imm)
        elif opcode == 0x33:  # OP
            result = alu(funct3, alt, a, b)
        elif opcode == 0x0F:  # FENCE: no caches, nothing to order
            pass
        elif opcode == 0x73:  # SYSTEM
            result, target = self._exec_system(inst, funct3, a)
            if target is not None:
                next_pc = target
        else:
            raise _Trap(CAUSE_ILLEGAL, inst)

        if next_pc % 4:
            raise SimError(f"misaligned jump to {next_pc:#x} from {pc:#x}")
        self.pc = next_pc
        if result is None or rd == 0:
            return Retired(pc, 0, 0)
        self.regs[rd] = result
        return Retired(pc, rd, result)

    def _exec_load(self, funct3: int, addr: int) -> int:
        if funct3 == 0:
            return sext(self.load(addr, 1), 8) & MASK32
        if funct3 == 1:
            return sext(self.load(addr, 2), 16) & MASK32
        if funct3 == 2:
            return self.load(addr, 4)
        if funct3 == 4:
            return self.load(addr, 1)
        if funct3 == 5:
            return self.load(addr, 2)
        raise SimError(f"bad load funct3 {funct3}")

    def run(self, max_steps: int = 1_000_000) -> list[Retired | Trap]:
        log: list[Retired | Trap] = []
        for _ in range(max_steps):
            log.append(self.step())
            if self.exit_code is not None:
                return log
        raise SimError(f"no exit after {max_steps} instructions")
