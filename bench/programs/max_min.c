int max(int a, int b) {
    if (a > b) {
        return a;
    }
    return b;
}

int min(int a, int b) {
    if (a < b) {
        return a;
    }
    return b;
}

int clamp(int x, int low, int high) {
    return max(low, min(x, high));
}

int main() {
    print(clamp(5, 0, 10));
    print(clamp(0 - 3, 0, 10));
    print(clamp(42, 0, 10));
    return 0;
}
