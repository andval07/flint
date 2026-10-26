"""Thin wrapper around zig, used as a RISC-V cross compiler and linker.

zig ships clang and lld for every target, so `pip install ziglang` is the whole
toolchain: no riscv-gcc to install.
"""

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "programs" / "runtime"
TARGET = ["-target", "riscv32-freestanding", "-mcpu=generic_rv32", "-mno-relax"]


class ToolchainError(Exception):
    pass


def zig(*args: str, cwd: Path | None = None) -> None:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "ziglang", *args],
            cwd=cwd, capture_output=True, text=True, timeout=300,
        )
    except FileNotFoundError as exc:
        raise ToolchainError("zig is not available: pip install ziglang") from exc
    if result.returncode != 0:
        if "No module named ziglang" in result.stderr:
            raise ToolchainError("zig is not available: pip install ziglang")
        raise ToolchainError(f"zig {args[0]} failed:\n{result.stderr}")


def _link_and_flatten(inputs: list[Path], workdir: Path, extra: list[str]) -> bytes:
    elf = workdir / "out.elf"
    binary = workdir / "out.bin"
    zig("cc", *TARGET, "-nostdlib", "-ffreestanding", *extra,
        *map(str, inputs), "-o", str(elf))
    zig("objcopy", "-O", "binary", str(elf), str(binary))
    return binary.read_bytes()


def llvm_assemble(source: str) -> bytes:
    """Assemble with LLVM and return the raw text section, linked at address 0."""
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        src = work / "in.s"
        src.write_text(".text\n.globl _start\n_start:\n" + source)
        script = work / "text_at_zero.ld"
        script.write_text("ENTRY(_start)\nSECTIONS { . = 0; .text : { *(.text*) } }\n")
        return _link_and_flatten([src], work, [f"-Wl,-T,{script}"])


def compile_c(sources: list[Path], opt: str = "-O2") -> bytes:
    """Build a C program with the flint runtime into a flat memory image."""
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        inputs = [RUNTIME / "start.s", RUNTIME / "flint.c", *sources]
        extra = [opt, "-fno-sanitize=all", f"-I{RUNTIME}", f"-Wl,-T,{RUNTIME / 'link.ld'}"]
        return _link_and_flatten(inputs, work, extra)
