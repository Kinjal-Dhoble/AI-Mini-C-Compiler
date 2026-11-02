// yacc_demo.c - exercises the LALR(1) grammar: precedence, dangling else,
// functions, recursion, loops, printf and scanf.
#include <stdio.h>

int power(int base, int exp) {
    if (exp == 0)
        return 1;
    return base * power(base, exp - 1);
}

int classify(int n) {
    // dangling else: this else belongs to the inner if
    if (n > 0)
        if (n > 100) return 2;
        else return 1;
    return 0;
}

int main() {
    int a;
    int b;
    int i;
    int sum = 0;

    printf("Enter two numbers: ");
    scanf("%d %d", &a, &b);

    // precedence: * before +, && before ||
    printf("a + b * 2 = %d\n", a + b * 2);
    printf("(a + b) * 2 = %d\n", (a + b) * 2);
    if (a > 0 && b > 0 || a == b)
        printf("both positive, or equal\n");

    for (i = 1; i <= b; i = i + 1) {
        sum = sum + i;
    }
    printf("1 + ... + %d = %d\n", b, sum);

    while (a > 9) {
        a = a / 10;
    }
    printf("first digit of a: %d\n", a);
    printf("2^b = %d\n", power(2, b));
    printf("class of sum: %d\n", classify(sum));
    return 0;
}
