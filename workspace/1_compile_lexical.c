// DEMO 1a - LEXICAL ERRORS (the lexer rejects characters C does not allow)
// Press Run or just look at the Problems panel: every error is listed at once, not only the first.
#include <stdio.h>

int main() {
    int total = 10 @ 5;           // 1. '@' is not a C operator
    char grade = 'AB';            // 2. a char holds ONE character
    int price = 100 $;            // 3. '$' is not allowed in C
    printf("Total %d\n", total ? 1 : 0);   // 4. ?: is not supported in Mini C
    printf("unclosed text);       // 5. the string is never closed
    return 0;
}
