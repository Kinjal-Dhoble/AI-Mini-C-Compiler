// Arrays: a list of marks, a 2-D table, and arrays passed to functions.
// Run it (F5), then Debug (F9) and watch the Variables tab show each array change.
#include <stdio.h>

int total(int marks[], int n) {
    int sum = 0;
    int i;
    for (i = 0; i < n; i = i + 1) {
        sum = sum + marks[i];
    }
    return sum;
}

int highest(int marks[], int n) {
    int best = marks[0];
    int i;
    for (i = 1; i < n; i = i + 1) {
        if (marks[i] > best) {
            best = marks[i];
        }
    }
    return best;
}

void sortMarks(int marks[], int n) {
    int i;
    int j;
    for (i = 0; i < n - 1; i = i + 1) {
        for (j = 0; j < n - 1 - i; j = j + 1) {
            if (marks[j] > marks[j + 1]) {
                int keep = marks[j];
                marks[j] = marks[j + 1];
                marks[j + 1] = keep;
            }
        }
    }
}

int main() {
    int marks[5] = {72, 85, 64, 90, 78};
    int table[3][3] = {{1, 2, 3}, {4, 5, 6}, {7, 8, 9}};
    int i;
    int diagonal = 0;
    printf("Total: %d, highest: %d\n", total(marks, 5), highest(marks, 5));
    sortMarks(marks, 5);
    printf("Sorted:");
    for (i = 0; i < 5; i = i + 1) {
        printf(" %d", marks[i]);
    }
    printf("\n");
    for (i = 0; i < 3; i = i + 1) {
        diagonal = diagonal + table[i][i];
    }
    printf("Diagonal sum of the 3x3 table: %d\n", diagonal);
    printf("marks takes %d bytes: %d ints of %d bytes\n", sizeof(marks), sizeof(marks) / sizeof(marks[0]), sizeof(int));
    return 0;
}
