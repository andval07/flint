"""Golden model: an instruction-set simulator for RV32I.

The RTL core is checked against this model one retired instruction at a time,
so it has to be boring and obviously correct. No pipeline, no timing: fetch,
decode, execute, retire.
"""

from dataclasses import dataclass

from flint.memmap import MEM_SIZE, PUTCHAR_ADDR, TOHOST_ADDR

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
    raise SimError(f"bad branch funct3 {funct3}")


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

    # --- memory -----------------------------------------------------------

    def _check(self, addr: int, size: int) -> None:
        if addr % size:
            raise SimError(f"misaligned access of {size} bytes at {addr:#x}")
        if addr + size > MEM_SIZE:
            raise SimError(f"access outside memory at {addr:#x}")

    def load(self, addr: int, size: int) -> int:
        self._check(addr, size)
        return int.from_bytes(self.mem[addr : addr + size], "little")

    def store(self, addr: int, size: int, value: int) -> None:
        if addr == TOHOST_ADDR:
            self.exit_code = value & MASK32
            return
        if addr == PUTCHAR_ADDR:
            self.output.append(value & 0xFF)
            return
        self._check(addr, size)
        mask = (1 << (8 * size)) - 1
        self.mem[addr : addr + size] = (value & mask).to_bytes(size, "little")

    # --- execution --------------------------------------------------------

    def step(self) -> Retired:
        pc = self.pc
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
        else:
            raise SimError(f"illegal instruction {inst:08x} at {pc:#x}")

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

    def run(self, max_steps: int = 1_000_000) -> list[Retired]:
        log = []
        for _ in range(max_steps):
            log.append(self.step())
            if self.exit_code is not None:
                return log
        raise SimError(f"no exit after {max_steps} instructions")
