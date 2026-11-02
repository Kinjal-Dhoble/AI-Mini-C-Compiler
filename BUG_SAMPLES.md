# Find bugs: array, string and data-type mistakes

The `workspace/bug_01_*.c` ... `bug_24_*.c` programs each contain **one classic logical error**. Every one of them
**compiles without errors**: the compiler cannot reject them, because they are valid C. That is what makes them
logical errors. They compile, but they do the wrong thing when they run.

**How to try one**

* IDE: open the file, then click **Find bugs** in the AI panel. This works without Ollama too.
* Command line: `python main.py workspace/bug_01_off_by_one.c --ai bugs`

**How Find bugs finds them, in two steps**

1. **Rules**: the compiler looks at the syntax tree and the types for patterns that are usually mistakes. For
   example, `i <= 5` used as an index into an array of 5. These are *suspects*.
2. **Running**: the program is run on the MiniC virtual machine, which **checks every array access and string
   function**. If the mistake really happens, the finding is marked **confirmed by running**, with the exact
   evidence, such as "marks[5] uses index 5, but marks has 5 elements".

If Ollama is running, the AI then gives each finding a verdict and a short explanation, and can propose a full
fixed program. The compiler checks that fixed program before it is shown.

**Why it matters:** real C does **not** check any of this. The column "plain gcc" shows what the same program
does when compiled normally with gcc: it prints garbage, crashes later, or prints a wrong answer that looks
plausible. Our VM and our native build stop at the exact line instead. (The garbage values below come from
gcc on Linux; on a Mac, or with other settings, the numbers will be different. That is exactly the problem: the
result depends on whatever happens to be in memory.)

| # | File | Mistake | Rule | Confirmed by running |
|---|---|---|---|---|
| 1 | bug_01_off_by_one.c | `i <= 5` on an array of 5 | off_by_one | yes |
| 2 | bug_02_last_element.c | `temps[5]` used as the last element | array_out_of_bounds | yes |
| 3 | bug_03_sizeof_loop.c | loop up to `sizeof(prices)` | sizeof_bytes | yes |
| 4 | bug_04_sizeof_parameter.c | `sizeof` on an array parameter | sizeof_pointer | rule only |
| 5 | bug_05_wrong_length.c | wrong length passed to a function | array_size_argument | yes |
| 6 | bug_06_uninitialised_count.c | counting in an array never set to 0 | array_read_before_write | yes |
| 7 | bug_07_no_room_for_null.c | `char word[5] = "hello"` | no_room_for_terminator | yes |
| 8 | bug_08_strcpy_overflow.c | `strcpy` into a too-small array | strcpy_overflow | yes |
| 9 | bug_09_strcat_overflow.c | `strcat` past the end | strcpy_overflow | yes |
| 10 | bug_10_missing_terminator.c | string built without `'\0'` | missing_terminator | yes |
| 11 | bug_11_string_equals.c | `typed == saved` on strings | string_compare_eq | yes |
| 12 | bug_12_strcmp_condition.c | `if (strcmp(a, b))` meaning "same" | strcmp_as_bool | rule only |
| 13 | bug_13_integer_division.c | `double avg = (a + b + c) / 3;` | integer_division | yes |
| 14 | bug_14_float_equality.c | `0.1f == 0.1`, `0.1 + 0.2 == 0.3` | float_equality | yes |
| 15 | bug_15_lost_decimals.c | a double stored in an int | lost_decimals | yes |
| 16 | bug_16_char_overflow.c | 150 and 200 stored in a char | char_overflow | yes |
| 17 | bug_17_scanf_char.c | `scanf("%c")` after `scanf("%d")` | scanf_char_newline | yes |
| 18 | bug_18_strncpy.c | `strncpy` fills the array, no `'\0'` | strncpy_no_terminator | yes |
| 19 | bug_19_matrix_bounds.c | `c <= 3` in a 3 x 3 table | off_by_one | yes |
| 20 | bug_20_stray_semicolon.c | `while (n > 0);` | empty_body | yes |
| 21 | bug_21_switch_fallthrough.c | a `case` without `break` | switch_fallthrough | yes |
| 22 | bug_22_counter_changed.c | `i += 1` inside `for (...; i++)` | counter_changed | rule only |
| 23 | bug_23_impossible_condition.c | `mark < 0 && mark > 100` | impossible_condition | rule only |
| 24 | bug_24_constant_overflow.c | `days * 24 * 60 * 60 * 1000` in an int | overflow | yes |

---

## Arrays

### 1. Off-by-one loop: `bug_01_off_by_one.c`
**Meant to:** add up 5 marks (the answer is 389).
**The mistake:** `for (i = 0; i <= 5; i = i + 1)`. Indexes of `marks[5]` go from 0 to 4, so the last pass reads `marks[5]`, which does not exist.
**Plain gcc:** prints `Total marks: 33154`. A wrong number, and no error.
**Find bugs:** *Loop goes past the end of the array*, **confirmed by running**: "array index out of bounds: marks[5] uses index 5, but marks has 5 elements (valid indexes are 0 to 4)".
**Fix:** `for (i = 0; i < 5; i = i + 1)`

### 2. The last element is size - 1: `bug_02_last_element.c`
**Meant to:** print the first and the last of 5 temperatures.
**The mistake:** `temps[5]`. With 5 elements the last one is `temps[4]`.
**Plain gcc:** prints `Last day: 32767`, which is whatever happens to be in memory after the array.
**Find bugs:** *Index 5 is outside the array temps*. The rule spots it from the constant alone, and the run confirms it.
**Fix:** `temps[4]`

### 3. `sizeof` counts bytes: `bug_03_sizeof_loop.c`
**Meant to:** print the 4 prices.
**The mistake:** `i < sizeof(prices)`. `sizeof` gives **bytes**: 4 ints × 4 bytes = 16, so the loop runs 16 times.
**Plain gcc:** prints the 4 prices and then 12 lines of garbage.
**Find bugs:** *sizeof counts bytes, not elements*, confirmed: "prices[4] uses index 4, but prices has 4 elements".
**Fix:** `i < sizeof(prices) / sizeof(prices[0])`

### 4. `sizeof` on an array parameter: `bug_04_sizeof_parameter.c`
**Meant to:** average 5 ages (the answer is 20.0).
**The mistake:** inside `average(int values[])`, `sizeof(values)` is the size of an **address** (8 bytes), not of the array. So `n` is 8 / 4 = 2, and only 2 ages are averaged.
**Plain gcc:** prints `Average age: 18.5`. It does not crash, so the answer just looks plausible.
**Find bugs:** *sizeof on an array parameter* (a rule finding: nothing crashes, the answer is simply wrong).
**Fix:** pass the length: `double average(int values[], int n)` and call `average(ages, 5)`.

### 5. Wrong length passed to a function: `bug_05_wrong_length.c`
**Meant to:** print 5 scores.
**The mistake:** `printAll(scores, 10)`, but `scores` has only 5 elements.
**Plain gcc:** prints the 5 scores and then 5 garbage numbers (`32764 -1277561600 ...`).
**Find bugs:** *Function is told the array is longer than it is* (at the call, line 14). It is confirmed **inside** `printAll`: "a[5] uses index 5, but a (the array 'scores') has 5 elements". The VM knows that `a` is really `scores`.
**Fix:** `printAll(scores, 5)`

### 6. Counting in an array that was never set to 0: `bug_06_uninitialised_count.c`
**Meant to:** count how often each digit appears in 4422791.
**The mistake:** `int count[10];` starts with garbage, so `count[digit] = count[digit] + 1` adds 1 to garbage.
**Plain gcc:** happened to print the right answer this time, because the memory happened to be 0. On another machine, or with another compiler setting, it will not. This is the most dangerous kind of bug.
**Find bugs:** *count[...] is used before it was set*, confirmed: "count[1] is read before it has been given a value".
**Fix:** `int count[10] = {0};`

### 19. Off-by-one in a 2-D array: `bug_19_matrix_bounds.c`
**Meant to:** add up a 3 × 3 table (the answer is 45).
**The mistake:** `for (c = 1; c <= 3; ...)`: columns go from 0 to 2. Column 0 is skipped and column 3 does not exist.
**Plain gcc:** prints `Sum: 32810`.
**Find bugs:** *Loop goes past the end of the array*: "uses grid[...][3]. But each row of grid has 3 elements", confirmed: "grid[0][3] uses index 3, but dimension 2 of grid has 3 elements".
**Fix:** `for (c = 0; c < 3; c = c + 1)`

---

## Strings (char arrays that end with `'\0'`)

### 7. No room for the `'\0'`: `bug_07_no_room_for_null.c`
**Meant to:** print "hello" and its length.
**The mistake:** `char word[5] = "hello";`. "hello" needs **6** places: 5 letters plus the `'\0'` that marks the end. C allows this line, but then `word` is not a proper string.
**Plain gcc:** happened to print `hello has 5 letters`, by luck.
**Find bugs:** *No room for the '\0' in word*, confirmed: "strlen() went past the end of word (5 chars) without finding the '\0'". The compiler also gives a warning on the declaration line.
**Fix:** `char word[6] = "hello";` or `char word[] = "hello";`

### 8. `strcpy` into a too-small array: `bug_08_strcpy_overflow.c`
**Meant to:** store a full name.
**The mistake:** "Kinjal Dhoble" needs 14 places (13 characters + `'\0'`), but `name` has 8.
**Plain gcc:** the program **crashes** (exit -6, "stack smashing detected"). That is a buffer overflow, the classic security hole.
**Find bugs:** *strcpy writes past the end of name*, confirmed: "strcpy() needs 14 places (13 characters + '\0') but name only has 8".
**Fix:** `char name[20];`

### 9. `strcat` past the end: `bug_09_strcat_overflow.c`
**Meant to:** make "Good morning, Adhitya!".
**The mistake:** `greeting` holds 13 places, but the joined text needs 12 + 10 + 1 = 23.
**Plain gcc:** crashes (exit -6).
**Find bugs:** *strcat writes past the end of greeting*, confirmed with the exact count.
**Fix:** `char greeting[30] = "Good morning";`

### 10. String built one letter at a time with no `'\0'`: `bug_10_missing_terminator.c`
**Meant to:** keep only the letters of "CS-2026-AI", which should give "CSAI".
**The mistake:** the loop copies letters into `letters[k]` but never writes `letters[k] = '\0'` after the last one, so printf does not know where the text stops.
**Plain gcc:** prints `Letters only: CSAIssR` followed by garbage characters.
**Find bugs:** *String letters has no '\0' at its end*, confirmed: "printf("%s") read letters[4], which has no value yet".
**Fix:** after the loop, `letters[k] = '\0';`

### 11. Comparing strings with `==`: `bug_11_string_equals.c`
**Meant to:** let the user in when the password is right.
**The mistake:** `if (typed == saved)`. For arrays, `==` compares **where they are in memory**, not their letters. Two different arrays are never at the same place.
**Plain gcc:** always prints `Wrong password`.
**Find bugs:** *Strings compared with ==*, confirmed by running: "both strings contained "secret", but == gave 0 because they are two different arrays in memory". The compiler also warns about it.
**Fix:** `if (strcmp(typed, saved) == 0)`

### 12. Using `strcmp` as true/false: `bug_12_strcmp_condition.c`
**Meant to:** print "Same user" for two equal names.
**The mistake:** `if (strcmp(first, second))`. strcmp gives **0 when the strings are equal**, and 0 means false, so the program prints the opposite.
**Plain gcc:** prints `Different users`.
**Find bugs:** *strcmp result used the wrong way* (a rule finding: the program does not crash).
**Fix:** `if (strcmp(first, second) == 0)`

### 18. `strncpy` that leaves no `'\0'`: `bug_18_strncpy.c`
**Meant to:** keep the first 5 letters of "Compilers".
**The mistake:** `strncpy(shortName, course, 5)` into `shortName[5]`. strncpy copies exactly 5 characters and does **not** add `'\0'` when the source is longer.
**Plain gcc:** prints `Short name: CompiCompilers`, because printf runs on into the next array.
**Find bugs:** *strncpy may leave no '\0'*, confirmed: "printf("%s") went past the end of shortName (5 chars)".
**Fix:** `char shortName[6]; strncpy(shortName, course, 5); shortName[5] = '\0';`

---

## Number types

### 13. Whole-number division: `bug_13_integer_division.c`
**Meant to:** average 70, 85 and 90, which is 81.67.
**The mistake:** `double average = (a + b + c) / 3;`. Both sides of `/` are ints, so C divides as whole numbers **first** (245 / 3 = 81), and only then stores 81 in the double.
**Plain gcc:** prints `Average: 81.00`.
**Find bugs:** *Whole-number division loses the decimals*, confirmed: "245 / 3 gave 81: whole-number division threw away the remainder (the exact answer is 81.67)".
**Fix:** `(a + b + c) / 3.0` or `(double) (a + b + c) / 3`

### 14. Comparing decimals with `==`: `bug_14_float_equality.c`
**Meant to:** print "price is 0.1" and "equal".
**The mistake:** 0.1 cannot be stored exactly. A float keeps about 7 digits (0.100000001...) and a double about 16 (0.1000000000000000055...), so `price == 0.1` is false. In the same way, 0.1 + 0.2 is 0.30000000000000004, not 0.3.
**Plain gcc:** prints `price is not 0.1?` and `not equal: 0.30000000000000004`.
**Find bugs:** two findings, both confirmed: "0.10000000149011612 == 0.1 gave 0: the two values differ only after the 7th digit".
**Fix:** `if (fabs(sum - 0.3) < 0.000001)`, and use `0.1f` when comparing with a float.

### 15. Decimals cut off: `bug_15_lost_decimals.c`
**Meant to:** convert 75 °F to Celsius (23.9).
**The mistake:** the result is a double, but `celsius` is an int, so the decimals are **thrown away** (not rounded): 23.89 becomes 23.
**Plain gcc:** prints `75.0 F is 23 C`.
**Find bugs:** *Decimal part is cut off*, confirmed: "the decimal value 23.8889 was stored as the whole number 23".
**Fix:** `double celsius = ...;` and print it with `%.1f`

### 16. A char only holds -128 to 127: `bug_16_char_overflow.c`
**Meant to:** keep a score of 150 and a bonus of 200.
**The mistake:** a `char` is 8 bits, so 150 wraps around to -106 and 200 to -56.
**Plain gcc:** prints `Score: -106` and `Bonus: -56`.
**Find bugs:** *Value does not fit in a char* (rule), plus two **confirmed** conversions: "converting 150 to char gives -106".
**Fix:** use `int score;`

### 17. `scanf("%c")` reads the leftover space or Enter: `bug_17_scanf_char.c`
**Meant to:** read an age, then Y or N. (Input: `20 Y`.)
**The mistake:** `scanf("%d")` stops right **before** the space or Enter that the user typed after the number. `scanf("%c")` then reads that space instead of the `Y`.
**Plain gcc:** prints `Not a member (answer was code 32)`. Code 32 is a space; with Enter it would be 10.
**Find bugs:** *scanf("%c") reads the leftover space or Enter*, confirmed: "scanf("%c") read a space left over from the previous input".
**Fix:** `scanf(" %c", &answer);`. The space before `%c` skips spaces and new lines.

---

## How to present this

1. Open `bug_01_off_by_one.c` and press **Run**. Our compiler stops with "array index out of bounds: marks[5]".
   Then say: "Real gcc prints 33154 instead of 389, with no error at all." (`python tools/difftest.py` shows both.)
2. Click **Find bugs**: the rule finding appears at once, marked **confirmed by running**, with a "How to fix" line.
3. Open `bug_11_string_equals.c`: the program does not crash, but the run still proves the bug ("both strings
   contained secret, but == gave 0").
4. Show `bug_04` or `bug_12`. These are found by a rule only, because nothing crashes. Explain the difference
   between a *suspect* (rule) and *evidence* (running).
5. With Ollama on, click **Find bugs** again: the AI gives each finding a verdict and a beginner explanation, and
   **Propose a full fix** writes a corrected program that the compiler re-runs before you can apply it.

---

## Logic mistakes (version 10)

These use the C features added in version 10. C compilers accept all of them; most of them give no warning at all
without extra flags.

### 20. Stray semicolon: `bug_20_stray_semicolon.c`
**Meant to:** count down 5, 4, 3, 2, 1.
**The mistake:** `while (n > 0);`. The `;` is the whole loop body, so the loop never changes `n` and never ends. The `{ ... }` below it is just a block that would run afterwards.
**Plain gcc:** hangs, printing nothing.
**Find bugs:** *Stray ';' after if / loop*, **confirmed by running**: the run stops after 300,000 steps on line 6.
**Fix:** `while (n > 0) {`

### 21. Switch fall-through: `bug_21_switch_fallthrough.c`
**Meant to:** print only `Tuesday` for day 2.
**The mistake:** `case 2:` has no `break`, so it carries on into `case 3` and also prints `Wednesday`.
**Plain gcc:** prints `Tuesday` and `Wednesday`.
**Find bugs:** *case falls through into the next case*, **confirmed by running**: "the case on line 10 had no break, so the program went on into the case on line 12". Empty cases that share code (`case 6: case 7:`) are not reported.
**Fix:** add `break;` after `printf("Tuesday\n");`

### 22. Loop counter changed in the body: `bug_22_counter_changed.c`
**Meant to:** print all 6 scores doubled.
**The mistake:** `i += 1;` inside `for (int i = 0; i < 6; i++)`, so `i` goes up by 2 each turn and every other score is skipped.
**Plain gcc:** prints `8 30 46`, 3 numbers instead of 6, with no error.
**Find bugs:** *Loop counter changed inside the loop* (a rule finding: nothing crashes here; with an odd limit the same mistake runs past the end of the array and is confirmed).
**Fix:** remove `i += 1;`

### 23. Impossible condition: `bug_23_impossible_condition.c`
**Meant to:** reject marks outside 0 to 100.
**The mistake:** `mark < 0 && mark > 100`. No number is both below 0 and above 100, so the check never fires and 120 is saved.
**Plain gcc:** prints `Mark 120 saved`.
**Find bugs:** *Condition can never change its answer* (rule): the compiler works out that the two ranges do not overlap.
**Fix:** `mark < 0 || mark > 100`

### 24. Overflow in a constant calculation: `bug_24_constant_overflow.c`
**Meant to:** print 2592000000, the milliseconds in 30 days.
**The mistake:** `days * 24 * 60 * 60 * 1000` is worked out in `int` (all the values are ints) and overflows before it is stored in the `long`.
**Plain gcc:** prints `Milliseconds: -1702967296`.
**Find bugs:** *Integer overflow*, **confirmed by running**: "2592000 * 1000 = 2592000000 does not fit in a 32-bit int; it became -1702967296".
**Fix:** `long ms = (long) days * 24 * 60 * 60 * 1000;`

