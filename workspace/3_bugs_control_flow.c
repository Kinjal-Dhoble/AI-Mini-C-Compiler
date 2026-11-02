// DEMO 3d - FIND BUGS: control-flow mistakes. Click "Find bugs".
// Running confirms the fall-through; the stray ';' at the end makes the loop run forever (the VM stops it).
#include <stdio.h>

int main() {
    int day = 2;
    switch (day) {
        case 1: printf("Monday\n"); break;
        case 2: printf("Tuesday\n");             // 1. no break: also prints Wednesday
        case 3: printf("Wednesday\n"); break;
        default: printf("Another day\n");
    }

    int mark = 120;
    if (mark < 0 && mark > 100) {                // 2. impossible: should be ||
        printf("Invalid mark\n");
    }

    int scores[6] = {4, 8, 15, 16, 23, 42};
    for (int i = 0; i < 6; i++) {
        printf("%d\n", scores[i]);
        i += 1;                                  // 3. skips every other score
    }

    int n = 3;
    while (n > 0);                               // 4. stray ';': the loop never ends
    {
        n--;
    }
    printf("Done\n");
    return 0;
}
