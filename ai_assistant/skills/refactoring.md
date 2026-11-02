---
name: refactoring-explanations
description: Explains compiler-made refactorings (while to for, simplified conditionals) to a beginner. The compiler does the rewriting; the model only explains.
temperature: 0.2
max_retries: 0
include: _mini_c_language.md
---
# Task: explain refactorings the compiler has already made

The compiler found places where the code can be written more clearly and rewrote them itself,
by matching patterns in the syntax tree. Each rewrite was then checked: the new program compiles
and, when run, prints the same output and returns the same value. You do NOT change any code.

For each refactoring (keep its `id`), write a short explanation for a beginner:
1. What the old code does, in one sentence.
2. Why the new form is clearer or shorter (name the idea: "a for loop keeps the counter in one place",
   "a comparison already gives 1 or 0", "one condition with && instead of two nested ifs").
3. One sentence on why the behaviour is the same.
Keep each explanation under 70 words. Do not repeat the code back in full.

Then write a one-sentence `summary` of all the refactorings together, and one general `tip`
the student can apply to future code.

## Output - a single JSON object
{
  "summary": "...",
  "explanations": [{"id": "R1", "explanation": "..."}],
  "tip": "..."
}
