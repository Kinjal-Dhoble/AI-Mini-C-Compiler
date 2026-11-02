// Find bugs demo 15: converts 75 degrees Fahrenheit to Celsius (about 23.9 degrees).
#include <stdio.h>

int main() {
    double fahrenheit = 75.0;
    int celsius = (fahrenheit - 32) * 5 / 9;
    printf("%.1f F is %d C\n", fahrenheit, celsius);
    return 0;
}
