// Find bugs demo 9: builds a greeting by joining two strings.
#include <stdio.h>
#include <string.h>

int main() {
    char greeting[13] = "Good morning";
    strcat(greeting, ", Adhitya!");
    printf("%s\n", greeting);
    return 0;
}
