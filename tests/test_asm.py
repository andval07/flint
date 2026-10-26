"""The assembler is checked against LLVM's, shipped with zig."""

import pytest

from flint.asm import AsmError, assemble, split_li
from flint.toolchain import ToolchainError, llvm_assemble

SAMPLE = """
start:
add a0, a1, a2
sub t0, t1, t2
sra s1, s2, s3
sltu a0, a0, a1
addi sp, sp, -16
xori a3, a4, -1
slli a0, a0, 31
srai a1, a1, 7
lw a0, -4(sp)
lbu t0, 2047(a1)
sh a2, -2048(s0)
sw ra, 12(sp)
beq a0, a1, start
bgeu t0, t1, end
jal ra, start
jalr ra, 8(a0)
lui a0, 0xfffff
auipc t1, 0x12
csrrw a0, mtvec, a1
csrrs t0, mstatus, zero
csrrc a2, mie, a3
csrrwi a0, mscratch, 31
csrrsi zero, mstatus, 8
csrrci a1, 0x344, 1
csrr a0, mcause
csrw mepc, t0
csrs mie, a1
csrc mstatus, a2
csrwi mtval, 0
ecall
ebreak
mret
wfi
end:
nop
"""


def test_encoding_matches_llvm():
    try:
        expected = llvm_assemble(SAMPLE)
    except ToolchainError as exc:
        pytest.skip(str(exc))
    assert assemble(SAMPLE) == expected


@pytest.mark.parametrize("value", [0, 1, -1, 0x7FF, 0x800, -2048, 0x12345678, 0xFFFFF800])
def test_li_split_rebuilds_the_constant(value):
    upper, low = split_li(value)
    assert ((upper << 12) + low) & 0xFFFF_FFFF == value & 0xFFFF_FFFF


def test_out_of_range_immediate_is_rejected():
    with pytest.raises(AsmError, match="does not fit"):
        assemble("addi a0, a0, 2048")


def test_unknown_label_is_rejected():
    with pytest.raises(AsmError, match="unknown label"):
        assemble("j nowhere")
