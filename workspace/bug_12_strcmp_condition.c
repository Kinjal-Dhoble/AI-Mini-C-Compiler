// Find bugs demo 12: says whether two usernames are the same.
#include <stdio.h>
#include <string.h>

int main() {
    char first[10] = "umesh";
    char second[10] = "umesh";
    if (strcmp(first, second)) {
        printf("Same user\n");
    } else {
        printf("Different users\n");
    }
    return 0;
}
