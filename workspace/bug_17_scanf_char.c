// Find bugs demo 17: reads an age, then asks Y or N. Type for example: 20 Y
#include <stdio.h>

int main() {
    int age;
    char answer;
    printf("Age? ");
    scanf("%d", &age);
    printf("Member (Y/N)? ");
    scanf("%c", &answer);
    if (answer == 'Y') {
        printf("Member aged %d\n", age);
    } else {
        printf("Not a member (answer was code %d)\n", answer);
    }
    return 0;
}
