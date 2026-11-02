// DEMO 2b - LOGIC WARNINGS: valid C that gcc accepts, but each line is almost certainly a mistake.
// All of them are found WITHOUT running the program (static analysis).
#include <stdio.h>

void forever() {
    while (1) {                          // 1. endless loop: no break or return inside
        printf("tick\n");
    }
}

int main() {
    int mark = 120;
    int total = 0;
    int days = 30;
    if (mark < 0 && mark > 100) {        // 2. impossible: no number is below 0 AND above 100
        printf("Invalid\n");
    }
    if (mark == mark) {                  // 3. a value compared with itself: always true
        total = 1;
    }
    for (int i = 0; i < 5; i++) {
        total += i;
        i++;                             // 4. loop counter also changed inside the loop
    }
    if (total > 3);                      // 5. stray ';': the if controls nothing
        printf("Big total\n");
    switch (mark / 10) {
        case 12: printf("Twelve\n");     // 6. no break: falls into the next case
        case 10: printf("Ten\n"); break;
        default: printf("Other\n");
    }
    int ms = 30 * 24 * 60 * 60 * 1000;   // 7. constant calculation overflows int
    printf("%d %d %d\n", total, ms, days);
    return 0;
}
