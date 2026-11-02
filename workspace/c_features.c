// c_features.c - the everyday C features: ++ / --, += and friends, !, break, continue,
// do-while, switch, several variables in one declaration, const and #define.
#include <stdio.h>

#define SIZE 8
#define LIMIT (SIZE * 10)

const int BONUS = 5;
int counter;                       // globals start at 0, as in C

void tick() {
    counter++;
}

int main() {
    int marks[SIZE] = {72, 45, 88, 91, 30, 67, 55, 99};
    int i, total = 0, passed = 0;

    for (i = 0; i < SIZE; i++) {
        if (marks[i] < 40) continue;        // skip fails
        total += marks[i];
        passed++;
        tick();
    }
    printf("passed %d, total %d, ticks %d\n", passed, total, counter);

    int first_high = -1;
    for (i = 0; i < SIZE; ++i) {
        if (marks[i] > 90) {
            first_high = i;
            break;                          // stop at the first one
        }
    }
    printf("first mark above 90 is at index %d\n", first_high);

    int n = 1234, digits = 0;
    do {
        n /= 10;
        digits++;
    } while (n != 0);
    printf("1234 has %d digits\n", digits);

    for (i = 1; i <= 4; i++) {
        switch (i) {
            case 1: printf("one ");  break;
            case 2:
            case 3: printf("two-or-three "); break;
            default: printf("other ");
        }
    }
    printf("\n");

    char grade = 'B';
    switch (grade) {
        case 'A': printf("excellent\n"); break;
        case 'B': printf("good\n");      break;
        default:  printf("keep going\n");
    }

    int x = 10;
    x -= 3; x *= 4; x %= 9; x += BONUS;
    printf("x = %d, !x = %d, !!x = %d\n", x, !x, !!x);
    if (!(total > LIMIT)) printf("total is at most %d\n", LIMIT);
    else printf("total is above %d\n", LIMIT);
    return 0;
}
