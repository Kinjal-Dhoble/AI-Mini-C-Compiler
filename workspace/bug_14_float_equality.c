// Find bugs demo 14: 0.1 + 0.2 should be 0.3, so this should print "equal".
#include <stdio.h>

int main() {
    float price = 0.1f;
    double sum = 0.1 + 0.2;
    if (price == 0.1) {
        printf("price is 0.1\n");
    } else {
        printf("price is not 0.1?\n");
    }
    if (sum == 0.3) {
        printf("equal\n");
    } else {
        printf("not equal: %.17f\n", sum);
    }
    return 0;
}
