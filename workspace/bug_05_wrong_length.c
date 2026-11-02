// Find bugs demo 5: prints an array with a helper function.
#include <stdio.h>

void printAll(int a[], int n) {
    int i;
    for (i = 0; i < n; i = i + 1) {
        printf("%d ", a[i]);
    }
    printf("\n");
}

int main() {
    int scores[5] = {9, 7, 10, 8, 6};
    printAll(scores, 10);
    return 0;
}
