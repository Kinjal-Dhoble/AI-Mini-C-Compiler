// Find bugs demo 24: the number of milliseconds in 30 days (2592000000).
#include <stdio.h>

int main() {
    int days = 30;
    long ms = days * 24 * 60 * 60 * 1000;
    printf("Milliseconds: %ld\n", ms);
    return 0;
}
