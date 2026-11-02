// Reads numbers with scanf. Type them in the Input box (IDE) or at the prompt (command line).
#include <stdio.h>

int main() {
    int n;
    int i;
    int total = 0;
    printf("How many numbers? ");
    scanf("%d", &n);
    for (i = 1; i <= n; i = i + 1) {
        int x;
        printf("Number %d: ", i);
        scanf("%d", &x);
        total = total + x;
    }
    printf("\nSum = %d\n", total);
    if (n > 0) {
        printf("Average = %d\n", total / n);
    }
    return 0;
}
