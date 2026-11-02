// referee_demo.c - a program for demonstrating the compiler as the AI's referee.
//
// 1. Click "Test the referee" in the AI panel (Ollama is not needed).
//    Wrong answers are built from this program and every one is rejected (red);
//    the two real improvements, folding 2 * 3 and renaming a variable, are accepted.
//
// 2. Click "Try to fool it yourself". Edit the right-hand side, pick the feature, Submit.
//    As an Optimise answer:
//      - write 6 instead of 2 * 3            -> accepted: same output
//      - change i < 5 to i <= 5              -> rejected: it prints one more line
//      - delete the last printf              -> rejected: the output changes
//      - change "total" in a printf string   -> rejected: the printed text changes
//    As a Document answer:
//      - add comments anywhere               -> accepted: code tokens unchanged
//      - add a comment and change any number -> rejected: code changed
#include <stdio.h>

int square(int n) {
    return n * n;
}

int main() {
    int scale = 2 * 3;
    int total = 0;
    int i;
    for (i = 1; i < 5; i = i + 1) {
        printf("%d squared is %d\n", i, square(i));
        total = total + square(i);
    }
    printf("total = %d, scaled = %d\n", total, total * scale);
    return 0;
}
