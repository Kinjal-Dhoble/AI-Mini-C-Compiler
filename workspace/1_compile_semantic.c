// DEMO 1c - SEMANTIC ERRORS (the grammar is fine, but the meaning is wrong)
#include <stdio.h>

const int MAX_MARKS = 100;

int average(int a, int b) {
    return (a + b) / 2;
}

void show(int v) {
    printf("%d\n", v);
    return v;                            // 1. a void function cannot return a value
}

int main() {
    int count = 3;
    int count = 4;                       // 2. 'count' is declared twice
    total = 10;                          // 3. 'total' was never declared
    int avg = average(70);               // 4. average() needs 2 arguments
    int x = show(5);                     // 5. show() is void: it has no value to store
    MAX_MARKS = 90;                      // 6. a const cannot be changed
    printMarks(avg);                     // 7. this function does not exist
    break;                               // 8. break outside a loop or switch
    return 0;
}
