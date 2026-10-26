# flint

A 5-stage pipelined RISC-V CPU (RV32I + Zicsr, machine and user mode) in
Verilog, built from scratch. It runs C programs compiled with clang, and every
instruction it retires is checked against a Python model of the ISA.

```
IF -> ID -> EX -> MEM -> WB
```

- about 670 lines of RTL, plus a testbench
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

## Traps and the timer

Enough of the privileged spec to run a small kernel:

- CSRs `mstatus` (MIE, MPIE, MPP), `mtvec`, `mepc`, `mcause`, `mtval`,
  `mscratch`, `mie`, `mip`, with `csrrw/csrrs/csrrc` and the immediate forms
- `ecall`, `ebreak`, `mret`; `wfi` is a no-op
- illegal instruction trap (cause 2, `mtval` holds the instruction); CSR access
  and `mret` from user mode are illegal
- a timer: `mtime` and `mtimecmp` are memory-mapped and raise the machine timer
  interrupt (cause `0x8000_0007`)

Traps, CSR writes and `mret` happen in MEM, the commit point. A trap or `mret`
flushes the three younger instructions and fetches from `mtvec` or `mepc`; the
trapping instruction does not retire, so a trap costs 4 cycles. A CSR read is
ready in MEM like a load, so it shares the load-use stall.

`mtime` counts retired instructions, not cycles. The ISS and the core then take
the interrupt on the same instruction, and the lockstep compare still works.

Memory map:

| Address | Use |
|---|---|
| `0x0000_0000` - `0x0000_ffff` | RAM, code and data |
| `0x1000_0000` | store here to stop the simulation; the value is the exit code |
| `0x1000_0004` | store a byte here to print it |
| `0x0200_4000` | `mtimecmp` (32 bits, reset value `0xffff_ffff`) |
| `0x0200_bff8` | `mtime`, read only |

## Verification

Every test runs the same program on the ISS and on the Verilog core, and
compares the commit logs line by line: PC, destination register and value of
every retired instruction, plus PC and cause of every trap. The first different line fails the test with
both values.

- 21 directed ISS tests and 11 assembler tests (the assembler output is compared
  with LLVM's)
- 10 directed pipeline tests, one per hazard case
- a test that checks a load-use costs exactly one extra cycle
- 40 random programs: short forward branches, loads and stores, and only eight
  registers, so back-to-back dependences are common
- 7 directed trap tests: younger instructions flushed by `ecall`, CSR read used
  by the next instruction, `csrw mtvec` right before `ecall`, a store after an
  illegal instruction, user mode, a timer interrupt during load-use stalls
- a test that checks a trap costs exactly 4 cycles
- 40 random programs with timer interrupts, CSR reads and writes, `ecall`,
  `ebreak` and illegal CSRs, half of them in user mode
- 4 C programs at `-O0` and `-O2`

To check the tests catch real bugs, I disabled the EX/MEM forwarding: all 51
pipeline tests failed. With the load-use stall removed, 29 failed. Same check on
the trap logic (107 pipeline tests now): CSR reads left out of the load-use
stall, 44 failed; `mtime` also counting trapped instructions, 41 failed; a
store in MEM still writing when its instruction traps, 3 failed. Without the
EX/MEM flush on a trap, the first 16 tests all failed and the rest ran away
until I stopped the run after 15 minutes.

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
| generic gates (Yosys `synth`) | 8,538 cells |
| iCE40 LUT4 | 2,112 |
| iCE40 carry cells | 275 |
| block RAMs | 4 (the register file) |
| fmax on iCE40 UP5K | 19.8 MHz (best of 5 seeds) |

The core has 302 port bits, more than the FPGA has pins, so
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
rtl/        core.v (pipeline), csr.v (CSRs, traps, timer), decoder.v, alu.v,
            regfile.v
sim/tb.v    memory, MMIO and commit log
flint/      iss.py, asm.py, rtlsim.py (runs Icarus), toolchain.py (zig)
programs/   C programs and the runtime (start.s, link.ld, putchar/exit)
synth/      Yosys script, timing harness, report script
tests/      ISS, assembler, lockstep and C program tests
```

## Not implemented

Misaligned access exceptions (the ISS stops instead), external and software
interrupts, a 64-bit `mtime`, PMP, supervisor mode, the M extension, caches.
