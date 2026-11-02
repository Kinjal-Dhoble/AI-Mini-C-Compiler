int steps(int n) {
    int count = 0;
    while (n != 1) {
        if (n % 2 == 0) {
            n = n / 2;
        } else {
            n = 3 * n + 1;
        }
        count = count + 1;
    }
    return count;
}

int main() {
    int start;
    int longest = 0;
    int best = 1;
    for (start = 1; start < 30; start = start + 1) {
        int s = steps(start);
        if (s > longest) {
            longest = s;
            best = start;
        }
    }
    print(best);
    print(longest);
    return best;
}
