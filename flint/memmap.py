"""Memory map shared by the golden model, the testbench and the C runtime.

One flat 64 KiB memory starting at 0. The program image is loaded into both
the instruction and the data memory. Two store-only addresses talk to the host.
The timer sits at the CLINT addresses but is 32 bits wide.
"""

MEM_SIZE = 64 * 1024
TOHOST_ADDR = 0x1000_0000  # a store here ends the run; the value is the exit code
PUTCHAR_ADDR = 0x1000_0004  # a store here prints one byte
MTIMECMP_ADDR = 0x0200_4000  # read/write; the timer fires when mtime >= mtimecmp
MTIME_ADDR = 0x0200_BFF8  # read-only; counts retired instructions, not cycles
