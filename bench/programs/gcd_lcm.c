int gcd(int a, int b) {
    while (b != 0) {
        int r = a % b;
        a = b;
        b = r;
    }
    return a;
}

int lcm(int a, int b) {
    return a / gcd(a, b) * b;
}

int main() {
    int x = 84;
    int y = 36;
    print(gcd(x, y));
    print(lcm(x, y));
    return 0;
}
