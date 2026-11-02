// Functions, recursion and the call graph. Try Debug, then "Step into" a call.
int calls = 0;

int factorial(int n) {
    calls = calls + 1;
    if (n <= 1) {
        return 1;
    }
    return n * factorial(n - 1);
}

int gcd(int a, int b) {
    while (b != 0) {
        int r = a % b;
        a = b;
        b = r;
    }
    return a;
}

void show(int label, int value) {
    print(label);
    print(value);
}

int main() {
    show(1, factorial(6));
    show(2, gcd(84, 36));
    print(calls);
    return 0;
}
