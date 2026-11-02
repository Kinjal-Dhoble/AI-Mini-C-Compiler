// DEMO 1d - TYPE ERRORS (arrays, strings, printf/scanf and switch used the wrong way)
#include <stdio.h>

int main() {
    int n = 5;
    int marks[3] = {70, 85, 90, 65};     // 1. 4 values for an array of 3
    int copy[3];
    char name[4] = "Vijay";              // 2. "Vijay" needs 6 places (5 letters + '\0')
    int scores[n];                       // 3. array size must be a constant
    double price = 9.5;
    copy = marks;                        // 4. a whole array cannot be assigned with =
    int r = price % 2;                   // 5. % needs whole numbers, price is a double
    printf("%d\n", price);               // 6. %d given a double
    printf("%s\n", n);                   // 7. %s given an int
    n[0] = 1;                            // 8. n is not an array
    switch (n) {
        case 1: printf("one\n"); break;
        case 1: printf("again\n"); break;   // 9. the same case twice
        case n: printf("n\n"); break;       // 10. a case value must be a constant
    }
    return 0;
}
