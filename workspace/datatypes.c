// Every data type: char, short, int, long, float and double, with printf formats and casts.
#include <stdio.h>
#include <math.h>

double circleArea(double radius) {
    return 3.14159 * radius * radius;
}

int main() {
    char grade = 'A';
    short year = 2026;
    int students = 63;
    long population = 1400000000;
    float price = 49.99f;
    double pi = 3.141592653589793;
    int total = 457;
    int count = 6;
    printf("char   %c (code %d), next letter %c\n", grade, grade, grade + 1);
    printf("short  %d, int %d, long %ld\n", year, students, population * 3);
    printf("float  %.2f, double %.10f\n", price, pi);
    printf("Average with (double): %.3f\n", (double) total / count);
    printf("Average without a cast: %d\n", total / count);
    printf("Circle of radius 2: %.4f, sqrt(2) = %f, 2^10 = %.0f\n", circleArea(2), sqrt(2.0), pow(2, 10));
    printf("sizes: char %d, short %d, int %d, long %d, float %d, double %d\n",
           sizeof(char), sizeof(short), sizeof(int), sizeof(long), sizeof(float), sizeof(double));
    return 0;
}
