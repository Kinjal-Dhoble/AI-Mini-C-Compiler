// DEMO 1b - SYNTAX ERRORS (the parser recovers and reports ALL of them, with how it recovered)
#include <stdio.h>

int main() {
    int marks = 75                       // 1. missing ';'
    int bonus = 5;
    if (marks > 50 {                     // 2. missing ')'
        printf("Pass\n");
    }
    marks = (marks + bonus;              // 3. missing ')' in an expression
    while (marks < 100)
        marks = marks + * 2;             // 4. an operator with nothing before it
    printf("Marks: %d\n", marks);
    return 0;
}
