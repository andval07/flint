"""C programs built with zig cc, run on the ISS and on the core in lockstep."""

from pathlib import Path

import pytest

from flint import rtlsim
from flint.iss import Iss
from flint.toolchain import ROOT, compile_c

PROGRAMS = sorted((ROOT / "programs").glob("*.c"))
EXPECTED_OUTPUT = {
    "fib": b"fib(15) = 610\n",
    "sort": b"sorted\n",
    "crc32": b"crc32 = 3421780262\n",
    "sieve": b"primes below 2000: 303\n",
}


@pytest.mark.parametrize("source", PROGRAMS, ids=lambda p: p.stem)
@pytest.mark.parametrize("opt", ["-O0", "-O2"])
def test_program(source: Path, opt: str) -> None:
    image = compile_c([source], opt)
    iss = Iss(image)
    expected = iss.run(max_steps=5_000_000)
    assert iss.exit_code == 0
    assert bytes(iss.output) == EXPECTED_OUTPUT[source.stem]

    got = rtlsim.run(image, max_cycles=10_000_000)
    assert got.log == expected
    assert got.exit_code == 0
    assert got.output == EXPECTED_OUTPUT[source.stem]
    print(f"{source.stem} {opt}: {got.retired} instr, {got.cycles} cycles, "
          f"CPI {got.cycles / got.retired:.2f}")
