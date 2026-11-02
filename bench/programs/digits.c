int digit_sum(int n) {
    int total = 0;
    while (n > 0) {
        total = total + n % 10;
        n = n / 10;
    }
    return total;
}

int reverse(int n) {
    int r = 0;
    while (n > 0) {
        r = r * 10 + n % 10;
        n = n / 10;
    }
    return r;
}

int main() {
    int value = 12345;
    print(digit_sum(value));
    print(reverse(value));
    if (reverse(121) == 121) {
        print(1);
    }
    return 0;
}
