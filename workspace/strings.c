// Strings are char arrays that end with '\0'. This program uses the string functions
// strlen, strcpy, strcat, strcmp and the character functions toupper / isalpha.
#include <stdio.h>
#include <string.h>
#include <ctype.h>

int countVowels(char word[]) {
    int count = 0;
    int i;
    for (i = 0; i < strlen(word); i = i + 1) {
        char c = tolower(word[i]);
        if (c == 'a' || c == 'e' || c == 'i' || c == 'o' || c == 'u') {
            count = count + 1;
        }
    }
    return count;
}

void reverse(char text[]) {
    int left = 0;
    int right = strlen(text) - 1;
    while (left < right) {
        char keep = text[left];
        text[left] = text[right];
        text[right] = keep;
        left = left + 1;
        right = right - 1;
    }
}

int main() {
    char first[20] = "Kinjal";
    char last[] = "Dhoble";
    char full[40];
    char shout[20];
    int i;
    strcpy(full, first);
    strcat(full, " ");
    strcat(full, last);
    printf("Full name: %s (%d characters)\n", full, strlen(full));
    printf("Vowels in %s: %d\n", full, countVowels(full));
    strcpy(shout, first);
    for (i = 0; shout[i] != '\0'; i = i + 1) {
        shout[i] = toupper(shout[i]);
    }
    printf("Shouted: %s, first letter %c\n", shout, shout[0]);
    reverse(shout);
    printf("Reversed: %s\n", shout);
    if (strcmp(first, "Kinjal") == 0) {
        printf("strcmp says the names are equal\n");
    }
    char names[3][10] = {"Umesh", "Adhitya", "Kinjal"};
    for (i = 0; i < 3; i = i + 1) {
        printf("%-8s starts with %c and is %d long\n", names[i], names[i][0], strlen(names[i]));
    }
    return 0;
}
