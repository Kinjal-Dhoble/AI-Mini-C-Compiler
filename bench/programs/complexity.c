// Try "Explain flow": the compiler finds the loops, the AI explains the Big-O.
int sum_of_divisors(int n) {
    int total = 0;
    int d;
    for (d = 1; d <= n; d = d + 1) {
        if (n % d == 0) {
            total = total + d;
        }
    }
    return total;
}

int count_pairs(int n, int target) {
    int count = 0;
    int i;
    int j;
    for (i = 1; i <= n; i = i + 1) {
        for (j = 1; j <= n; j = j + 1) {
            if (i + j == target) {
                count = count + 1;
            }
        }
    }
    return count;
}

int fib(int n) {
    if (n < 2) {
        return n;
    }
    return fib(n - 1) + fib(n - 2);
}

int main() {
    print(sum_of_divisors(28));
    print(count_pairs(100, 50));
    print(fib(20));
    return 0;
}
