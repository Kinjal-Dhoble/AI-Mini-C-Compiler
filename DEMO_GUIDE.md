# Demo guide: the numbered error programs

The `workspace/` folder starts with eleven numbered programs. Each file packs **several errors of one kind**, and the
number at the front tells you what kind it is. They sort to the top of the Workspace list in the order you demo them.

| Prefix | What it shows | What to click | What you will see |
|---|---|---|---|
| `1_compile_` | Code the compiler **rejects** | nothing (or Run) | Every error at once, each with a line, column and hint |
| `2_warn_` | Code that **compiles** but is wasted or wrong | Run, then look at Problems | Yellow warnings; the program still runs |
| `3_bugs_` | Mistakes that **compile and run** but give wrong answers | **Find bugs** | Each mistake found by a rule, most **confirmed by running** |
| `4_crash_` | Errors that only show up **while running** | type 1-7 in Input, then Run | The run stops at the exact line with a plain message |

The older one-mistake-per-file samples (`bug_01` ... `bug_24`) are still there for one-at-a-time demos; see
`BUG_SAMPLES.md`.

---

## How to run it (Windows PowerShell)

1. Unzip `ai_mini_c_compiler_full.zip` and go into the project folder (the one with `ide.py`):
   ```powershell
   cd C:\path\to\mini_compiler_work
   ```
2. (Optional, for the AI parts) make sure Ollama is running: `ollama list` should show `qwen3:8b`.
   **Everything in this guide works without Ollama**: compile errors, warnings, Find bugs and runtime errors all
   come from the compiler.
3. If an older copy of the IDE is still running, stop it first (otherwise the old version keeps the port):
   ```powershell
   Get-NetTCPConnection -LocalPort 8765 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
   ```
4. Start MiniC Studio:
   ```powershell
   python ide.py --model qwen3:8b
   ```
5. Open **http://127.0.0.1:8765** in your browser and press **Ctrl + F5** once, so the browser loads the new page
   instead of a cached one.
6. Open the **Workspace** panel (left). The `1_...` to `4_...` files are at the top.

Without the IDE, every demo also works from the command line:
```powershell
python main.py workspace\1_compile_types.c                         # all the errors
python main.py workspace\3_bugs_numbers.c --ai bugs                # Find bugs
python main.py workspace\4_crash_runtime_errors.c --run --input "2"  # runtime error 2
```

To check that all of the demos still behave as described here: `python -m unittest tests.test_c_features -v`.

---

## 1. Compile errors (`1_compile_*`): the compiler refuses the program

Open each file. The errors appear in the **Problems** panel straight away. The point to make is that the compiler
reports **all** of them, not just the first, and every one has a line number and a hint.

| File | Errors inside |
|---|---|
| `1_compile_lexical.c` | 5 lexical errors: `@`, `$`, `'AB'` (a char holds one character), `?:` (not supported), a string that is never closed |
| `1_compile_syntax.c` | 4 syntax errors: a missing `;`, two missing `)`, an operator with nothing in front of it (`+ * 2`). The parser says how it recovered from each one and keeps going |
| `1_compile_semantic.c` | 8 meaning errors: a void function returning a value, `count` declared twice, an undeclared variable, the wrong number of arguments, a void result used as a value, changing a `const`, a function that does not exist, `break` outside a loop |
| `1_compile_types.c` | 10 type errors: too many initial values, a string too long for its array, a variable array size, `copy = marks` (a whole array), `%` on a double, `%d` given a double, `%s` given an int, indexing a non-array, a repeated `case`, a `case` value that is not a constant |

**Extra point:** switch the **Parser** chip (top bar) to **Lex & Yacc** and the same errors appear, because the
LALR(1) parser checks the same grammar. (Its recovery is different, so after a syntax error it may report one
extra follow-on error.)

## 2. Warnings (`2_warn_*`): it compiles, but the compiler is suspicious

Press **Run**: both programs run. Then look at the yellow warnings in **Problems**.

**`2_warn_unused_and_dead_code.c`**: wasted or risky code.
1. `helper()` is never called
2. `unused` is never read
3. `result` is read before it has a value
4. `if (0)` can never run (dead branch)
5. a `printf` after `return` can never run

**`2_warn_logic_mistakes.c`**: 7 logic mistakes. gcc accepts all of them; most give no warning even with `-Wall`.
1. `while (1)` with no `break` or `return` (an endless loop)
2. `mark < 0 && mark > 100` can never be true
3. `mark == mark` is always true
4. the loop counter `i` is changed inside the `for` body as well
5. `if (total > 3);`: the stray `;` means the `if` controls nothing, so `Big total` is printed regardless
6. `case 12` has no `break`, so it carries on into `case 10` (it prints `Twelve` and then `Ten`)
7. `30 * 24 * 60 * 60 * 1000` overflows `int`, so the program prints `-1702967296`

All of these are found **without running** the program: this is static analysis.
(You will also see "magic number" notes, which are style hints, not errors.)

## 3. Find bugs (`3_bugs_*`): the program runs but is wrong

Open the file and click **Find bugs** in the AI panel. The compiler's findings appear within about a second. Each
finding says either **rule** (spotted in the code) or **confirmed by running** (the VM ran the program and caught
the mistake as it happened, with evidence). If Ollama is running, the AI then adds a verdict and an explanation for
each finding.

The key comparison is **what plain gcc does with the same file**: it gives no error at all, just wrong output.

| File | Mistakes | Confirmed by running | Plain gcc prints |
|---|---|---|---|
| `3_bugs_numbers.c` | integer division, lost decimals, char overflow, int overflow, `==` on decimals | **all 5** | `Average: 81.00`, `Celsius: 23`, `Level: -106`, `Milliseconds: -1702967296`, `Not equal` |
| `3_bugs_strings.c` | `==` on strings, `if (strcmp(...))`, `strncpy` with no `'\0'`, `strcpy` overflow | 3 of 4 (the `strcmp` one is a rule, since nothing crashes) | `Wrong password`, then `Christopher Nolan`, written past the end of an 8-place array without any error |
| `3_bugs_arrays.c` | `sizeof` on an array parameter, `i <= 5` past the end, counting in an array that was never zeroed | 2 of 3 (the run stops at the out-of-bounds read on line 22) | `Average age: 18` (wrong: it should be 20) and `Total: 389 1`, which looks right only by luck |
| `3_bugs_control_flow.c` | missing `break`, impossible condition, loop counter changed, stray `;` after `while` | 3 of 4 (the run catches the endless loop) | `Tuesday Wednesday 4 15 23`, then it hangs forever |

The mistakes that do not crash come **first** in each file and the one that stops the program comes **last**, so a
single run can confirm several of them.

## 4. Runtime errors (`4_crash_runtime_errors.c`): only visible while running

Type **one number** in the **Input** box, press **Run**, then try the next number.

| Input | Error | Mini C says | Plain gcc does |
|---|---|---|---|
| 1 | array index out of bounds | `marks[3] uses index 3, but marks has 3 elements` (line 21) | prints garbage (`1042854928`) and says it finished normally |
| 2 | division by zero | `division by zero` (line 25) | crashes with exit code 136 and no message |
| 3 | variable used before a value | `variable 'total' is read before it has a value` (line 29) | prints `5` as if nothing were wrong |
| 4 | endless recursion | `stack overflow: more than 1000 nested calls to depth() (is the base case missing?)` (line 12) | segmentation fault (exit code 139) |
| 5 | string copy overflow | `strcpy() needs 10 places ... but city only has 6` (line 37) | `*** stack smashing detected ***` |
| 6 | scanf runs out of input | `the program asked for a number (scanf) but there is no more input` (line 43) | prints `0` and carries on |
| 7 | loop that never ends | stops after a step limit and shows the line | prints `k = 0` forever |
| other | nothing wrong | `Finished normally (choice N)` | the same |

**The point to make:** C itself does not check any of this. Mini C stops at the exact line with a sentence a beginner
can understand. The same checks run in the VM, in the native `.exe` that Run builds, and in the debugger.

---

## A 5-minute demo order

1. `1_compile_types.c`: "every error at once, with hints". Switch the parser to Lex & Yacc and show the same errors.
2. `2_warn_logic_mistakes.c`: "valid C, but the compiler sees the logic mistakes". Run it to show the stray `;`
   and the fall-through in the output.
3. `3_bugs_numbers.c`, then **Find bugs**: "all 5 confirmed by running, while plain C prints plausible wrong numbers".
4. `4_crash_runtime_errors.c` with inputs 1, 3 and 4: "real C prints garbage or crashes silently; we name the line".
5. Optional: `referee_demo.c` and **Test the referee**, to show that the compiler checks the AI's answers too.
