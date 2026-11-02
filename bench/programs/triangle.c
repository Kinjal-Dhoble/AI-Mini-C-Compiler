int main() {
    int n = 10;
    int row;
    int total = 0;
    for (row = 1; row <= n; row = row + 1) {
        int col;
        for (col = 1; col <= row; col = col + 1) {
            total = total + 1;
        }
    }
    print(total);
    int formula = n * (n + 1) / 2;
    print(formula);
    if (total == formula) {
        print(1);
    } else {
        print(0);
    }
    return 0;
}
