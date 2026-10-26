"""A small two-pass RV32I assembler for tests and random programs.

Supports every RV32I instruction the core runs, the Zicsr instructions,
ecall/ebreak/mret/wfi, labels, and the pseudo instructions nop, li, mv, j,
csrr/csrw/csrs/csrc (and their immediate forms) and halt. It is not meant to replace a real
toolchain: C programs go through zig cc instead.
"""

import re

from flint.memmap import TOHOST_ADDR


class AsmError(Exception):
    pass


OP = {  # name: (funct3, funct7)
    "add": (0, 0x00), "sub": (0, 0x20), "sll": (1, 0x00), "slt": (2, 0x00),
    "sltu": (3, 0x00), "xor": (4, 0x00), "srl": (5, 0x00), "sra": (5, 0x20),
    "or": (6, 0x00), "and": (7, 0x00),
}
OP_IMM = {"addi": 0, "slti": 2, "sltiu": 3, "xori": 4, "ori": 6, "andi": 7}
SHIFT_IMM = {"slli": (1, 0x00), "srli": (5, 0x00), "srai": (5, 0x20)}
LOADS = {"lb": 0, "lh": 1, "lw": 2, "lbu": 4, "lhu": 5}
STORES = {"sb": 0, "sh": 1, "sw": 2}
CSR_OPS = {"csrrw": 1, "csrrs": 2, "csrrc": 3, "csrrwi": 5, "csrrsi": 6, "csrrci": 7}
CSR_PSEUDO = {  # name: (funct3, writes rd)
    "csrr": (2, True), "csrw": (1, False), "csrs": (2, False), "csrc": (3, False),
    "csrwi": (5, False), "csrsi": (6, False), "csrci": (7, False),
}
CSR_NAMES = {
    "mstatus": 0x300, "mie": 0x304, "mtvec": 0x305, "mscratch": 0x340,
    "mepc": 0x341, "mcause": 0x342, "mtval": 0x343, "mip": 0x344,
}
SYSTEM = {"ecall": 0x000, "ebreak": 0x001, "mret": 0x302, "wfi": 0x105}
BRANCHES = {"beq": 0, "bne": 1, "blt": 4, "bge": 5, "bltu": 6, "bgeu": 7}

ABI = ["zero", "ra", "sp", "gp", "tp", "t0", "t1", "t2", "s0", "s1"] + [
    f"a{i}" for i in range(8)
] + [f"s{i}" for i in range(2, 12)] + [f"t{i}" for i in range(3, 7)]
REGS = {name: i for i, name in enumerate(ABI)} | {f"x{i}": i for i in range(32)}
REGS["fp"] = 8

MEM_OPERAND = re.compile(r"^(-?\w+)\((\w+)\)$")


def reg(token: str) -> int:
    try:
        return REGS[token]
    except KeyError:
        raise AsmError(f"unknown register {token!r}") from None


def check_range(value: int, bits: int, what: str) -> int:
    low, high = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
    if not low <= value <= high:
        raise AsmError(f"{what} {value} does not fit in {bits} signed bits")
    return value


def enc_r(funct7: int, rs2: int, rs1: int, funct3: int, rd: int, opcode: int) -> int:
    return funct7 << 25 | rs2 << 20 | rs1 << 15 | funct3 << 12 | rd << 7 | opcode


def enc_i(imm: int, rs1: int, funct3: int, rd: int, opcode: int) -> int:
    imm = check_range(imm, 12, "immediate") & 0xFFF
    return imm << 20 | rs1 << 15 | funct3 << 12 | rd << 7 | opcode


def enc_s(imm: int, rs2: int, rs1: int, funct3: int) -> int:
    imm = check_range(imm, 12, "offset") & 0xFFF
    return (imm >> 5) << 25 | rs2 << 20 | rs1 << 15 | funct3 << 12 | (imm & 0x1F) << 7 | 0x23


def enc_b(offset: int, rs2: int, rs1: int, funct3: int) -> int:
    if offset % 2:
        raise AsmError(f"branch offset {offset} is odd")
    imm = check_range(offset, 13, "branch offset") & 0x1FFF
    return (
        (imm >> 12) << 31 | ((imm >> 5) & 0x3F) << 25 | rs2 << 20 | rs1 << 15
        | funct3 << 12 | ((imm >> 1) & 0xF) << 8 | ((imm >> 11) & 1) << 7 | 0x63
    )


def enc_u(imm20: int, rd: int, opcode: int) -> int:
    return (imm20 & 0xFFFFF) << 12 | rd << 7 | opcode


def enc_j(offset: int, rd: int) -> int:
    if offset % 2:
        raise AsmError(f"jump offset {offset} is odd")
    imm = check_range(offset, 21, "jump offset") & 0x1FFFFF
    return (
        (imm >> 20) << 31 | ((imm >> 1) & 0x3FF) << 21 | ((imm >> 11) & 1) << 20
        | ((imm >> 12) & 0xFF) << 12 | rd << 7 | 0x6F
    )


def split_li(value: int) -> tuple[int, int]:
    """Split a 32-bit constant into lui's upper 20 bits and addi's low 12."""
    value &= 0xFFFF_FFFF
    low = value & 0xFFF
    if low >= 0x800:
        low -= 0x1000
    return ((value - low) >> 12) & 0xFFFFF, low


def parse_lines(source: str) -> list[tuple[str, list[str]]]:
    lines = []
    for raw in source.splitlines():
        text = raw.split("#")[0].strip()
        while ":" in text:
            label, text = text.split(":", 1)
            lines.append((label.strip() + ":", []))
            text = text.strip()
        if text:
            name, _, rest = text.partition(" ")
            args = [a.strip() for a in rest.split(",") if a.strip()]
            lines.append((name.lower(), args))
    return lines


def size_of(name: str, args: list[str]) -> int:
    if name.endswith(":"):
        return 0
    if name == "li":
        return 8
    if name == "halt":
        return 12
    return 4


def assemble(source: str) -> bytes:
    lines = parse_lines(source)
    labels: dict[str, int] = {}
    pc = 0
    for name, args in lines:
        if name.endswith(":"):
            if name[:-1] in labels:
                raise AsmError(f"duplicate label {name[:-1]!r}")
            labels[name[:-1]] = pc
        pc += size_of(name, args)

    words: list[int] = []
    for name, args in lines:
        if not name.endswith(":"):
            words.extend(encode(name, args, 4 * len(words), labels))
    return b"".join(w.to_bytes(4, "little") for w in words)


def number(token: str, labels: dict[str, int], pc: int, relative: bool) -> int:
    if token in labels:
        return labels[token] - pc if relative else labels[token]
    try:
        return int(token, 0)
    except ValueError:
        raise AsmError(f"unknown label or number {token!r}") from None


def expect(name: str, args: list[str], count: int) -> None:
    if len(args) != count:
        raise AsmError(f"{name} takes {count} operands, got {len(args)}")


def encode(name: str, args: list[str], pc: int, labels: dict[str, int]) -> list[int]:
    if name in OP:
        expect(name, args, 3)
        funct3, funct7 = OP[name]
        return [enc_r(funct7, reg(args[2]), reg(args[1]), funct3, reg(args[0]), 0x33)]
    if name in OP_IMM:
        expect(name, args, 3)
        imm = number(args[2], labels, pc, False)
        return [enc_i(imm, reg(args[1]), OP_IMM[name], reg(args[0]), 0x13)]
    if name in SHIFT_IMM:
        expect(name, args, 3)
        funct3, funct7 = SHIFT_IMM[name]
        shamt = number(args[2], labels, pc, False)
        if not 0 <= shamt < 32:
            raise AsmError(f"shift amount {shamt} out of range")
        return [enc_r(funct7, shamt, reg(args[1]), funct3, reg(args[0]), 0x13)]
    if name in LOADS or name in STORES or name == "jalr":
        return [encode_mem(name, args, labels, pc)]
    if name in BRANCHES:
        expect(name, args, 3)
        offset = number(args[2], labels, pc, True)
        return [enc_b(offset, reg(args[1]), reg(args[0]), BRANCHES[name])]
    if name in ("lui", "auipc"):
        expect(name, args, 2)
        opcode = 0x37 if name == "lui" else 0x17
        return [enc_u(number(args[1], labels, pc, False), reg(args[0]), opcode)]
    if name == "jal":
        rd, target = ("ra", args[0]) if len(args) == 1 else args
        return [enc_j(number(target, labels, pc, True), reg(rd))]
    if name in CSR_OPS or name in CSR_PSEUDO:
        return [encode_csr(name, args, labels, pc)]
    if name in SYSTEM:
        expect(name, args, 0)
        return [SYSTEM[name] << 20 | 0x73]
    return encode_pseudo(name, args, pc, labels)


def encode_csr(name: str, args: list[str], labels: dict[str, int], pc: int) -> int:
    if name in CSR_OPS:
        expect(name, args, 3)
        funct3, rd, csr_token, source = CSR_OPS[name], args[0], args[1], args[2]
    else:
        expect(name, args, 2)
        funct3, writes_rd = CSR_PSEUDO[name]
        if writes_rd:
            rd, csr_token, source = args[0], args[1], "zero"
        else:
            rd, csr_token, source = "zero", args[0], args[1]
    csr = CSR_NAMES.get(csr_token)
    if csr is None:
        csr = number(csr_token, labels, pc, False)
    if not 0 <= csr < 4096:
        raise AsmError(f"csr {csr} out of range")
    if funct3 & 4:
        field = number(source, labels, pc, False)
        if not 0 <= field < 32:
            raise AsmError(f"csr immediate {field} out of range")
    else:
        field = reg(source)
    return csr << 20 | field << 15 | funct3 << 12 | reg(rd) << 7 | 0x73


def encode_mem(name: str, args: list[str], labels: dict[str, int], pc: int) -> int:
    expect(name, args, 2)
    match = MEM_OPERAND.match(args[1].replace(" ", ""))
    if not match:
        raise AsmError(f"expected offset(reg), got {args[1]!r}")
    offset = number(match.group(1), labels, pc, False)
    base = reg(match.group(2))
    if name in STORES:
        return enc_s(offset, reg(args[0]), base, STORES[name])
    if name == "jalr":
        return enc_i(offset, base, 0, reg(args[0]), 0x67)
    return enc_i(offset, base, LOADS[name], reg(args[0]), 0x03)


def encode_pseudo(name: str, args: list[str], pc: int, labels: dict[str, int]) -> list[int]:
    if name == "nop":
        return [enc_i(0, 0, 0, 0, 0x13)]
    if name == "mv":
        expect(name, args, 2)
        return [enc_i(0, reg(args[1]), 0, reg(args[0]), 0x13)]
    if name == "j":
        expect(name, args, 1)
        return [enc_j(number(args[0], labels, pc, True), 0)]
    if name == "fence":
        return [0x0FF0000F]
    if name == "li":
        expect(name, args, 2)
        upper, low = split_li(number(args[1], labels, pc, False))
        rd = reg(args[0])
        return [enc_u(upper, rd, 0x37), enc_i(low, rd, 0, rd, 0x13)]
    if name == "halt":
        # sw a0 to TOHOST. t6 is clobbered, so programs must not rely on it.
        expect(name, args, 0)
        upper, low = split_li(TOHOST_ADDR)
        return [enc_u(upper, 31, 0x37), enc_i(low, 31, 0, 31, 0x13), enc_s(0, 10, 31, 2)]
    raise AsmError(f"unknown instruction {name!r}")
