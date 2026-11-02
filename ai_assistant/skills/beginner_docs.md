---
name: beginner-documentation
description: Adds beginner-friendly comments to any program (compiled, failed, or AI-corrected) without changing a single code token.
temperature: 0.3
max_retries: 2
include: _mini_c_language.md
---
# Task: document code for a beginner

You will receive a program (plus its IR if it compiled). Return the SAME program
with comments added so that someone in their first programming course can follow it.

## The one hard rule
**You may only ADD comments and blank lines. Every other character of code must stay exactly
as it is** — same tokens, same order, same names, same numbers. Do not fix, optimise,
reformat, or rename anything. The compiler checks this: it strips all comments from your
version and compares the result to the original token by token. Any difference is rejected.

## What to write
1. **File header** (`/* ... */` at the very top): what the program does overall, in 1-3 sentences,
   and what value `main` returns.
2. **Before each function** (a docstring): a block comment with its purpose, each parameter, the return value, and where it fits in the call graph ("called by main", "calls itself"). Use the program-structure facts.
3. **Before each declaration**: what the variable stores (its role, not just its type).
4. **Before each `if`, `while`, `for`**: what decision or repetition happens, in plain words.
   For loops, say how many times it runs if that can be worked out, and what changes each time.
5. **Non-obvious expressions**: explain operator precedence (`10 + 20 * 3` is 70 because `*`
   happens before `+`), integer division (`7 / 2` is `3`), and `%` (remainder).
6. **Trace values** where helpful: `// x is now 70` after a constant assignment.
7. Where it really helps understanding, add ONE short note on what the compiler does with that
   line, based on the IR you were given (e.g. `// the compiler turns this into IF_FALSE ... GOTO L1`).
   Keep this to at most 2–3 notes in the whole file.

## Modes — the first line of the request tells you which one applies
- **MODE: compiled** — the program compiled. Follow the list above.
- **MODE: failed** — the program did NOT compile. Still document it as written, describing what
  each part is *trying* to do. At every compiler error (and any obvious follow-on error you can
  see) put a comment directly above or at the end of the faulty line:
  `// ERROR: <what is wrong, in plain words>. Fix: <the exact change>.`
  Remember a syntax error is often reported on the line AFTER the real mistake — put the
  `ERROR:` comment on the line that actually needs changing. Do NOT fix the code: the comments
  are the only thing you add. Do not guess values in traces for code that cannot run.
- **MODE: corrected** — you get the original program, its errors, and the AI-corrected version.
  Document the corrected version, and on every line that differs from the original add
  `// FIXED: <what was wrong before>, <why this version is right>.` In the file header, add one
  line summarising how many problems were fixed.

## Style
- Use `//` for short notes on their own line above the code, or at the end of a line.
- Use `/* ... */` for headers. Never put `*/` inside a comment's text.
- One idea per comment, at most ~80 characters per line. Do not comment closing braces or obvious things like `return x;` unless it explains WHAT value is returned and why.
- Keep the original indentation; indent a comment to match the code beneath it.

## Output — a single JSON object
{
  "overview": "2-4 sentence plain-language summary of the program, for display before the code.",
  "concepts": ["list of C concepts a beginner meets in this program, e.g. 'operator precedence', 'while loop'"],
  "documented_code": "the full program with comments added"
}

## Example
Input:
```
  1 | int main() {
  2 |     int x = 10 + 20 * 3;
  3 |     if (x >= 50) { x = x - 1; }
  4 |     return x;
  5 | }
```
Output:
{"overview": "The program calculates a number, lowers it by one if it is at least 50, and returns the result. Because 10 + 20 * 3 is 70, the final result is 69.",
 "concepts": ["variable declaration", "operator precedence", "if statement", "return value of main"],
 "documented_code": "/*\n * Computes a value, adjusts it if it is large, and returns it.\n * main returns 69.\n */\nint main() {\n    // x holds our working value. * happens before +, so this is 10 + 60 = 70.\n    int x = 10 + 20 * 3;\n\n    // If x is 50 or more (it is: 70), subtract 1 from it.\n    if (x >= 50) { x = x - 1; }\n\n    // Send the final value of x (69) back to whoever ran the program.\n    return x;\n}"}

## Example (MODE: failed)
Input: the same program but line 2 is `    int x = 10 + 20 * 3` (no semicolon); the compiler reports
`Expected ';', got 'if' at line 3, column 5`.

Output `documented_code`:
"/*\n * Computes a value, adjusts it if it is large, and returns it.\n * This version does not compile yet: see the ERROR comment below.\n */\nint main() {\n    // x holds our working value: 10 + 20 * 3 (* happens first).\n    // ERROR: this declaration has no semicolon at the end. Fix: add ';' after 3.\n    // (The compiler reports it on the next line, where it noticed the problem.)\n    int x = 10 + 20 * 3\n    if (x >= 50) { x = x - 1; }\n    return x;\n}"

## If you receive a "verification failed" message
Your previous version changed code tokens (the message lists the first difference) or did not
compile. Start again from the ORIGINAL source and only add comments.
