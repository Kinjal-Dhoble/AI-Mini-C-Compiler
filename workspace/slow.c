// Try the AI "Optimise" feature on this one.
int main() {
    int sum = 0;
    int i;
    int k = 4;
    int unused = 7;
    for (i = 0; i < 10; i = i + 1) {
        sum = sum + k * 2;
    }
    sum = sum + 0;
    print(sum);
    return sum;
}
