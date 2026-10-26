"""Lockstep tests: the Verilog core must retire exactly what the ISS retires."""

import random

import pytest

from flint import rtlsim
from flint.asm import assemble
from flint.iss import Iss, Trap


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


# --- traps ---------------------------------------------------------------------

MTIMECMP = "0x02004000"
MTIME = "0x0200bff8"

# Skips the instruction that raised an exception; for the timer it pushes
# mtimecmp forward. Uses only s2-s4, which the programs below leave alone.
HANDLER = f"""
handler:
    addi s2, s2, 1
    csrr s3, mcause
    blt s3, zero, irq
    csrr s3, mepc
    addi s3, s3, 4
    csrw mepc, s3
    mret
irq:
    li s3, {MTIME}
    lw s4, 0(s3)
    addi s4, s4, INTERVAL
    li s3, {MTIMECMP}
    sw s4, 0(s3)
    mret
"""

TRAP_DIRECTED = {
    "ecall_flushes_younger_instructions": """
        li t0, handler
        csrw mtvec, t0
        li a0, 1
        ecall
        addi a0, a0, 10
        li t0, 0x10000004
        sw a0, 0(t0)
        halt
    handler:
        csrr t1, mepc
        addi t1, t1, 8
        csrw mepc, t1
        mret
    """,
    "csr_read_feeds_next_instruction": """
        li t0, 0x123
        csrw mscratch, t0
        csrrw a1, mscratch, zero
        addi a0, a1, 1
        csrr a2, mscratch
        add a0, a0, a2
        halt
    """,
    "csrw_mtvec_right_before_ecall": """
        li t0, handler
        csrw mtvec, t0
        ecall
        li a0, 99
        halt
    handler:
        csrr a0, mcause
        halt
    """,
    "mepc_write_then_mret": """
        li t0, there
        csrw mepc, t0
        mret
        li a0, 99
        halt
    there:
        li a0, 5
        halt
    """,
    "illegal_store_does_not_write": """
        li t0, handler
        csrw mtvec, t0
        li t1, 0x400
        li t2, 77
        sw t2, 0(t1)
        csrr a0, 0x7c0
        sw zero, 0(t1)
        halt
    handler:
        lw a0, 0(t1)
        csrr a1, mtval
        halt
    """,
    "user_mode_cannot_touch_csrs_or_mret": """
        li t0, handler
        csrw mtvec, t0
        li t0, user
        csrw mepc, t0
        mret
    user:
        csrr a0, mstatus
        mret
        ecall
        halt
    """ + HANDLER.replace("INTERVAL", "1"),
    "timer_interrupt_in_load_use_stall": f"""
        li t0, handler
        csrw mtvec, t0
        li t0, {MTIMECMP}
        li t1, 14
        sw t1, 0(t0)
        li t0, 0x80
        csrw mie, t0
        csrsi mstatus, 8
        li t0, 0x400
        lw t1, 0(t0)
        addi t1, t1, 1
        lw t1, 0(t0)
        addi t1, t1, 1
        lw t1, 0(t0)
        addi t1, t1, 1
        mv a0, s2
        halt
    """ + HANDLER.replace("INTERVAL", "12"),
}


@pytest.mark.parametrize("name", TRAP_DIRECTED)
def test_trap_directed(name: str) -> None:
    got = check(assemble(TRAP_DIRECTED[name]))
    assert any(isinstance(entry, Trap) for entry in got.log) or name.startswith(
        ("csr_read", "mepc_write")
    )


def test_trap_costs_four_cycles() -> None:
    plain = check(assemble("nop\nnop\nnop\nhalt"))
    trapped = check(assemble("li t0, handler\ncsrw mtvec, t0\nnop\necall\n"
                             "handler:\nhalt"))
    # the ecall slot does not retire, then IF, ID and EX refill from mtvec
    assert trapped.cycles - trapped.retired == plain.cycles - plain.retired + 4


def random_trap_program(rng: random.Random, user: bool, length: int = 150) -> str:
    """Random hazards plus timer interrupts, CSR traffic, ecalls and illegal CSRs."""
    interval = rng.randint(12, 40)  # longer than the handler, or it livelocks
    lines = [
        "li t0, handler", "csrw mtvec, t0",
        f"li t0, {MTIMECMP}", f"li t1, {rng.randint(10, 40)}", "sw t1, 0(t0)",
        "li t0, 0x80", "csrw mie, t0", "csrsi mstatus, 8",
    ]
    if user:
        lines += ["li t0, body", "csrw mepc, t0", "mret", "body:"]
    body = random_program(rng, length).splitlines()[:-2]  # no xor/halt yet
    csr_ops = [
        "csrrw {rd}, mscratch, {rs}", "csrrs {rd}, mscratch, {rs}",
        "csrrc {rd}, mscratch, {rs}", "csrr {rd}, mip", "csrr {rd}, mstatus",
        "csrr {rd}, 0x7c0", "ecall", "ebreak", "wfi",
        f"li {{rd}}, {MTIME}\nlw {{rd}}, 0({{rd}})",
    ]
    for line in body:
        lines.append(line)
        if rng.random() < 0.1:
            op = rng.choice(csr_ops)
            lines.append(op.format(rd=rng.choice(REGS), rs=rng.choice(REGS)))
    lines += ["xor a0, a0, s2", "halt"]
    return "\n".join(lines) + HANDLER.replace("INTERVAL", str(interval))


@pytest.mark.parametrize("user", [False, True], ids=["machine", "user"])
@pytest.mark.parametrize("seed", range(20))
def test_random_traps_lockstep(seed: int, user: bool) -> None:
    got = check(assemble(random_trap_program(random.Random(1000 + seed), user)))
    assert sum(isinstance(entry, Trap) for entry in got.log) > 0
