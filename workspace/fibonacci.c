int main() {
    int a = 0;
    int b = 1;
    int i;
    for (i = 0; i < 12; i = i + 1) {
        print(a);
        int next = a + b;
        a = b;
        b = next;
    }
    return a;
}
