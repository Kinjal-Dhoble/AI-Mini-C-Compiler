// Find bugs demo 16: keeps a score in a char to save memory.
#include <stdio.h>

int main() {
    char score = 100;
    score = score + 50;
    printf("Score: %d\n", score);
    char bonus = 200;
    printf("Bonus: %d\n", bonus);
    return 0;
}
