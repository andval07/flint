"""Memory map shared by the golden model, the testbench and the C runtime.

One flat 64 KiB memory starting at 0. The program image is loaded into both
the instruction and the data memory. Two store-only addresses talk to the host.
"""

MEM_SIZE = 64 * 1024
TOHOST_ADDR = 0x1000_0000  # a store here ends the run; the value is the exit code
PUTCHAR_ADDR = 0x1000_0004  # a store here prints one byte
