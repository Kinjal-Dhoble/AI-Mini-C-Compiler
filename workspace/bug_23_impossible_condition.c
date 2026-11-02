// Find bugs demo 23: a mark from 0 to 100 is valid; anything else is an error.
#include <stdio.h>

int main() {
    int mark = 120;
    if (mark < 0 && mark > 100) {
        printf("Invalid mark\n");
    } else {
        printf("Mark %d saved\n", mark);
    }
    return 0;
}
