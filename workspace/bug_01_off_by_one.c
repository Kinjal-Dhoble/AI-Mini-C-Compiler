// Find bugs demo 1: adds up 5 marks. Compiles fine, but the total is wrong. Why?
#include <stdio.h>

int main() {
    int marks[5] = {72, 85, 64, 90, 78};
    int total = 0;
    int i;
    for (i = 0; i <= 5; i = i + 1) {
        total = total + marks[i];
    }
    printf("Total marks: %d\n", total);
    return 0;
}
