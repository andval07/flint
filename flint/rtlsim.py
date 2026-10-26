"""Run a memory image on the Verilog core with Icarus and parse the commit log."""

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from flint.iss import Retired
from flint.memmap import MEM_SIZE

ROOT = Path(__file__).resolve().parent.parent
RTL = sorted((ROOT / "rtl").glob("*.v"))
TB = ROOT / "sim" / "tb.v"
BUILD = ROOT / "build"


class RtlSimError(Exception):
    pass


@dataclass(frozen=True)
class RtlResult:
    log: list[Retired]
    exit_code: int | None
    output: bytes
    cycles: int
    retired: int


def _tool(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    for guess in (Path("C:/iverilog/bin") / f"{name}.exe", Path("/usr/bin") / name):
        if guess.exists():
            return str(guess)
    raise RtlSimError(f"{name} not found: install Icarus Verilog")


def build() -> Path:
    """Compile the testbench once; rebuild only when a source is newer."""
    BUILD.mkdir(exist_ok=True)
    out = BUILD / "tb.vvp"
    sources = [*RTL, TB]
    if out.exists() and all(s.stat().st_mtime <= out.stat().st_mtime for s in sources):
        return out
    result = subprocess.run(
        [_tool("iverilog"), "-g2012", "-Wall", "-o", str(out), *map(str, sources)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RtlSimError(f"iverilog failed:\n{result.stderr}")
    return out


def _write_hex(image: bytes, path: Path) -> None:
    if len(image) > MEM_SIZE:
        raise RtlSimError("image larger than memory")
    padded = image + bytes(-len(image) % 4)
    words = (int.from_bytes(padded[i:i + 4], "little") for i in range(0, len(padded), 4))
    path.write_text("".join(f"{w:08x}\n" for w in words))


def run(image: bytes, max_cycles: int = 2_000_000) -> RtlResult:
    vvp = build()
    with tempfile.TemporaryDirectory() as tmp:
        hex_path = Path(tmp) / "image.hex"
        _write_hex(image, hex_path)
        result = subprocess.run(
            [_tool("vvp"), "-n", str(vvp), f"+image={hex_path.as_posix()}",
             f"+max_cycles={max_cycles}"],
            capture_output=True, text=True, timeout=600, env=os.environ,
        )
    return _parse(result.stdout)


def _parse(stdout: str) -> RtlResult:
    log: list[Retired] = []
    output = bytearray()
    exit_code = None
    cycles = retired = 0
    for line in stdout.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "C":
            log.append(Retired(int(parts[1], 16), int(parts[2]), int(parts[3], 16)))
        elif parts[0] == "PUTC":
            output.append(int(parts[1], 16))
        elif parts[0] == "EXIT":
            exit_code, cycles, retired = int(parts[1]), int(parts[3]), int(parts[5])
        elif parts[0] == "TIMEOUT":
            cycles, retired = int(parts[2]), int(parts[4])
        elif parts[0] == "ERROR":
            raise RtlSimError(line)
    return RtlResult(log, exit_code, bytes(output), cycles, retired)
