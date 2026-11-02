# AI-Assisted Mini C Compiler

An educational compiler for a small subset of C, with a browser IDE (MiniC Studio) and a local
LLM assistant (Ollama). The design rule throughout: **the compiler supplies the facts, the AI
explains and proposes, and the compiler verifies every proposal before a student can apply it.**

Python 3.9+, no pip packages. Optional: Ollama (AI features) and gcc/clang/tcc (native executables).

---

## Quick start

**Demoing?** Start with `DEMO_GUIDE.md`: the numbered programs `workspace/1_compile_*`, `2_warn_*`, `3_bugs_*` and
`4_crash_*` each pack several errors of one kind, with what to click and what you will see.

```bash
python ide.py                        # MiniC Studio at http://127.0.0.1:8765
python ide.py --model qwen3:8b       # choose the Ollama model

python main.py workspace/primes.c --run              # command line: compile, build, run
python main.py workspace/bugs.c --ai bugs,refactor,flow
python main.py workspace/broken.c --ai fix           # all errors, then AI fixes
python main.py workspace/input.c --run --input "4 7" # program that uses scanf
python main.py workspace/primes.c --frontend yacc    # parse with the LALR(1) yacc grammar
python main.py workspace/bug_01_off_by_one.c --ai bugs  # array/string bug samples (works without Ollama too)
python -m unittest discover -s tests -v              # all tests, no Ollama needed
python tools/parser_agreement.py                     # hand-written vs yacc vs flex/bison
python tools/benchmark.py --models qwen2.5-coder:7b,qwen3:8b   # AI evaluation
python tools/difftest.py                             # compare against plain gcc
python tools/fuzz.py                                 # crash-test + random programs vs gcc
python tools/referee_demo.py                         # watch the compiler reject wrong AI answers
python tools/speedtest.py --model qwen3:8b           # measure the AI speed-ups on your computer
```

**AI setup:** install Ollama, then `ollama pull qwen2.5-coder:7b` (or use a model you have).
**Native .exe on Windows:** install MinGW-w64 (MSYS2: `pacman -S mingw-w64-ucrt-x86_64-gcc`) and add
`C:\msys64\ucrt64\bin` to PATH. Without a C compiler, Run builds a portable MiniC VM program instead.

---

## Feature checklist

### A. Essential compiler functions

| # | Function | Status | Where |
|---|---|---|---|
| E1 | Lexical analysis: tokens with line and column; **all** lexical errors reported | Done | `compiler/lexer.py` |
| E2 | Parsing and AST, every node tied to its start **and end** position. Two parsers: hand-written recursive descent and an LALR(1) **yacc** grammar (PLY, plus the same grammar for real flex/bison) | Done | `compiler/parser.py`, `compiler/yacc_frontend.py`, `grammar/` |
| E3 | Semantic analysis and symbol table: scopes, **types** (char, short, int, long, float, double), **arrays** (1-D and 2-D, array parameters), **strings** and library functions, printf/scanf placeholder checks, function signatures, call argument counts and types, return checks | Done | `compiler/semantic.py`, `compiler/typesys.py` |
| E4 | Error reporting: type, line, column, offending token, expected token, plain-English explanation, hint | Done | `compiler/diagnostics.py` |
| E5 | Error recovery: phrase-level (inserts a missing `;` or `)` and keeps the tree, so semantic errors are found too) and panic mode (skips to `;`, `}`, a whole `{...}` block or a statement keyword). Every error says how the parser recovered | Done | `compiler/parser.py` |
| E6 | Control-flow graph per function, call graph with recursion (Tarjan SCC) and unreachable functions | Done | `compiler/cfg.py`, `compiler/call_graph.py` |
| E7 | AST metrics and rule checks: function length, nesting depth, magic numbers, too many parameters, unreachable code (AST and CFG), constant conditions, unused functions, uninitialised use | Done | `compiler/static_analysis.py`, `compiler/loops.py` |
| | Intermediate code (three-address IR) with source-line map; short-circuit `&&`/`||`; typed temporaries and explicit conversions (`t5 = (double) t3`); array element access (`t6 = a[t1]`, `a[t1] = t6`); `printf` lowered to `WRITE_STR`/`WRITE_INT`/`WRITE_FMT` | Done | `compiler/code_gen.py` |
| | Back-end: IR to C (one line per IR instruction) to a native executable; or a portable VM build. Arrays are bounds-checked and string functions check for overflow and a missing `'\0'`, like the VM | Done | `compiler/backend.py` |
| | Virtual machine: calls, recursion, every data type (8/16/32/64-bit wrap-around, 32-bit float), arrays shared by reference, **checked** array accesses and string functions, overflow detection, step trace for the debugger (arrays shown as `{1, 2, ?}` and strings as `"text"`) | Done | `ai_assistant/ir_interpreter.py` |

### B. AI features

| Category | Feature | How it works | Verified by |
|---|---|---|---|
| Documentation | Function-level summaries and docstrings | `beginner_docs` skill, grounded in the call graph | Code tokens unchanged |
| | Line or block explanation | Same skill; also works on code that does not compile (`// ERROR:` comments) and on AI-corrected code (`// FIXED:`) | Code tokens unchanged |
| | Call-graph and control-flow explanation | Compiler computes CFG loops (dominators, back edges, natural loops), call graph, loop bounds; `flow_explanation` skill explains | Facts come from the compiler |
| Error assistance | Plain-English error explanations | Rule-based for every diagnostic, AI for the full picture | n/a |
| | Fix suggestions (code snippet and full program) | `fix_suggestions` skill sees **all** errors at once | Compiles; keeps your functions and most of your code |
| | Error recovery hints | Each syntax error carries the parser's recovery ("Assumed a missing ';' after '0' on line 2", "Skipped `) { a = 2; }` and resumed at line 4") plus a beginner hint (e.g. which `{` is unclosed, judged from indentation) | n/a |
| Static analysis | Code smell detection | Rules (E7); AI explains in documentation and flow views | n/a |
| | Bug detection (rules + LLM) | 28 rules find suspects (9 general + 19 for arrays, strings and number types), the program is **run on the VM** to confirm them (division by zero, overflow, infinite loop, missing base case, index out of bounds, buffer overflow, missing `'\0'`, `==` on strings, integer division, float `==` ...), every finding has a "how to fix" hint, the `bug_detection` skill judges each finding and finds logic bugs | Proposed fix compiles and runs without the error |
| Optimisation | Source-level optimisation | Compiler-proven facts (constant folding, identities...) + IR; `optimization` skill | Compiles; same output and return value on the VM |
| | Algorithmic hints | Loop nesting from the CFG + loop bounds and growth (linear / logarithmic / data-dependent) give a Big-O estimate; `flow_explanation` suggests better algorithms | Faster version must print and return the same, VM step count reported |
| Transformation | Refactoring (while to for, simplify conditionals) | **Deterministic AST pattern matching** (7 patterns), text-span replacement that keeps comments; AI only explains | Compiles; same output and return value |
| | Code completion | Editor completion for keywords, snippets, your variables and your functions with parameter placeholders (not LLM-based: a local 8B model is too slow and unreliable for as-you-type completion) | n/a |

**Guide.** The *Guide* tab (or the ? button on the left) explains every feature in three steps,
including what the compiler checks for each AI feature.

**Speed.** Local models are slow on laptops, so the assistant: keeps the model loaded (`keep_alive` 30 min)
and pre-loads it when the IDE opens; switches off the hidden "thinking" step of qwen3 / deepseek-r1 (set
`MINIC_THINK=1` to keep it); sizes the context window to each prompt so the model fits on the GPU more
often; leaves the IR out of prompts that do not need it; caps runaway answers; and reuses verified answers
to the same question instantly (*Ask again* skips the saved answer). *Find bugs* shows the compiler's findings at once and asks
the model only for short verdicts and explanations; the whole corrected program is a separate, optional
*Propose a full fix* request (in the CLI you are asked), because writing and re-checking a full program
was most of the wait. Every AI card and the CLI show live
progress: thinking, writing (characters so far), checking with the compiler, rejected and asking again.

**Test the referee (IDE, no Ollama needed).** The *Test the referee* button builds typical wrong AI
answers from whatever program is open (a changed constant, an off-by-one loop bound, a deleted statement,
documentation that sneaks in a code change, an empty or gutted fix) plus answers that should pass, and runs
each through the real checks. *Try to fool it yourself* opens an editable diff: write any "AI answer",
choose the feature, and submit it to the referee. Code: `ai_assistant/referee.py`.

**The compiler as referee.** Every rejected AI answer is recorded: the IDE card and the CLI report show
a referee log (attempt, why it was rejected, what was sent back to the model). `tools/referee_demo.py`
makes this repeatable for a presentation: a scripted model gives typical wrong answers (an empty fix, a
rewrite, documentation that changes a value, an optimisation that changes the output, a bug fix that still
crashes) and the real verification code rejects each one.

Every AI result that changes code is shown as a side-by-side diff and needs confirmation; the first
change to a file keeps `<name>.bak`. Failed verifications are sent back to the model with the exact
reason (up to `max_retries` in the skill file) and anything still failing is marked *not verified*.

---

## MiniC Studio (IDE)

| Area | What it does |
|---|---|
| Editor | Monaco (VS Code's editor), Mini C highlighting, live squiggles with explanations, autocomplete |
| Pipeline rail | Lex, Parse, Semantic, IR, Build, Run light up per stage; click one to see what it produced |
| Run (F5) | Builds a real executable (gcc/clang/tcc) and runs it; output and exit code in the Terminal. Programs that use `scanf` take their input from the **Input** box above the Terminal (Enter runs) |
| Parser chip | Choose the hand-written parser or the LALR(1) yacc parser for every compile |
| Parse trace | Every shift, reduce and goto of the LALR parser with its stack and lookahead, whether both parsers built the same tree, the grammar, and a "check with flex & bison" button |
| Debug (F9) | Step over (F10), into (F11), out (Shift+F11), back (Shift+F10), continue to breakpoints (F8); call stack, locals, globals, output |
| Views | Problems, Tokens, AST, IR (hover to see the source line), Control flow (loops and back edges), Call graph, Target code (generated C and assembly), Variables |
| AI assistant | Fix errors, Optimise, Document, Find bugs, Refactor, Explain flow; diff review before applying |

Find bugs, Refactor and Explain flow also work with Ollama offline (compiler analysis only).

---

## The language

**Types:** `char` (8-bit), `short` (16-bit), `int` (32-bit), `long` (64-bit), `float`, `double`; functions return
one of these or `void`. Literals `42`, `3.14`, `2.5f`, `1e-3`, `'a'`, `'\n'`, `"text"`. Numbers convert like C
(`int i = 9.99;` stores 9, `7 / 2` is 3), casts `(double) x`, `sizeof(int)`, `sizeof(a)`. Integer types wrap around.

**Arrays:** `int marks[5];` `int a[] = {1, 2, 3};` `int m[3][4];` `int m[2][2] = {{1, 2}, {3, 4}};`, elements `a[i]`,
`m[i][j]`; array parameters `int sum(int a[], int n)` / `void f(int m[][4])` work on the caller's array.

**Strings:** char arrays ending with `'\0'`: `char name[20] = "Ann";` `char names[3][10] = {"a", "b", "c"};`.
Built in (no `#include` needed): `strlen strcpy strncpy strcat strncat strcmp strncmp puts putchar`,
`toupper tolower isupper islower isalpha isdigit isalnum isspace`, `abs sqrt pow fabs floor ceil`.

**Statements:** globals (start at 0); functions with calls and recursion; `if/else`, `while`, `for`,
`do ... while`, `switch` / `case` / `default` (with fall-through like C), `break`, `continue`; `return`;
`int a = 1, b, c = 3;`, `const int N = 5;`; `x++ x-- ++x --x` and `+= -= *= /= %=` as statements;
`+ - * / %`, comparisons, `&& ||` (short-circuit, like C), `!`, unary minus; `//` and `/* */` comments.

**Preprocessor:** `#define NAME value` (values may use earlier names), `#undef NAME`; `#include <...>` is
accepted and ignored. Line numbers in error messages stay those of your file.

**Output:** `printf` with `%d %i %ld %u %x %c %s %f %e %g`, widths and flags (`%5d`, `%-10s`, `%.2f`), the escapes
`\n \t \\ \" \0`; each placeholder must match its value's type (a compile error otherwise). Short form `print(x);`.
`#include <...>` is accepted and ignored, so programs written with includes are valid standard C.

**Input:** `scanf("%d %lf %s", &n, &d, name);` with `%d %ld %f %lf %c %s` (`" %c"` skips spaces, `%s` needs no `&`,
elements need `&a[i]`). In the IDE the input comes from the Input box; on the command line from `--input "4 7"`
or, if not given, typed interactively. Automatic checks (AI verification, bug confirmation, difftest) use a fixed
sample input so they are repeatable. Missing input is a run-time error that says which `scanf` ran out.

**Checked at run time:** every array access and string function. An index outside `0..N-1`, a string without
`'\0'`, `strcpy`/`strcat`/`scanf("%s")` writing past the end of an array, or reading an array element that was
never set stops the program with a clear message (VM, native build and debugger), where real C would silently
continue with garbage. `tools/difftest.py` shows these programs as `safe`. Also stopped with a message: division
or `%` by zero, reading a variable before it was given a value, deep recursion (stack overflow), missing input,
and a program that never finishes (step limit).

**Logic warnings** (the program compiles, but probably does not do what was meant): a stray `;` after
`if/while/for`, a `case` that falls through without `break`, `x == x`, a condition that can never be true
(`m < 0 && m > 100`), a `for` counter that is also changed in the body, a constant calculation that overflows `int`,
`while (1)` with no `break` or `return`, and code after `return`/`break`/`continue`.

**Robustness:** `python tools/fuzz.py` throws thousands of broken programs at the compiler (mutated examples,
random tokens, Unicode and NUL bytes, unterminated comments and strings, 1000-deep nesting, huge numbers and arrays)
and checks that nothing crashes and every error has a line number. It then generates random valid programs using
every feature and checks that the VM, our native build and gcc print exactly the same.

Not supported: pointers, `struct`, `enum`, `unsigned`, `?:`, bitwise operators, `goto`, macros with parameters,
`#if`/`#ifdef`, variable-length arrays, and `++`/`+=` inside a bigger expression (`a[i++] = 0;`).

## Find bugs: array, string and type mistakes

`workspace/bug_01_*.c` ... `bug_24_*.c` each contain one classic logical error. They all compile; open one and
click **Find bugs** (or `python main.py workspace/bug_01_off_by_one.c --ai bugs`). See `BUG_SAMPLES.md` for
what each one does wrong, what the compiler reports and how to fix it.

| Sample | Mistake | Rule | Confirmed by running? |
|---|---|---|---|
| bug_01_off_by_one | `for (i = 0; i <= 5; ...)` over `marks[5]` | off_by_one | yes, index 5 out of bounds |
| bug_02_last_element | `temps[5]` as the last of 5 | array_out_of_bounds | yes |
| bug_03_sizeof_loop | loop to `sizeof(prices)` (bytes, not elements) | sizeof_bytes | yes |
| bug_04_sizeof_parameter | `sizeof(values)` inside a function | sizeof_pointer | rule (wrong result, no crash) |
| bug_05_wrong_length | `printAll(scores, 10)` for 5 scores | array_size_argument | yes, inside printAll |
| bug_06_uninitialised_count | `count[d] = count[d] + 1` without zeroing | array_read_before_write | yes |
| bug_07_no_room_for_null | `char word[5] = "hello";` | no_room_for_terminator | yes, strlen finds no `'\0'` |
| bug_08_strcpy_overflow | `strcpy` 13 letters into `name[8]` | strcpy_overflow | yes, buffer overflow |
| bug_09_strcat_overflow | `strcat` past the end | strcpy_overflow | yes |
| bug_10_missing_terminator | string built letter by letter, no `'\0'` | missing_terminator | yes |
| bug_11_string_equals | `typed == saved` | string_compare_eq | yes: both said "secret", `==` gave 0 |
| bug_12_strcmp_condition | `if (strcmp(a, b))` for "same" | strcmp_as_bool | rule |
| bug_13_integer_division | `double avg = (a + b + c) / 3;` | integer_division | yes: 245 / 3 gave 81 |
| bug_14_float_equality | `0.1f == 0.1`, `0.1 + 0.2 == 0.3` | float_equality | yes |
| bug_15_lost_decimals | `int celsius = ... 5 / 9;` from a double | lost_decimals | yes: 23.89 stored as 23 |
| bug_16_char_overflow | `char score = 100 + 50` | char_overflow / overflow | yes: 150 became -106 |
| bug_17_scanf_char | `scanf("%c")` after `scanf("%d")` | scanf_char_newline | yes: %c read a space |
| bug_18_strncpy | `strncpy(d, s, 5)` into `d[5]` | strncpy_no_terminator | yes |
| bug_19_matrix_bounds | `c <= 3` in a 3x3 table | off_by_one | yes, `grid[0][3]` |
| bug_20_stray_semicolon | `while (n > 0);` | empty_body | yes, never finishes |
| bug_21_switch_fallthrough | `case 2:` without `break` | switch_fallthrough | yes, ran into case 3 |
| bug_22_counter_changed | `i += 1` inside `for (...; i++)` | counter_changed | rule (skips every other score) |
| bug_23_impossible_condition | `mark < 0 && mark > 100` | impossible_condition | rule |
| bug_24_constant_overflow | `days * 24 * 60 * 60 * 1000` | overflow | yes: became -1702967296 |

Working examples of every feature: `workspace/arrays.c`, `workspace/strings.c`, `workspace/datatypes.c`, `workspace/c_features.c`.

## Lex and yacc

The language has a second, formal front end:

| File | What it is |
|---|---|
| `compiler/yacc_frontend.py` | Lex rules and yacc grammar written with PLY (Python Lex-Yacc, bundled in `compiler/vendor/ply`). LALR(1): 97 productions, 191 states, **0 conflicts**. Dangling `else` solved with `%prec`. Error recovery with `stmt : error ';'`. Builds exactly the same AST as the hand-written parser, so semantic analysis, IR, AI and IDE all work with either |
| `grammar/minic.l`, `grammar/minic.y` | The same grammar for real **flex and bison** (C) |
| `tools/build_yacc.py` | Builds the flex/bison checker (`python tools/build_yacc.py`) and runs it on a file |
| `tools/parser_agreement.py` | Runs 266 programs (162 valid, 104 deliberately broken) through all three parsers and checks they accept/reject the same programs and build the same trees |
| `tools/fuzz.py` | Robustness fuzzer: thousands of broken/extreme inputs must never crash any stage, and random valid programs must print the same on the VM, our native build and gcc |

Use it with `--frontend yacc` on the command line or the parser chip in the IDE.

flex and bison are optional (the PLY parser needs nothing). Linux: `sudo apt install flex bison`.
macOS: `brew install flex bison`. Windows: `choco install winflexbison3` or MSYS2
`pacman -S flex bison` (`win_flex`/`win_bison` are detected automatically).

---

## Architecture

```
source
  -> lexer (all lexical errors) -> parser (recovery: insert / panic)  or  PLY yacc (LALR(1)) -> AST
  -> semantic analysis (scopes, calls, returns)
  -> IR + line map -> CFG per function -> loops (dominators, back edges) + call graph
  -> static analysis (E7)            -> back-end: C -> gcc -> executable | VM program
  compiler.pipeline.analyze() runs all of this once for the CLI, the IDE and the AI.

AI feature = skill file (system prompt) + grounded context (source, all diagnostics, IR,
compiler-proven facts, call graph, loops, complexity) + JSON schema
  -> Ollama -> verify with the compiler / VM -> retry with the failure -> diff -> user confirms
```

| Folder / file | Purpose |
|---|---|
| `compiler/` | lexer, parser, yacc_frontend (PLY), typesys (types, library functions, printf/scanf formats), semantic, code_gen, cfg, loops, call_graph, static_analysis, pipeline, backend, printer, refactor, diagnostics |
| `ai_assistant/llm_client.py` | Ollama REST client (JSON-schema output) |
| `ai_assistant/skills/*.md` | One skill per feature + `_mini_c_language.md` shared rules |
| `ai_assistant/ai_features.py` | Features, verification loops, CLI reports |
| `ai_assistant/bug_detector.py` | Bug rules + confirmation by execution |
| `ai_assistant/ir_interpreter.py` | VM |
| `ai_assistant/file_writer.py` | Diff, confirmation, backup, safe write-back (CLI) |
| `ide/` | `server.py` (stdlib HTTP + JSON API), `static/` (UI, bundled Monaco and fonts) |
| `tools/benchmark.py`, `bench/programs/` | AI evaluation |
| `tools/difftest.py` | Differential testing against gcc |
| `grammar/`, `tools/build_yacc.py`, `tools/parser_agreement.py` | flex/bison grammar, its builder, three-parser agreement |
| `workspace/` | Example programs (one per feature) |
| `tests/` | Unit tests |

---

## Evaluation (for the report)

`tools/benchmark.py` breaks the reference programs on purpose (missing `;`, misspelled variable,
missing `)`, `i++`, missing `}`, wrong argument count, missing declaration) and injects runtime
bugs, then measures per model:

* **fix**: verified on the first try, after retries, and whether the fixed program behaves
  exactly like the original (the original is known, so this is ground truth)
* **optimize**: verified rate, IR size and VM step reduction
* **docs**: verified rate (no code changed)
* **bugs**: injected bug reported as real, fix runs without errors
* **caught**: first answers that were wrong and would have reached the student without the check

Results: `bench/results/*.csv` and a Markdown summary table. The benchmark already found one
verification gap during development (an empty "fix" compiles), which is now checked.

### Differential testing against gcc

`tools/difftest.py` runs every program three ways: on the MiniC VM, as our compiler's native build,
and compiled directly by gcc as ordinary C (with `print(x)` mapped to `printf`). All three must print
the same text and return the same exit code (programs with `scanf` get the same sample input). All example and benchmark programs agree; the array/string bug samples are reported as `safe` (our VM and native build stop at the mistake, plain gcc runs past it). It found
one real semantic difference during development (Mini C evaluated both sides of `&&`/`||`), which
was fixed by generating short-circuit jumps in the IR.

---

## Limitations

* Only `main` without parameters can be executed; input comes through `scanf`.
* Variables shadowed inside nested blocks of the same function share one IR name.
* Complexity estimates are heuristics from loop structure; data-dependent loops and recursion are
  labelled as such instead of guessed. The AI explains them.
* AI quality depends on the local model; verification limits the damage, not the quality.
* Array elements read before being set are caught by the VM (and the debugger); the native build starts local
  arrays at 0, so it cannot see that mistake (it does check bounds, overflows and missing `'\0'`).
* `char` is signed (8-bit) everywhere; builds use `-fsigned-char` so ARM Macs behave like x86.
* `isupper()` and friends return exactly 1 or 0 (real C only promises non-zero).
