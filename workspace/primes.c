// Prints every prime number below 50 and returns how many there are.
int main() {
    int n;
    int count = 0;
    for (n = 2; n < 50; n = n + 1) {
        int d = 2;
        int prime = 1;
        while (d * d <= n && prime == 1) {
            if (n % d == 0) { prime = 0; }
            d = d + 1;
        }
        if (prime == 1) {
            print(n);
            count = count + 1;
        }
    }
    return count;
}
