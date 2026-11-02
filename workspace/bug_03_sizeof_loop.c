// Find bugs demo 3: prints every price in the list using sizeof to get the length.
#include <stdio.h>

int main() {
    int prices[4] = {120, 75, 300, 45};
    int i;
    for (i = 0; i < sizeof(prices); i = i + 1) {
        printf("Item %d costs %d\n", i + 1, prices[i]);
    }
    return 0;
}
