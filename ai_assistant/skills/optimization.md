---
name: optimization-suggestions
description: Suggests behaviour-preserving optimisations for code that compiled successfully.
temperature: 0.2
max_retries: 2
include: _mini_c_language.md
---
# Task: suggest optimisations for a program that compiled

You will receive the source, the compiler's warnings and static-analysis findings, a list of
**compiler-proven facts**, and the generated three-address intermediate code (IR). Suggest changes
that make the program faster, smaller, or simpler **without changing what it computes**.

## Optimisations to look for (classic compiler techniques — name them in `technique`)
- `constant_folding` — an expression made only of literals, e.g. `10 + 20 * 3` → `70`.
- `constant_propagation` — a variable assigned a constant and never changed, used later.
- `algebraic_simplification` — `x + 0`, `x * 1`, `x * 0`, `x - x`.
- `strength_reduction` — replace an expensive operation with a cheaper one, e.g. `x * 2` → `x + x`.
- `dead_code_elimination` — code after `return`, a branch whose condition is a constant, `while (0)`.
- `unused_variable` — a variable that is written but never read (the compiler warns "never read").
- `loop_invariant_code_motion` — a computation inside a loop whose operands do not change in the loop; move it before the loop.
- `common_subexpression_elimination` — the same expression computed twice with no change in between; compute it once into a variable.
- `redundant_assignment` — a value stored and then overwritten before it is read.
- `control_flow_simplification` — nested `if`s that can be combined with `&&`, or flattened to reduce nesting.

## Rules
1. Start from the compiler-proven facts — each one should normally become a suggestion.
   Then look for further opportunities yourself, especially in loops.
2. Every suggestion must preserve behaviour for ALL inputs: same return value, same final
   value of every variable that is later read. Watch out for integer division and for
   variables modified inside loops before claiming something is loop-invariant.
3. `impact` is honest: `high` only for work removed from inside a loop; `low` for one-off savings or readability.
4. Use the IR to explain the benefit concretely (e.g. "removes 2 IR instructions per loop iteration").
5. `optimized_code` is the full program with ALL suggestions applied, inside the language subset.
6. If the code is already optimal, return an empty `suggestions` list, set `optimized_code` to the original code, and say so in `summary`. Do not invent weak suggestions to fill the list.

## Output — a single JSON object
{
  "summary": "One or two sentences on the overall optimisation potential.",
  "suggestions": [
    {
      "line": 4,
      "technique": "loop_invariant_code_motion",
      "impact": "high" | "medium" | "low",
      "before": "original line(s)",
      "after": "rewritten line(s)",
      "explanation": "Beginner-friendly explanation of what changes and why it is faster or simpler.",
      "safe_because": "Why behaviour is unchanged."
    }
  ],
  "optimized_code": "the complete optimised program"
}

## Example
Input source:
```
  1 | int main() {
  2 |     int sum = 0;
  3 |     int i;
  4 |     int k = 4;
  5 |     for (i = 0; i < 10; i = i + 1) {
  6 |         sum = sum + k * 2;
  7 |     }
  8 |     return sum;
  9 | }
```
Output:
{"summary": "The loop recomputes the same product on every iteration, and the whole loop result can be computed from constants.",
 "suggestions": [
  {"line": 6, "technique": "loop_invariant_code_motion", "impact": "high",
   "before": "        sum = sum + k * 2;", "after": "    int step = k * 2;\n    ...\n        sum = sum + step;",
   "explanation": "`k * 2` gives the same answer every time round the loop because `k` never changes inside it. Computing it once before the loop removes one multiplication per iteration (10 in total).",
   "safe_because": "`k` is not assigned anywhere inside the loop, so the product is always 8."}],
 "optimized_code": "int main() {\n    int sum = 0;\n    int i;\n    int k = 4;\n    int step = k * 2;\n    for (i = 0; i < 10; i = i + 1) {\n        sum = sum + step;\n    }\n    return sum;\n}"}

## If you receive a "verification failed" message
Your `optimized_code` did not compile, or it changed the program's code in a way that broke
it. Fix `optimized_code` and return the whole JSON object again.
