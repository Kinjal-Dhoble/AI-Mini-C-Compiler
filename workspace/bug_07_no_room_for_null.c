// Find bugs demo 7: stores a word and prints how long it is.
#include <stdio.h>
#include <string.h>

int main() {
    char word[5] = "hello";
    printf("%s has %d letters\n", word, strlen(word));
    return 0;
}
