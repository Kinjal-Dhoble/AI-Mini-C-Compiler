int power(int base, int exp) {
    int result = 1;
    int i;
    for (i = 0; i < exp; i = i + 1) {
        result = result * base;
    }
    return result;
}

int main() {
    print(power(2, 10));
    print(power(3, 5));
    print(power(7, 0));
    return 0;
}
