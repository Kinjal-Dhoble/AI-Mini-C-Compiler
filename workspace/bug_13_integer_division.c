// Find bugs demo 13: the average of 3 marks. 70, 85 and 90 should give 81.67.
#include <stdio.h>

int main() {
    int a = 70;
    int b = 85;
    int c = 90;
    double average = (a + b + c) / 3;
    printf("Average: %.2f\n", average);
    return 0;
}
