---
name: flow-and-complexity
description: Explains a program's control flow, call graph and loops, estimates its time complexity, and gives algorithmic hints with an optional verified faster version.
temperature: 0.2
max_retries: 2
include: _mini_c_language.md
---
# Task: explain how the program flows and how fast it is

You receive the source, the intermediate code, and **program-structure facts the compiler
computed**: every function with who calls it and whom it calls (call graph, recursion), every
loop found in the control-flow graph (CFG) with its nesting depth, the variable that controls it,
what bounds it, and how that variable grows, plus a rough Big-O estimate per function.
These facts are correct; build on them and do not contradict them.

Key terms to explain simply when you use them:
- control-flow graph: the program cut into straight-line pieces (basic blocks) joined by arrows for every possible jump
- back edge: an arrow that jumps backwards, which is what makes a loop
- O(n), O(n^2), O(log n): how the amount of work grows when the input size n grows

## Method
1. `overview`: what the program does from start to finish, following the order main runs things.
2. `functions`: for each function, its purpose in one sentence and how it fits in the call graph.
   Mention recursion and functions that are never called.
3. `loops`: for each loop (use the compiler's line numbers), how many times it runs (exactly if the
   facts say so, otherwise in terms of the bound, e.g. "n times", "about log2(n) times"), and what it does.
4. `complexity`: the overall Big-O of the program with a short reason that points at the loops/recursion.
   If all bounds are constants, say the program does a fixed amount of work and give the total count.
5. `hints`: algorithmic improvements only where they really help, e.g.
   - nested loops that check every pair when a formula or a single loop is enough (sum 1..n = n*(n+1)/2)
   - a loop that recomputes the same value each time
   - testing divisors up to n instead of up to the square root (`d * d <= n`)
   - naive recursion that recomputes the same calls (fib) -> a loop
   - exponentiation by repeated multiplication -> repeated squaring
   For each: the line, the issue, the suggestion, and the complexity before and after.
   Return an empty list if the algorithm is already reasonable. Do not suggest micro-optimisations here.
6. `improved_code`: if you gave at least one hint, the whole program with the hints applied. It must
   print exactly the same output and return the same value (this is checked by running it).
   Otherwise an empty string.

## Output - a single JSON object
{
  "overview": "...",
  "functions": [{"name": "main", "purpose": "...", "calls": ["fib"], "called_by": []}],
  "loops": [{"line": 5, "runs": "n times", "explanation": "..."}],
  "complexity": {"big_o": "O(n^2)", "reasoning": "..."},
  "hints": [{"line": 6, "issue": "...", "suggestion": "...", "before": "O(n^2)", "after": "O(n)"}],
  "improved_code": ""
}

## If you receive a "verification failed" message
Your `improved_code` either did not compile or did not produce the same output/return value
when it was run. Fix it (or return an empty string) and send the whole JSON object again.
