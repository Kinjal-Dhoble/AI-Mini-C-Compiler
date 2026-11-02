// Find bugs demo 20: count down from 5 to 1.
#include <stdio.h>

int main() {
    int n = 5;
    while (n > 0);
    {
        printf("%d\n", n);
        n--;
    }
    printf("Lift off!\n");
    return 0;
}
