#include "flint.h"

static uint32_t crc32(const uint8_t *p, int n) {
    uint32_t crc = 0xffffffffu;
    while (n--) {
        crc ^= *p++;
        for (int k = 0; k < 8; k++) crc = (crc >> 1) ^ (0xedb88320u & -(crc & 1));
    }
    return ~crc;
}

int main(void) {
    static const char msg[] = "123456789";
    uint32_t crc = crc32((const uint8_t *)msg, 9);
    flint_puts("crc32 = ");
    flint_put_u32(crc);
    flint_putc('\n');
    return crc == 0xcbf43926u ? 0 : 1;
}
