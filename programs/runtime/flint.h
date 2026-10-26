#ifndef FLINT_H
#define FLINT_H

#include <stdint.h>

void flint_putc(char c);
void flint_puts(const char *s);
void flint_put_u32(uint32_t value);
_Noreturn void flint_exit(int code);

#endif
