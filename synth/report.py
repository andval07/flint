"""Synthesize the core with Yosys and place-and-route it on an iCE40 UP5K.

Prints the generic gate count, the iCE40 resource use and the best fmax over a
few placement seeds. Run from the repository root: python synth/report.py
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
SEEDS = range(1, 6)


def tool(name: str, *args: str) -> str:
    exe = Path(sys.executable).parent / name
    result = subprocess.run([str(exe), *args], cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0 and "Max frequency" not in result.stderr:
        sys.exit(f"{name} failed:\n{result.stderr[-2000:]}")
    return result.stdout + result.stderr


def main() -> None:
    BUILD.mkdir(exist_ok=True)
    tool("yowasp-yosys", "-q", "-s", "synth/synth.ys")
    generic = (BUILD / "generic_stat.txt").read_text()
    ice40 = (BUILD / "ice40_stat.txt").read_text()
    print("generic cells:", re.search(r"(\d+) cells", generic).group(1))
    for cell in ("SB_LUT4", "SB_CARRY", "SB_RAM40_4K"):
        print(f"{cell}:", re.search(rf"(\d+)\s+{cell}\b", ice40).group(1))
    dffs = sum(int(n) for n in re.findall(r"(\d+)\s+SB_DFF\w*", ice40))
    print("flip-flops (incl. harness):", dffs)

    best = 0.0
    for seed in SEEDS:
        log = tool("yowasp-nextpnr-ice40", "--up5k", "--package", "sg48",
                   "--json", "build/core_ice40.json", "--freq", "12",
                   "--seed", str(seed))
        mhz = max(float(m) for m in re.findall(r"Max frequency for clock.*?: ([\d.]+) MHz", log))
        print(f"seed {seed}: {mhz:.2f} MHz")
        best = max(best, mhz)
    print(f"best fmax on iCE40 UP5K: {best:.2f} MHz")


if __name__ == "__main__":
    main()
