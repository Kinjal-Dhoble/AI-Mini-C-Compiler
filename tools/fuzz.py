"""Throw thousands of programs at the compiler to check that it is solid.

    python tools/fuzz.py                  # both modes, default sizes
    python tools/fuzz.py --crash 3000     # only the crash test, 3000 inputs
    python tools/fuzz.py --diff 200       # only the comparison with gcc, 200 programs
    python tools/fuzz.py --seed 7         # repeat a run exactly

crash   Broken and strange input: every example program mutated (tokens deleted, duplicated, swapped or
        replaced; the file cut short; random characters, Unicode and NUL bytes inserted) plus hand-made
        extremes (empty file, unterminated comment / string / char, huge numbers, 1000-deep nesting,
        5000 statements, #define cycles). Both parsers, semantic analysis, IR, the VM and the C back end
        must never crash: a program may be rejected, but always with errors that have a line and column.

diff    Random VALID programs using everything the language has (ints, arrays, ++ / += ..., if, while,
        for, do-while, switch with and without break, break / continue, functions, globals, printf).
        Each runs on the MiniC VM, as our native build, and compiled directly by gcc: all three must
        print exactly the same and return the same exit code. Needs gcc.
"""
import argparse
import random
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from compiler.pipeline import analyze                   # noqa: E402
from compiler.lexer import TOKEN_RE                     # noqa: E402
from compiler import backend                            # noqa: E402
from ai_assistant.ir_interpreter import run_program     # noqa: E402

VOCAB = ["int", "char", "double", "void", "if", "else", "while", "for", "do", "switch", "case", "default", "break",
         "continue", "return", "const", "sizeof", "printf", "scanf", "print", "x", "a", "main", "(", ")", "{", "}",
         "[", "]", ";", ",", ":", "=", "==", "+=", "++", "--", "-", "+", "*", "/", "%", "!", "&&", "||", "<", ">=",
         "0", "1", "42", "2147483647", "3.5", "'a'", '"s"', '"%d\\n"', "&", "#define", "#include <stdio.h>"]
GARBAGE = ["@", "$", "`", "~", "?", "\\", "é", "∑", "😀", "\x00", "\t", "\r", "﻿", "/*", "*/", "//", "'", '"', "#", "\n"]


# ------------------------------------------------------------------ crash test
def tokens(src):
    return [m.group() for m in TOKEN_RE.finditer(src)]


def mutate(src, rng):
    toks = tokens(src)
    if not toks:
        return src
    kind = rng.randrange(8)
    i = rng.randrange(len(toks))
    if kind == 0:
        del toks[i]
    elif kind == 1:
        toks.insert(i, toks[i])
    elif kind == 2:
        j = rng.randrange(len(toks)); toks[i], toks[j] = toks[j], toks[i]
    elif kind == 3:
        toks[i] = rng.choice(VOCAB)
    elif kind == 4:
        toks.insert(i, " " + rng.choice(VOCAB) + " ")
    elif kind == 5:
        return src[:rng.randrange(len(src) + 1)]                    # file cut short
    elif kind == 6:
        k = rng.randrange(len(src) + 1)
        return src[:k] + rng.choice(GARBAGE) * rng.randint(1, 3) + src[k:]
    else:
        for _ in range(rng.randint(2, 6)):                           # several edits at once
            toks[rng.randrange(len(toks))] = rng.choice(VOCAB + [""])
    return "".join(toks)


def extremes():
    yield ""
    yield "\x00\x00"
    yield "// only a comment"
    yield "int main() { /* never closed"
    yield 'int main() { printf("never closed); }'
    yield "int main() { char c = 'ab'; char d = '; }"
    yield "int main() { int x = 99999999999999999999999; double d = 1e999; return 0; }"
    yield "int main() { return " + "(" * 1000 + "1" + ")" * 1000 + "; }"
    yield "int main() { " + "{" * 1000 + "}" * 1000 + " return 0; }"
    yield "int main() { int x = 0; " + "x = -" * 500 + "1; return 0; }"
    yield "int main() { int x = 0;\n" + "x += 1;\n" * 5000 + "return x; }"
    yield "int main() { int " + "v" * 5000 + " = 1; return 0; }"
    yield "#define A B\n#define B A\nint main() { return A; }"
    yield "#define N\nint a[N];\nint main() { return 0; }"
    yield "#define\n#undef\n#if 1\nint main() { return 0; }"
    yield "int main() { switch (1) { } switch (2) { default: } return 0; }"
    yield "int main() { do ; while (0); for (;;) break; return 0; }"
    yield "int f(" + ", ".join(f"int p{i}" for i in range(300)) + ") { return 0; } int main() { return 0; }"
    yield "int main() { int a[2147483647]; return 0; }"
    yield "int main() { int a[0]; int b[-3]; return 0; }"
    yield "int main() { break; continue; case 1: default: return 0; }"
    yield "﻿int main() { return 0; }"
    yield "int main() { int x; x = x / 0; x %= 0; return 1 / 0; }"
    yield "void main() { return; }"
    yield "int main(int argc) { return argc; }"
    yield "int f() { return f(); } int main() { return f(); }"
    yield "int main() { int i; for (i = 0; ; i++) {} return 0; }"


def check_result(r, src, where):
    for e in r["errors"] + r["warnings"] + r.get("static", []):
        line, col = e.get("line"), e.get("column")
        if not isinstance(line, int) or line < 1 or not isinstance(col, int) or col < 0:
            raise AssertionError(f"{where}: diagnostic without a proper position: {e}")
        if not e.get("message"):
            raise AssertionError(f"{where}: diagnostic without a message: {e}")


def one_crash_case(src, cc):
    for frontend in ("hand", "yacc"):
        r = analyze(src, frontend=frontend)
        check_result(r, src, frontend)
        if r["success"]:
            run = run_program(r["ir"], r["line_map"], max_steps=20_000, stdin="1 2 3 4 5 6 7 8")
            if run["error_kind"] is None and run["error"]:
                raise AssertionError(f"VM error without a kind: {run['error']}")
            backend.ir_to_c(r["ir"], r["line_map"], "fuzz.c")
    return r


def crash_test(n, rng, cc, log):
    programs = [p.read_text(encoding="utf-8") for p in sorted((ROOT / "workspace").glob("*.c"))]
    cases = list(extremes())
    while len(cases) < n:
        src = rng.choice(programs)
        for _ in range(rng.choice((1, 1, 1, 2, 3))):
            src = mutate(src, rng)
        cases.append(src)
    for _ in range(max(1, n // 20)):                             # pure token soup
        cases.append(" ".join(rng.choice(VOCAB) for _ in range(rng.randint(1, 60))))
    failures, accepted, compiled = [], 0, 0
    t0 = time.time()
    for k, src in enumerate(cases):
        try:
            r = one_crash_case(src, cc)
            if r["success"]:
                accepted += 1
                if cc and accepted % 25 == 0:                    # the generated C must compile
                    with tempfile.TemporaryDirectory() as d:
                        b = backend.build(r["ir"], r["line_map"], d, "fuzz")
                        if not b["ok"]:
                            raise AssertionError("generated C does not compile:\n" + b["log"][:500])
                    compiled += 1
        except Exception as exc:                                  # a crash: record it with the input
            failures.append((src, f"{type(exc).__name__}: {exc}", traceback.format_exc()))
        if log and (k + 1) % 500 == 0:
            print(f"  {k + 1}/{len(cases)} inputs, {len(failures)} crashes, {time.time() - t0:.0f} s", flush=True)
    return {"inputs": len(cases), "accepted": accepted, "c_compiled": compiled, "failures": failures}


# ------------------------------------------------------------------ comparison with gcc
class Gen:
    """Random valid Mini C programs whose behaviour is fully defined in C (no division by 0, indexes in range)."""
    def __init__(self, rng):
        self.rng = rng
        self.n = 0

    def fresh(self, p="v"):
        self.n += 1
        return f"{p}{self.n}"

    def expr(self, scalars, arrays, depth=0):
        r = self.rng
        if depth > 2 or r.random() < 0.3:
            choice = r.random()
            if choice < 0.4 and scalars:
                return r.choice(scalars)
            if choice < 0.55 and arrays:
                name, size = r.choice(arrays)
                return f"{name}[{self.index(scalars, size)}]"
            return str(r.randint(-20, 99))
        op = r.choice(["+", "-", "*", "/", "%", "<", ">=", "==", "!=", "&&", "||", "!", "neg"])
        a = self.expr(scalars, arrays, depth + 1)
        if op == "!":
            return f"!({a})"
        if op == "neg":
            return f"-({a})"
        b = self.expr(scalars, arrays, depth + 1)
        if op in "/%":
            return f"({a}) {op} (({b}) % 7 + 8)"                  # divisor is 2 .. 14, never 0
        return f"({a}) {op} ({b})"

    def index(self, scalars, size):
        e = self.expr(scalars, [], 2)
        return f"((({e}) % {size}) + {size}) % {size}"             # always 0 .. size - 1

    def target(self, scalars, arrays):
        if arrays and self.rng.random() < 0.4:
            name, size = self.rng.choice(arrays)
            return f"{name}[{self.index(scalars, size)}]"
        return self.rng.choice(scalars)

    def block(self, scalars, arrays, depth, in_loop, in_switch, out):
        for _ in range(self.rng.randint(1, 4 if depth < 2 else 2)):
            self.stmt(scalars, arrays, depth, in_loop, in_switch, out)

    def stmt(self, scalars, arrays, depth, in_loop, in_switch, out):
        r, pad = self.rng, "    " * (depth + 1)
        k = r.random()
        if k < 0.25 or depth >= 3:
            t = self.target(scalars, arrays)
            form = r.choice(["=", "+=", "-=", "*=", "/=", "%=", "++", "--", "pre"])
            if form in ("++", "--"):
                out.append(f"{pad}{t}{form};")
            elif form == "pre":
                out.append(f"{pad}++{t};")
            elif form in ("/=", "%="):
                out.append(f"{pad}{t} {form} (({self.expr(scalars, arrays)}) % 5 + 6);")
            else:
                out.append(f"{pad}{t} {form} {self.expr(scalars, arrays)};")
        elif k < 0.35:
            out.append(f'{pad}printf("%d\\n", {self.expr(scalars, arrays)});')
        elif k < 0.47:
            out.append(f"{pad}if ({self.expr(scalars, arrays)}) {{")
            self.block(scalars, arrays, depth + 1, in_loop, in_switch, out)
            if r.random() < 0.5:
                out.append(f"{pad}}} else {{")
                self.block(scalars, arrays, depth + 1, in_loop, in_switch, out)
            out.append(f"{pad}}}")
        elif k < 0.58:
            v = self.fresh("i")
            out.append(f"{pad}for (int {v} = 0; {v} < {r.randint(1, 6)}; {v}++) {{")
            self.block(scalars + [v] if False else scalars, arrays, depth + 1, True, False, out)
            self._maybe_jump(pad + "    ", True, out, scalars, arrays)
            out.append(f"{pad}}}")
        elif k < 0.66:
            w = self.fresh("w")
            out.append(f"{pad}int {w} = {r.randint(1, 6)};")
            out.append(f"{pad}while ({w} > 0) {{")
            out.append(f"{pad}    {w}--;")
            self.block(scalars, arrays, depth + 1, True, False, out)
            self._maybe_jump(pad + "    ", True, out, scalars, arrays)
            out.append(f"{pad}}}")
        elif k < 0.73:
            w = self.fresh("d")
            out.append(f"{pad}int {w} = {r.randint(0, 5)};")
            out.append(f"{pad}do {{")
            out.append(f"{pad}    {w}--;")
            self.block(scalars, arrays, depth + 1, True, False, out)
            out.append(f"{pad}}} while ({w} > 0);")
        elif k < 0.85:
            out.append(f"{pad}switch (({self.expr(scalars, arrays)}) % 4) {{")
            for value in r.sample([-3, -2, -1, 0, 1, 2, 3], r.randint(1, 4)):
                out.append(f"{pad}    case {value}:")
                self.block(scalars, arrays, depth + 2, in_loop, True, out)
                if r.random() < 0.7:
                    out.append(f"{pad}        break;")
            if r.random() < 0.6:
                out.append(f"{pad}    default:")
                self.block(scalars, arrays, depth + 2, in_loop, True, out)
            out.append(f"{pad}}}")
        elif k < 0.92 and self.funcs:
            name, nargs = r.choice(self.funcs)
            args = ", ".join(self.expr(scalars, arrays) for _ in range(nargs))
            out.append(f"{pad}{self.target(scalars, arrays)} = {name}({args});")
        else:
            out.append(f'{pad}printf("%d %d\\n", {self.expr(scalars, arrays)}, {self.expr(scalars, arrays)});')

    def _maybe_jump(self, pad, in_loop, out, scalars, arrays):
        if in_loop and self.rng.random() < 0.35:
            out.append(f"{pad}if ({self.expr(scalars, arrays)}) {self.rng.choice(['break', 'continue'])};")

    def program(self):
        r = self.rng
        self.funcs, out = [], ["#include <stdio.h>", "#define SIZE 5"]
        out.append("int g1;")
        out.append("int table[SIZE] = {3, 1, 4, 1, 5};")
        for f in range(r.randint(0, 2)):
            name, nargs = f"f{f}", r.randint(1, 3)
            params = [f"p{k}" for k in range(nargs)]
            body = [f"int {name}({', '.join('int ' + p for p in params)}) {{", "    int t = 0;"]
            self.block(params + ["t", "g1"], [("table", 5)], 0, False, False, body)
            body.append(f"    return {self.expr(params + ['t'], [('table', 5)])};")
            body.append("}")
            out += body
            self.funcs.append((name, nargs))
        out.append("int main() {")
        out.append(f"    int a = {r.randint(-9, 9)}, b = {r.randint(-9, 9)}, c = {r.randint(1, 9)};")
        out.append("    int arr[4] = {7, -2, 9, 0};")
        main = []
        self.block(["a", "b", "c", "g1"], [("arr", 4), ("table", 5)], 0, False, False, main)
        self.block(["a", "b", "c", "g1"], [("arr", 4), ("table", 5)], 0, False, False, main)
        out += main
        out.append('    printf("%d %d %d %d\\n", a, b, c, g1);')
        out.append("    return (a + b + c) % 100;")
        out.append("}")
        return "\n".join(out) + "\n"


def run_cmd(cmd, timeout=10):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return p.stdout, p.returncode


def diff_test(n, rng, cc, log):
    failures, steps_out = [], 0
    with tempfile.TemporaryDirectory() as d:
        for k in range(n):
            src = Gen(rng).program()
            try:
                r = analyze(src)
                if not r["success"]:
                    failures.append((src, "a generated (valid) program was rejected: " + "; ".join(e["message"] for e in r["errors"][:3]), ""))
                    continue
                vm = run_program(r["ir"], r["line_map"], max_steps=2_000_000)
                if vm["error"]:
                    if vm["error_kind"] == "step_limit":
                        steps_out += 1
                        continue
                    failures.append((src, f"VM runtime error on a valid program: {vm['error']}", ""))
                    continue
                ref = Path(d) / "ref.c"
                ref.write_text(src, encoding="utf-8")
                exe = Path(d) / ("ref.exe" if sys.platform == "win32" else "ref")
                subprocess.run([cc, "-w", "-O0", "-fwrapv", "-o", str(exe), str(ref)], check=True, capture_output=True)
                g_out, g_code = run_cmd([str(exe)])
                b = backend.build(r["ir"], r["line_map"], d, "ours")
                o_out, o_code = (run_cmd([b["exe"]] if b["mode"] == "native" else [sys.executable, b["exe"]])
                                  if b["ok"] else ("<build failed>", -1))
                vm_code = (vm["return_value"] or 0) % 256
                if not (vm["stdout"] == g_out == o_out and vm_code == g_code == o_code):
                    failures.append((src, f"different behaviour: gcc exit {g_code} {g_out[:80]!r} | VM exit {vm_code} "
                                          f"{vm['stdout'][:80]!r} | our build exit {o_code} {o_out[:80]!r}", ""))
            except Exception as exc:
                failures.append((src, f"{type(exc).__name__}: {exc}", traceback.format_exc()))
            if log and (k + 1) % 50 == 0:
                print(f"  {k + 1}/{n} programs, {len(failures)} differences", flush=True)
    return {"programs": n, "too_slow": steps_out, "failures": failures}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--crash", type=int, default=None, help="number of inputs for the crash test")
    ap.add_argument("--diff", type=int, default=None, help="number of random programs to compare with gcc")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    if args.crash is None and args.diff is None:
        args.crash, args.diff = 2000, 150
    rng = random.Random(args.seed)
    cc = shutil.which("gcc") or shutil.which("clang")
    ok = True
    if args.crash:
        print(f"Crash test: {args.crash} broken or extreme inputs (seed {args.seed})")
        res = crash_test(args.crash, rng, cc, not args.quiet)
        print(f"  {res['inputs']} inputs, {res['accepted']} accepted as valid, generated C compiled for {res['c_compiled']} of them")
        print(f"  crashes: {len(res['failures'])}")
        for src, msg, tb in res["failures"][:5]:
            ok = False
            print("  ---", msg, "\n  input:", repr(src[:300]), "\n", tb[-1500:])
        ok = ok and not res["failures"]
    if args.diff:
        if not cc:
            print("Comparison with gcc skipped: no gcc or clang found.")
        else:
            print(f"Comparison with gcc: {args.diff} random valid programs (seed {args.seed})")
            res = diff_test(args.diff, rng, cc, not args.quiet)
            print(f"  {res['programs']} programs, {res['too_slow']} skipped as too long-running, differences: {len(res['failures'])}")
            for src, msg, tb in res["failures"][:3]:
                print("  ---", msg, "\n", src[:2000], "\n", tb[-1500:])
            ok = ok and not res["failures"]
    print("\nAll good: nothing crashed and every program matched gcc." if ok else "\nProblems found (see above).")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
