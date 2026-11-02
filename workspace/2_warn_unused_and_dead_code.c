// DEMO 2a - WARNINGS: the program compiles and runs, but the compiler points at code that is wasted or risky.
// Look at the Problems panel (warnings are yellow). Press Run: it still works.
#include <stdio.h>

int helper(int v) {                      // 1. function never called
    return v * 2;
}

int main() {
    int unused = 42;                     // 2. variable never read
    int result;
    int x = 10;
    if (x > 100) {
        printf("Early: %d\n", result);   // 3. result is read here before it is given a value
    }
    result = x * 2;
    printf("Result: %d\n", result);
    if (0) {                             // 4. condition is always false: dead branch
        printf("never printed\n");
    }
    return 0;
    printf("after return\n");            // 5. unreachable code after return
}
