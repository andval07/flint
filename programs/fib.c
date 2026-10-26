#include "flint.h"

static uint32_t fib(uint32_t n) { return n < 2 ? n : fib(n - 1) + fib(n - 2); }

int main(void) {
    uint32_t r = fib(15);
    flint_puts("fib(15) = ");
    flint_put_u32(r);
    flint_putc('\n');
    return r == 610 ? 0 : 1;
}
