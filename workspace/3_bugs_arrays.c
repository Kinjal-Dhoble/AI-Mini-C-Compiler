// DEMO 3c - FIND BUGS: array mistakes. Click "Find bugs".
// Plain gcc runs straight past the end of the arrays and prints garbage; Mini C stops at the exact line.
#include <stdio.h>

int average(int values[]) {
    int n = sizeof(values) / sizeof(values[0]);  // 1. sizeof on a parameter is the size of an address
    int sum = 0;
    for (int i = 0; i < n; i++) {
        sum += values[i];
    }
    return sum / n;
}

int main() {
    int ages[5] = {18, 19, 20, 21, 22};
    printf("Average age: %d\n", average(ages));

    int count[10];                               // 2. never set to 0 before counting
    int total = 0;
    int marks[5] = {70, 85, 90, 65, 79};
    for (int i = 0; i <= 5; i++) {               // 3. i <= 5 reads marks[5]: out of bounds (stops here)
        total += marks[i];
    }
    count[1] = count[1] + 1;
    printf("Total: %d %d\n", total, count[1]);
    return 0;
}
