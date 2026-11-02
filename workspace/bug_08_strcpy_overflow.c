// Find bugs demo 8: copies a student's full name into an array.
#include <stdio.h>
#include <string.h>

int main() {
    char name[8];
    strcpy(name, "Kinjal Dhoble");
    printf("Student: %s\n", name);
    return 0;
}
