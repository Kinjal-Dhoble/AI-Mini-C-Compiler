// Try the "Find bugs" action on this program. It has several mistakes.
int average(int total, int count) {
    return total / count;
}

int factorial(int n) {
    return n * factorial(n - 1);
}

int sign(int x) {
    if (x > 0) { return 1; }
    if (x < 0) { return 0 - 1; }
}

int main() {
    int sum = 0;
    int i;
    for (i = 0; i < 10; i = i + 1) {
        sum = sum + i;
    }
    sum == 100;
    int big = 50000;
    print(big * big);
    print(sign(0) + 1);
    print(average(sum, i - 10));
    return 0;
}
