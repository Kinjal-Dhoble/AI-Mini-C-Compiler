// Find bugs demo 10: copies only the letters of a code into a new string.
#include <stdio.h>
#include <ctype.h>

int main() {
    char code[] = "CS-2026-AI";
    char letters[11];
    int i;
    int k = 0;
    for (i = 0; code[i] != '\0'; i = i + 1) {
        if (isalpha(code[i])) {
            letters[k] = code[i];
            k = k + 1;
        }
    }
    printf("Letters only: %s\n", letters);
    return 0;
}
