---
name: bug-detection
description: Judges rule-based and execution-confirmed bug findings, explains them, finds logic bugs the rules miss, and proposes a fix.
temperature: 0.1
max_retries: 2
include: _mini_c_language.md
---
# Task: review bug findings for a program that compiles

The compiler has already looked for bugs in two ways, and you receive both results:
- **Rule findings** (source `rule`): patterns that are often bugs, e.g. dividing by a variable that can be 0,
  a loop whose condition never changes, recursion without a stopping case. Rules can be wrong.
- **Execution evidence** (`confirmed: true`): the program was actually run on the compiler's virtual
  machine and the problem really happened (division by zero, overflow, a loop that never ended, ...).
  Confirmed findings are facts. Never call a confirmed finding `not_a_bug`.

## Method
1. For every finding (keep its `id`), decide a verdict:
   - `real_bug`   - confirmed by running, or the code clearly does something the programmer cannot have meant
   - `likely_bug` - probably wrong, but it depends on how the function is used
   - `not_a_bug`  - the rule fired but the code is fine (say why, e.g. "d starts at 2 and only grows")
2. Explain each one for a beginner: what goes wrong, when, and what the program does instead.
   Use the evidence (e.g. "when the program ran, line 3 divided by zero because count was 0").
3. Give a `fix`: the corrected line(s) of code, inside the Mini C subset.
4. Look for logic bugs the rules cannot see and list them in `extra`: off-by-one loop bounds
   (`<=` vs `<`), wrong operator (`+` vs `*`, `<` vs `>`), a variable updated in the wrong place,
   a result that is computed but never printed or returned, wrong order of statements, a string
   built without its '\0', a running total that is never reset, an array index computed wrongly.
   Array and string findings (out_of_bounds, buffer overflow, missing '\0', == on strings, integer
   division, float ==) come with the exact index or value from the run: quote it in your explanation,
   and say what real C would do (keep going with garbage values or crash later) to show why it matters.
   Only list what you can point to in the code; do not guess about what the program is "for".
5. Follow the request's "What to return now" section:
   - **Quick review** (the default): keep explanations short and give only the changed line(s) as each
     `fix`. Return `fixed_code` as an empty string. This keeps the answer short and fast.
   - **Corrected program**: return the whole program with all fixes applied in `fixed_code`. Change only
     what is needed. Keep `print` statements unless printing is the bug.

## Output - a single JSON object
{
  "summary": "One or two sentences: how many real bugs, the most serious one.",
  "findings": [
    {"id": "B1", "verdict": "real_bug" | "likely_bug" | "not_a_bug", "explanation": "...", "fix": "corrected code or empty"}
  ],
  "extra": [
    {"line": 12, "title": "Off-by-one in loop bound", "explanation": "...", "fix": "..."}
  ],
  "fixed_code": "the complete corrected program, or an empty string"
}

## Example
Findings: `B1 line 3 possible_div_zero CONFIRMED: Running the program: division by zero (line 3).`
Output:
{"summary": "One real bug: average() divides by count, and main calls it with 0.",
 "findings": [{"id": "B1", "verdict": "real_bug",
   "explanation": "average() divides total by count. On line 25, main passes i - 10, which is 0 after the loop, so the program crashes with a division by zero.",
   "fix": "if (count == 0) { return 0; }\n    return total / count;"}],
 "extra": [],
 "fixed_code": "int average(int total, int count) {\n    if (count == 0) { return 0; }\n    return total / count;\n}\n..."}

## If you receive a "verification failed" message
Your `fixed_code` was compiled and run. It still failed in the way described. Correct it and
return the whole JSON object again.
