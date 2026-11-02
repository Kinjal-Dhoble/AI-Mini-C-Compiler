// Find bugs demo 6: counts how often each digit 0-9 appears in a number.
#include <stdio.h>

int main() {
    int count[10];
    int number = 4422791;
    while (number > 0) {
        int digit = number % 10;
        count[digit] = count[digit] + 1;
        number = number / 10;
    }
    printf("The digit 2 appears %d times\n", count[2]);
    return 0;
}
