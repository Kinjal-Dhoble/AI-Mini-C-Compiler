// Find bugs demo 21: print the name of a day. Day 2 should print only "Tuesday".
#include <stdio.h>

int main() {
    int day = 2;
    switch (day) {
        case 1:
            printf("Monday\n");
            break;
        case 2:
            printf("Tuesday\n");
        case 3:
            printf("Wednesday\n");
            break;
        default:
            printf("Another day\n");
    }
    return 0;
}
