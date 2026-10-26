"""Lockstep tests: the Verilog core must retire exactly what the ISS retires."""

import random

import pytest

from flint import rtlsim
from flint.asm import assemble
from flint.iss import Iss


def check(image: bytes) -> rtlsim.RtlResult:
    iss = Iss(image)
    expected = iss.run()
    got = rtlsim.run(image)
    for i, (want, have) in enumerate(zip(expected, got.log)):
        assert have == want, f"retire #{i}: rtl {have} != iss {want}"
    assert len(got.log) == len(expected)
    assert got.exit_code == iss.exit_code
    assert got.output == bytes(iss.output)
    return got


DIRECTED = {
    "forward_ex_mem": """
        li a0, 5
        addi a0, a0, 1
        addi a0, a0, 1
        add a1, a0, a0
        halt
    """,
    "forward_both_operands": """
        li t0, 3
        li t1, 4
        add t2, t0, t1
        sub a0, t2, t0
        halt
    """,
    "load_use_stall": """
        li t0, 0x400
        li t1, 1234
        sw t1, 0(t0)
        lw t2, 0(t0)
        addi a0, t2, 1
        halt
    """,
    "load_into_store_data": """
        li t0, 0x400
        li t1, -7
        sw t1, 0(t0)
        lw t2, 0(t0)
        sw t2, 4(t0)
        lb a0, 4(t0)
        halt
    """,
    "branch_flush": """
        li a0, 0
        li t0, 1
        beq t0, t0, skip
        addi a0, a0, 100
        addi a0, a0, 100
    skip:
        addi a0, a0, 1
        halt
    """,
    "loop_sum": """
        li a0, 0
        li t0, 10
    loop:
        add a0, a0, t0
        addi t0, t0, -1
        bne t0, zero, loop
        halt
    """,
    "jal_jalr_link": """
        jal ra, func
        addi a0, a0, 1
        halt
    func:
        li a0, 40
        jalr zero, 0(ra)
    """,
    "byte_half_stores": """
        li t0, 0x800
        li t1, 0x11223344
        sw t1, 0(t0)
        li t2, 0xaa
        sb t2, 1(t0)
        li t2, 0xbeef
        sh t2, 2(t0)
        lw a1, 0(t0)
        lhu a2, 2(t0)
        lh a3, 2(t0)
        lbu a4, 1(t0)
        andi a0, a1, 0xff
        halt
    """,
    "x0_never_written": """
        addi zero, zero, 5
        add a0, zero, zero
        halt
    """,
    "putchar": """
        li t0, 0x10000004
        li t1, 72
        sw t1, 0(t0)
        li t1, 105
        sw t1, 0(t0)
        li a0, 0
        halt
    """,
}


@pytest.mark.parametrize("name", DIRECTED)
def test_directed(name: str) -> None:
    check(assemble(DIRECTED[name]))


def test_load_use_costs_one_cycle() -> None:
    base = check(assemble("li t0, 0x400\nlw t1, 0(t0)\nnop\naddi a0, t1, 0\nhalt"))
    stalled = check(assemble("li t0, 0x400\nlw t1, 0(t0)\naddi a0, t1, 0\nnop\nhalt"))
    assert stalled.cycles == base.cycles + 1


REGS = ["t0", "t1", "t2", "a0", "a1", "a2", "a3", "s0"]
ALU_R = ["add", "sub", "sll", "slt", "sltu", "xor", "srl", "sra", "or", "and"]
ALU_I = ["addi", "slti", "sltiu", "xori", "ori", "andi"]
SHIFT_I = ["slli", "srli", "srai"]
LOADS = ["lb", "lbu", "lh", "lhu", "lw"]
STORES = {"sb": 1, "sh": 2, "sw": 4}
BRANCHES = ["beq", "bne", "blt", "bge", "bltu", "bgeu"]


def random_program(rng: random.Random, length: int = 120) -> str:
    """Straight-line code with short forward branches, dense in hazards.

    s1 is a fixed data pointer so random loads/stores stay in a scratch area.
    Only a few registers are used, which makes back-to-back dependences common.
    """
    lines = ["li s1, 0x2000"]
    lines += [f"li {r}, {rng.randint(-2**31, 2**31 - 1)}" for r in REGS]
    for i in range(length):
        rd, rs1, rs2 = (rng.choice(REGS) for _ in range(3))
        kind = rng.random()
        if kind < 0.35:
            lines.append(f"{rng.choice(ALU_R)} {rd}, {rs1}, {rs2}")
        elif kind < 0.55:
            lines.append(f"{rng.choice(ALU_I)} {rd}, {rs1}, {rng.randint(-2048, 2047)}")
        elif kind < 0.62:
            lines.append(f"{rng.choice(SHIFT_I)} {rd}, {rs1}, {rng.randint(0, 31)}")
        elif kind < 0.65:
            lines.append(f"lui {rd}, {rng.randint(0, 0xfffff)}")
        elif kind < 0.67:
            lines.append(f"auipc {rd}, {rng.randint(0, 0xfffff)}")
        elif kind < 0.78:
            op = rng.choice(LOADS)
            size = {"lb": 1, "lbu": 1, "lh": 2, "lhu": 2, "lw": 4}[op]
            lines.append(f"{op} {rd}, {size * rng.randint(0, 63)}(s1)")
        elif kind < 0.88:
            op, size = rng.choice(list(STORES.items()))
            lines.append(f"{op} {rs2}, {size * rng.randint(0, 63)}(s1)")
        else:
            lines.append(f"{rng.choice(BRANCHES)} {rs1}, {rs2}, L{i}")
            lines += [f"addi {rng.choice(REGS)}, {rng.choice(REGS)}, 1"
                      for _ in range(rng.randint(0, 3))]
            lines.append(f"L{i}:")
    lines.append("xor a0, a0, a1")
    lines.append("halt")
    return "\n".join(lines)


@pytest.mark.parametrize("seed", range(40))
def test_random_lockstep(seed: int) -> None:
    check(assemble(random_program(random.Random(seed))))
