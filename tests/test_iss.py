"""Directed tests for the golden model. If these are wrong, every RTL test is wrong."""

import pytest

from flint.asm import assemble
from flint.iss import Iss, SimError


def run(source: str) -> Iss:
    iss = Iss(assemble(source + "\nhalt\n"))
    iss.run()
    return iss


def test_arithmetic_wraps_at_32_bits():
    iss = run("li a0, 0x7fffffff\naddi a0, a0, 1\nli a1, 0\naddi a1, a1, -1")
    assert iss.regs[10] == 0x8000_0000
    assert iss.regs[11] == 0xFFFF_FFFF


def test_signed_and_unsigned_compare():
    iss = run("li t0, -1\nli t1, 1\nslt a0, t0, t1\nsltu a1, t0, t1\nsltiu a2, t1, -1")
    assert (iss.regs[10], iss.regs[11], iss.regs[12]) == (1, 0, 1)


def test_shifts_use_low_five_bits_and_sra_keeps_sign():
    iss = run("li t0, 0x80000000\nli t1, 33\nsrl a0, t0, t1\nsra a1, t0, t1\nsll a2, t1, t1")
    assert iss.regs[10] == 0x4000_0000
    assert iss.regs[11] == 0xC000_0000
    assert iss.regs[12] == 66


def test_x0_stays_zero():
    iss = run("addi x0, x0, 5\nadd a0, x0, x0")
    assert iss.regs[0] == 0
    assert iss.regs[10] == 0


def test_loads_sign_and_zero_extend():
    iss = run(
        """
        li t0, 0x1000
        li t1, 0x8081f0ff
        sw t1, 0(t0)
        lb a0, 0(t0)
        lbu a1, 0(t0)
        lh a2, 2(t0)
        lhu a3, 2(t0)
        lw a4, 0(t0)
        """
    )
    assert iss.regs[10:15] == [0xFFFF_FFFF, 0xFF, 0xFFFF_8081, 0x8081, 0x8081_F0FF]


def test_partial_stores_touch_only_their_bytes():
    iss = run("li t0, 0x1000\nli t1, -1\nsw t1, 0(t0)\nsb x0, 1(t0)\nsh x0, 2(t0)\nlw a0, 0(t0)")
    assert iss.regs[10] == 0x0000_00FF


def test_branches_and_loop():
    iss = run(
        """
        li a0, 0
        li t0, 10
        loop:
        add a0, a0, t0
        addi t0, t0, -1
        bne t0, x0, loop
        """
    )
    assert iss.regs[10] == 55


def test_jal_and_jalr_link_and_clear_bit_zero():
    iss = run(
        """
        jal ra, func
        j done
        func:
        addi a0, ra, 0
        jalr x0, 1(ra)
        done:
        """
    )
    assert iss.regs[10] == 4
    assert iss.regs[1] == 4


def test_auipc_adds_pc():
    iss = run("nop\nauipc a0, 1")
    assert iss.regs[10] == 0x1004


def test_halt_sets_exit_code():
    iss = run("li a0, 42")
    assert iss.exit_code == 42


def test_illegal_instruction_raises():
    with pytest.raises(SimError, match="illegal"):
        Iss(b"\xff\xff\xff\xff").run()


def test_misaligned_load_raises():
    with pytest.raises(SimError, match="misaligned"):
        run("li t0, 0x1001\nlw a0, 0(t0)")
