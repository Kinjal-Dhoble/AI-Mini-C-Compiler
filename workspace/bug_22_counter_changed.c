// Find bugs demo 22: print every score, doubled. There are 6 scores.
#include <stdio.h>

int main() {
    int scores[6] = {4, 8, 15, 16, 23, 42};
    for (int i = 0; i < 6; i++) {
        printf("%d\n", scores[i] * 2);
        i += 1;
    }
    return 0;
}
