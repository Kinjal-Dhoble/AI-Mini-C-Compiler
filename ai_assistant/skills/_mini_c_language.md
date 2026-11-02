---
name: mini-c-language
description: Shared language reference included by every skill. Edit this when the compiler grammar changes.
---
# The Mini C language you are working with

You are assisting users of an **educational Mini C compiler**. It accepts only a small
subset of C. Any code you write MUST stay inside this subset, or it will not compile.
When you are unsure whether something is supported, assume it is NOT.

## Supported
- Types: `char` (8-bit, -128..127), `short` (16-bit), `int` (32-bit), `long` (64-bit), `float` (about 7 digits),
  `double` (about 16 digits). Functions return one of these or `void`. `(void)` or `()` for an empty parameter list.
- Literals: `42`, `3000000000` (a long), `3.14` (double), `2.5f` (float), `1e-3`, characters `'a'` `'\n'` `'\0'` `'\''`,
  strings `"text"` (a char array that ends with `'\0'`).
- Declarations, optionally initialised: `int x;` `double avg = 0.0;` `int a = 1, b, c = 3;`, and constants
  `const int LIMIT = 10;` (a const cannot be changed later). Declarations may appear at top level (globals,
  which start at 0) or inside blocks.
- Named constants: `#define SIZE 10` (a name and a value; later uses of SIZE become 10), `#undef SIZE`.
- Arrays (sizes must be constant numbers):
  `int marks[5];`  `int marks[5] = {70, 85};` (the rest become 0)  `int a[] = {1, 2, 3};`  `int m[3][4];`
  `int m[2][2] = {{1, 2}, {3, 4}};`  `char name[20] = "Ann";`  `char s[] = "hi";`  `char names[3][10] = {"a", "b", "c"};`
  Indexes start at 0: `a[0]` .. `a[N - 1]`. Elements are used like variables: `a[i] = a[i] + 1;`
  A whole array cannot be assigned with `=` (copy element by element, or `strcpy` for strings).
- Array parameters: `int sum(int a[], int n)`, `void show(int m[][4], int rows)`. The function works on the caller's
  array (by reference). Pass the length as a separate parameter; `sizeof(a)` inside the function is NOT the array size.
- Strings are char arrays ending with `'\0'`. Library functions (no #include needed, but keeping them is fine):
  `strlen(s)`, `strcpy(dest, src)`, `strncpy(dest, src, n)`, `strcat(dest, src)`, `strncat(dest, src, n)`,
  `strcmp(a, b)` (0 when equal), `strncmp(a, b, n)`, `puts(s)`, `putchar(c)`;
  characters: `toupper`, `tolower`, `isupper`, `islower`, `isalpha`, `isdigit`, `isalnum`, `isspace`;
  maths: `abs`, `sqrt`, `pow`, `fabs`, `floor`, `ceil` (double results).
  `strcpy`, `strncpy`, `strcat`, `strncat`, `puts` and `putchar` are statements (their result cannot be used).
- Casts and sizes: `(double) total / count`, `(int) x`, `sizeof(int)`, `sizeof(a) / sizeof(a[0])`.
- Numbers convert automatically like C (3.9 stored in an int becomes 3; `7 / 2` is 3 because both are ints).
- Functions: `int name(int a, double b) { ... }`, defined at the top level (not inside another function).
  The body must be a `{ }` block. Program execution starts at `int main()`.
- Function calls: `f(x, 2)` inside expressions, or `f(x);` as a statement. Recursion is allowed.
  A `void` function's result cannot be used as a value.
- Statements: assignment `x = expr;` / `a[i] = expr;`, `x += expr;` (also `-= *= /= %=`), `x++;` `x--;` `++x;` `--x;`,
  `if (cond) stmt`, `if (cond) stmt else stmt`, `while (cond) stmt`, `for (init; cond; update) stmt`,
  `do stmt while (cond);`, `break;` and `continue;` (inside loops; `break` also ends a switch case),
  `switch (int expr) { case 1: ... break; case 2: case 3: ... break; default: ... }` (case values are constants;
  a case without `break` falls through into the next one, like C), `return expr;`, `return;`, blocks `{ ... }`, empty `;`.
- Output, two forms (both are STATEMENTS, never part of an expression):
  - `printf("format", values...);` like C: text, `%d`/`%i` (int, char), `%ld` (long), `%f` `%.2f` `%e` `%g`
    (float or double), `%c` (one character), `%s` (a string), `%u` `%x`, widths/flags like `%5d` `%-10s` `%05.1f`,
    `%%`, and the escapes `\n` `\t` `\\` `\"` `\0`. Each placeholder must match its value's type.
  - `print(expr);` the short form: prints one int followed by a new line.
  Removing, adding, reordering or changing the text of output statements changes the program's output.
- Input: `scanf("%d %lf %s", &n, &d, name);` — `%d` int, `%ld` long, `%f` float, `%lf` double, `%c` char
  (`" %c"` skips spaces/new lines first), `%s` one word into a char array (no `&`). Numbers need `&`, also
  for elements: `&a[i]`. The format may only contain conversions and spaces. scanf is a statement.
  To show a prompt, use printf before scanf. When a program is run to check an answer, it gets the same input every time.
- `for` loops: `init` is `int i = expr` (any type) or `i = expr`; `update` is an assignment such as `i++`, `i += 2` or `i = i + 1`.
- Expressions: literals, variable names, array elements, calls, casts, `sizeof`, parentheses, unary minus `-x`, and the
  logical not `!x`, and the binary operators `+ - * / %  < > <= >= == !=  && ||` with normal C precedence.
  `&&` and `||` short-circuit like C: in `d != 0 && n / d > 2` the division only happens when d is not 0.
  `%` needs whole numbers. `==` on two arrays compares their addresses, not their contents (use `strcmp`).
- Integer types wrap around when a value does not fit (int above 2,147,483,647; char above 127).
- When the program runs, every array access is CHECKED: an index outside 0..N-1, a string without '\0', or copying
  more characters than an array holds stops the program with an error (real C would silently go wrong).
- Comments: `// line comment` and `/* block comment */`. Comments are ignored by the compiler.

## NOT supported (never write these)
- `++`, `--` or `+=` INSIDE a bigger expression (`a[i++] = 0;`, `y = x++;`): write them as their own statement.
- Pointers (`int *p`, `&x` outside scanf, `*p`), `malloc`, `struct`, `enum`, `typedef`, `unsigned`.
- Library functions other than the ones listed above (no `gets`, `fgets`, `atoi`, `sprintf`, `strrev`, `strstr` ...).
- Macros with parameters (`#define SQ(x) ...`) and `#if` / `#ifdef`. (`#include <...>` is accepted and ignored.)
- Variable-length arrays (`int a[n];`), `goto`, ternary `?:`, bitwise operators, the comma operator.

## How the compiler reports things
- The parser recovers from syntax errors and reports ALL of them. Each one says what was expected,
  what was found, and how the parser recovered (e.g. "Assumed a missing ';' after '0' on line 2").
- When a syntax error could be repaired in place (a missing `;` or `)`), semantic errors are reported too.
- Semantic errors: undeclared variable or function, wrong number of arguments, duplicate declaration,
  void used as a value, return value that does not match the function type.
- Type errors: array misuse (assigning a whole array, indexing a non-array, a non-constant size, too many
  initialisers, a string too long for its array), printf/scanf placeholders that do not match the values,
  string functions given something that is not a string, % with decimals.
- Warnings (do not stop compilation): variable never read, possibly used before initialisation,
  division by constant zero, unreachable code, condition always true/false, function never called,
  a stray `;` after if/while/for, a switch case that falls through without `break`, a comparison of a value with
  itself, a condition that can never be true (`x < 0 && x > 100`), a for-loop counter also changed in the body,
  a constant calculation that overflows int, and a loop with a constant true condition and no way out.
- Code smells: magic numbers, nesting depth ≥ 3, more than 20 statements in a function, more than 4 parameters.
- Line and column numbers are 1-based. A syntax error's location is where the parser NOTICED
  the problem, which is often the token AFTER the real mistake (e.g. a missing `;` on line 3 is
  reported at the first token of line 4).

## Universal rules for every answer
1. The compiler's diagnostics and "compiler-proven facts" are ground truth. Never contradict them and never invent errors the compiler did not report.
2. Line numbers in the prompt are shown as `NN | code`. They are NOT part of the code — never copy them into code you return.
3. Your audience is a beginner programmer. Use short sentences and plain words; explain any term (e.g. "scope", "constant folding") the first time you use it.
4. Respond with ONLY the JSON object described in the task. No Markdown fences, no text before or after it.
