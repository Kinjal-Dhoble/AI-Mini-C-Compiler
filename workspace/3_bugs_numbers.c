// DEMO 3a - FIND BUGS: number and type mistakes. Click "Find bugs".
// None of these crash, so plain C prints wrong answers that LOOK right.
// The compiler finds each one with a rule, then RUNS the program and confirms every one of them.
#include <stdio.h>

int main() {
    int a = 70;
    int b = 85;
    int c = 90;
    double average = (a + b + c) / 3;            // 1. integer division: 81, not 81.67
    printf("Average: %.2f\n", average);

    double fahrenheit = 75.0;
    int celsius = (fahrenheit - 32) * 5 / 9;     // 2. decimals lost: 23.89 stored as 23
    printf("Celsius: %d\n", celsius);

    char level = 100 + 50;                       // 3. 150 does not fit in a char: becomes -106
    printf("Level: %d\n", level);

    int days = 30;
    long ms = days * 24 * 60 * 60 * 1000;        // 4. int overflow before it is stored in the long
    printf("Milliseconds: %ld\n", ms);

    double x = 0.1 + 0.2;
    if (x == 0.3) {                              // 5. exact == on decimals is false here
        printf("Equal\n");
    } else {
        printf("Not equal\n");
    }
    return 0;
}
