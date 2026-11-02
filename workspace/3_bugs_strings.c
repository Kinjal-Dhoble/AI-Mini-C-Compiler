// DEMO 3b - FIND BUGS: string mistakes. Click "Find bugs".
// The quiet mistakes come first; the buffer overflow at the end makes the program stop.
#include <stdio.h>
#include <string.h>

int main() {
    char typed[10] = "secret";
    char saved[10] = "secret";
    if (typed == saved) {                        // 1. == compares addresses, not the text
        printf("Login ok\n");
    } else {
        printf("Wrong password\n");
    }
    if (strcmp(typed, saved)) {                  // 2. strcmp gives 0 when EQUAL, so this means "different"
        printf("Same text\n");
    }
    char shortName[5];
    strncpy(shortName, "Compilers", 5);          // 3. strncpy fills all 5 places, no '\0' left
    char name[8];
    strcpy(name, "Christopher Nolan");           // 4. 17 letters into 8 places: buffer overflow (stops here)
    printf("%s\n", name);
    return 0;
}
