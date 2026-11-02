int main() {
    int a = 0;
    int b = 5;
    
    // Avoid division by zero by checking a before dividing
    if (a != 0) {
        int c = b / a;
        printf("%d", c);
    } else {
        printf("Division by zero error");
    }
    return 0;
}
