// Find bugs demo 18: keeps only the first 5 letters of a course name.
#include <stdio.h>
#include <string.h>

int main() {
    char course[] = "Compilers";
    char shortName[5];
    strncpy(shortName, course, 5);
    printf("Short name: %s\n", shortName);
    return 0;
}
