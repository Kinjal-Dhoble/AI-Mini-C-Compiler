// DEMO 4 - RUNTIME ERRORS: the program compiles, then crashes in a way real C does not report clearly.
// Type ONE number in the Input box, then press Run:
//   1 = array index out of bounds      5 = string copy overflow
//   2 = division by zero               6 = scanf runs out of input
//   3 = variable used before a value   7 = loop that never ends
//   4 = endless recursion (stack overflow)
// Anything else runs normally. Each one stops at the exact line with a plain message.
#include <stdio.h>
#include <string.h>

int depth(int n) {
    return depth(n + 1) + 1;                     // no base case: never stops calling itself
}

int main() {
    int choice;
    int marks[3] = {70, 80, 90};
    scanf("%d", &choice);
    if (choice == 1) {
        int i = 3;
        printf("%d\n", marks[i]);                // index 3 of an array of 3
    }
    if (choice == 2) {
        int students = 0;
        printf("%d\n", 250 / students);          // divide by zero
    }
    if (choice == 3) {
        int total;
        total = total + 5;                       // total never had a value
        printf("%d\n", total);
    }
    if (choice == 4) {
        printf("%d\n", depth(0));
    }
    if (choice == 5) {
        char city[6];
        strcpy(city, "Bangalore");               // 9 letters + '\0' into 6 places
        printf("%s\n", city);
    }
    if (choice == 6) {
        int a;
        int b;
        scanf("%d %d", &a, &b);                  // only one number was typed
        printf("%d\n", a + b);
    }
    if (choice == 7) {
        int k = 0;
        while (k < 10) {
            printf("k = %d\n", k);               // k is never changed
        }
    }
    printf("Finished normally (choice %d)\n", choice);
    return 0;
}
