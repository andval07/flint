#include "flint.h"

#define LIMIT 2000

static uint8_t composite[LIMIT];

int main(void) {
    uint32_t count = 0;
    for (int i = 2; i < LIMIT; i++) {
        if (composite[i]) continue;
        count++;
        for (int j = i + i; j < LIMIT; j += i) composite[j] = 1;
    }
    flint_puts("primes below 2000: ");
    flint_put_u32(count);
    flint_putc('\n');
    return count == 303 ? 0 : 1;
}
