// Find bugs demo 4: a function that averages an array, working out the length itself.
#include <stdio.h>

double average(int values[]) {
    int n = sizeof(values) / sizeof(values[0]);
    int sum = 0;
    int i;
    for (i = 0; i < n; i = i + 1) {
        sum = sum + values[i];
    }
    return (double) sum / n;
}

int main() {
    int ages[5] = {18, 19, 20, 21, 22};
    printf("Average age: %.1f\n", average(ages));
    return 0;
}
