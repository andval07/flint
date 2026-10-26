#include "flint.h"

#define TOHOST  (*(volatile uint32_t *)0x10000000u)
#define PUTCHAR (*(volatile uint32_t *)0x10000004u)

void flint_putc(char c) { PUTCHAR = (uint8_t)c; }

void flint_puts(const char *s) {
    while (*s) flint_putc(*s++);
}

/* Decimal print without division: RV32I has no divide instruction and the
   compiler would call libgcc, which is not linked. */
void flint_put_u32(uint32_t value) {
    static const uint32_t powers[] = {1000000000u, 100000000u, 10000000u,
        1000000u, 100000u, 10000u, 1000u, 100u, 10u, 1u};
    int started = 0;
    for (int i = 0; i < 10; i++) {
        char digit = '0';
        while (value >= powers[i]) { value -= powers[i]; digit++; }
        if (digit != '0' || started || i == 9) { flint_putc(digit); started = 1; }
    }
}

_Noreturn void flint_exit(int code) {
    TOHOST = (uint32_t)code;
    for (;;) {}
}
