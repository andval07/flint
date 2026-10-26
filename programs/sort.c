#include "flint.h"

#define N 64

static uint32_t data[N];

static uint32_t next(uint32_t *state) {
    *state ^= *state << 13; *state ^= *state >> 17; *state ^= *state << 5;
    return *state;
}

static void insertion_sort(uint32_t *a, int n) {
    for (int i = 1; i < n; i++) {
        uint32_t key = a[i];
        int j = i - 1;
        while (j >= 0 && a[j] > key) { a[j + 1] = a[j]; j--; }
        a[j + 1] = key;
    }
}

int main(void) {
    uint32_t state = 2463534242u;
    for (int i = 0; i < N; i++) data[i] = next(&state);
    insertion_sort(data, N);
    for (int i = 1; i < N; i++)
        if (data[i - 1] > data[i]) return 1;
    flint_puts("sorted\n");
    return 0;
}
