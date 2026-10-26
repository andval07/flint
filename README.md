# flint

A 5-stage pipelined RISC-V CPU (RV32I) in Verilog, built from scratch. It runs
C programs compiled with clang, and every instruction it retires is checked
against a Python model of the ISA.

```
IF -> ID -> EX -> MEM -> WB
```

- about 420 lines of RTL, plus a testbench
- a Python instruction set simulator (ISS) used as the golden model
- a small two-pass assembler for test programs
- C programs built with `zig cc` (no riscv-gcc needed)
- synthesis with Yosys and place-and-route on an iCE40 UP5K with nextpnr

## Pipeline

| Hazard | How the core handles it |
|---|---|
| ALU result used by the next instruction | forwarded from EX/MEM into EX |
| result used two instructions later | forwarded from MEM/WB into EX |
| result used three instructions later | register file returns the value being written (write-first) |
| load followed by a use | 1-cycle stall, detected in ID |
| taken branch or jump | resolved in EX, IF/ID and ID/EX flushed: 2-cycle penalty |

Branches are predicted not taken. There are no caches: instruction and data
memory are outside the core with combinational reads, so the testbench models
them as one 64 KiB array.

Memory map:

| Address | Use |
|---|---|
| `0x0000_0000` - `0x0000_ffff` | RAM, code and data |
| `0x1000_0000` | store here to stop the simulation; the value is the exit code |
| `0x1000_0004` | store a byte here to print it |

## Verification

Every test runs the same program on the ISS and on the Verilog core, and
compares the commit logs line by line: PC, destination register and value of
every retired instruction. The first different line fails the test with both
values.

- 22 directed ISS tests and 11 assembler tests (the assembler output is compared
  with LLVM's)
- 10 directed pipeline tests, one per hazard case
- a test that checks a load-use costs exactly one extra cycle
- 40 random programs: short forward branches, loads and stores, and only eight
  registers, so back-to-back dependences are common
- 4 C programs at `-O0` and `-O2`

To check the tests catch real bugs, I disabled the EX/MEM forwarding: all 51
pipeline tests failed. With the load-use stall removed, 29 failed.

C programs (cycles counted from reset to the exit store):

| Program | Flags | Instructions | Cycles | CPI |
|---|---|---|---|---|
| fib(15), recursive | -O2 | 25,550 | 31,111 | 1.22 |
| insertion sort, 64 words | -O2 | 10,125 | 14,213 | 1.40 |
| sieve below 2000 | -O2 | 29,836 | 44,919 | 1.51 |
| crc32 of "123456789" | -O0 | 2,942 | 4,277 | 1.45 |

## Synthesis

`python synth/report.py` runs Yosys and nextpnr (both from pip, via YoWASP).

| | |
|---|---|
| generic gates (Yosys `synth`) | 6,858 cells |
| iCE40 LUT4 | 1,472 |
| iCE40 carry cells | 213 |
| block RAMs | 4 (the register file) |
| fmax on iCE40 UP5K | 20.1 MHz (best of 5 seeds) |

The core has 237 port bits, more than the FPGA has pins, so
`synth/timing_top.v` feeds the inputs from a shift register and folds all
outputs into one pin. The fmax is the core's own register-to-register path;
the memory is not part of it.

The critical path is the EX stage: forwarding compare, forwarding mux, adder
and branch compare, then the redirect signal that flushes the pipeline. Moving
branch resolution to ID or adding a branch predictor would shorten it.

## Running it

Needs Python 3.11+ and Icarus Verilog (`iverilog` and `vvp` on the PATH).

```
python -m venv .venv
.venv/Scripts/pip install pytest ziglang yowasp-yosys yowasp-nextpnr-ice40
.venv/Scripts/python -m pytest
.venv/Scripts/python synth/report.py
```

## Layout

```
rtl/        core.v (pipeline), decoder.v, alu.v, regfile.v
sim/tb.v    memory, MMIO and commit log
flint/      iss.py, asm.py, rtlsim.py (runs Icarus), toolchain.py (zig)
programs/   C programs and the runtime (start.s, link.ld, putchar/exit)
synth/      Yosys script, timing harness, report script
tests/      ISS, assembler, lockstep and C program tests
```

## Not implemented

CSRs, interrupts and exceptions (an illegal instruction stops the ISS; the core
treats it as a no-op), ECALL/EBREAK, the M extension, caches.
