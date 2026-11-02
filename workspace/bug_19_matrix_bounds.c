// Find bugs demo 19: adds up every number in a 3 x 3 table.
#include <stdio.h>

int main() {
    int grid[3][3] = {{1, 2, 3}, {4, 5, 6}, {7, 8, 9}};
    int sum = 0;
    int r;
    int c;
    for (r = 0; r < 3; r = r + 1) {
        for (c = 1; c <= 3; c = c + 1) {
            sum = sum + grid[r][c];
        }
    }
    printf("Sum: %d\n", sum);
    return 0;
}
