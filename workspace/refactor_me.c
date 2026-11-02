// Try the "Refactor" action on this program.
int is_even(int n) {
    if (n % 2 == 0) {
        return 1;
    } else {
        return 0;
    }
}

int in_range(int x, int low, int high) {
    if (x >= low) {
        if (x <= high) {
            return 1;
        }
    }
    return 0;
}

int main() {
    int total = 0;
    int i = 0;
    while (i < 10) {
        // add only the even numbers
        if ((is_even(i) == 1) == 1) {
            total = total + i;
        }
        i = i + 1;
    }
    int d = 3;
    if (d != 0) {
        if (total / d > 5) {
            print(total / d);
        }
    }
    if (1) {
        print(total);
    }
    if (total > 100) {
    } else {
        print(in_range(total, 10, 50));
    }
    return 0;
}
