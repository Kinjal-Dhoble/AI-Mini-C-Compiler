// Find bugs demo 11: checks a password. It always says "Wrong password", even when it is right.
#include <stdio.h>

int main() {
    char saved[10] = "secret";
    char typed[10] = "secret";
    if (typed == saved) {
        printf("Welcome!\n");
    } else {
        printf("Wrong password\n");
    }
    return 0;
}
