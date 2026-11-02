"""Do the three parsers agree?

    python tools/parser_agreement.py

1. hand-written recursive-descent parser   (compiler/parser.py, top-down)
2. PLY lex & yacc LALR parser              (compiler/yacc_frontend.py, bottom-up)
3. flex & bison                            (grammar/minic.l, grammar/minic.y; needs flex, bison, gcc)

Corpus: every example and benchmark program, plus broken versions made by the benchmark's
mutation tool. For each program the parsers must agree on accept/reject. When they accept:
1 and 2 must build the identical syntax tree (ignoring positions); 3 must report the same
functions and the same number of statements. The two lexers are also compared token by token.
"""
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from compiler.lexer import Lexer, LexErrors                      # noqa: E402
from compiler.parser import Parser, FunctionNode                # noqa: E402
from compiler import yacc_frontend as Y                         # noqa: E402
import build_yacc                                               # noqa: E402


def hand(src):
    try:
        toks = Lexer(src).tokenize()
    except LexErrors:
        return None, "lex"
    try:
        return Parser(toks).parse(), None
    except SyntaxError as e:
        return None, str(e)


def corpus(seed=3, per_program=4):
    import benchmark
    files = sorted((ROOT / "workspace").glob("*.c")) + sorted((ROOT / "bench" / "programs").glob("*.c"))
    items = [(f"{p.parent.name}/{p.name}", p.read_text(encoding="utf-8")) for p in files]
    good = [(Path(n), s) for n, s in items if hand(s)[0] is not None]
    for b in benchmark.make_broken(good, random.Random(seed), per_program=per_program):
        items.append((f"mutant {b['program']}: {b['what']}", b["broken"]))
    items += [
        ("dangling else", "int main() { int a = 1; if (a) if (a > 2) print(1); else print(2); return 0; }"),
        ("precedence", "int main() { return 1 + 2 * 3 - 4 / 2 % 3 < 5 == 1 && 0 || 1; }"),
        ("unary minus", "int main() { int x = -3; return - -x * -(2 + 1); }"),
        ("nested calls", "int f(int a, int b) { return a - b; }\nint main() { return f(f(1, 2), f(3, f(4, 5))); }"),
        ("empty statements", "int main() { ; ; { } for (;;) { return 0; } }"),
        ("printf/scanf", '#include <stdio.h>\nint main() { int a; scanf("%d", &a); printf("%d %d\\n", a, a * 2); return 0; }'),
        ("missing paren", "int main() { if (1 { return 0; } }"),
        ("stray brace", "int main() { return 0; } }"),
        ("bad for", "int main() { int i; for (i = 0; i < 3; i++) { } return 0; }"),
        ("arrays", "int a[3] = {1, 2, 3};\nint m[2][2] = {{1, 2}, {3, 4}};\nint main() { int i; a[0] = m[1][0] + a[2]; for (i = 0; i < 3; i = i + 1) { m[0][1] = m[0][1] + a[i]; } return a[0]; }"),
        ("array parameters", "int sum(int v[], int n) { int s = 0; int i; for (i = 0; i < n; i = i + 1) s = s + v[i]; return s; }\nint row(int g[][3], int r) { return g[r][0]; }\nint main() { int x[] = {4, 5}; int g[2][3]; g[1][0] = 7; return sum(x, 2) + row(g, 1); }"),
        ("strings", '#include <string.h>\nint main() { char s[20] = "hi"; char t[] = "there"; char names[2][8] = {"ann", "bob"}; strcat(s, t); printf("%s %c %d\\n", names[1], s[0], strlen(s)); return strcmp(s, t); }'),
        ("types and casts", "int main() { char c = 'a'; short h = 2; long big = 3000000000; float f = 2.5f; double d = 1.5e3; int k = (int) d / sizeof(int); return (int) (f * h) + c - k + (big > 0); }"),
        ("scanf targets", '#include <stdio.h>\nint main() { char w[10]; int a[3]; double d; scanf("%s %d %lf", w, &a[1], &d); printf("%s %d %.1f\\n", w, a[1], d); return 0; }'),
        ("missing ]", "int main() { int a[3 = {1, 2, 3}; return a[0]; }"),
        ("empty inner size", "int main() { int m[2][]; return 0; }"),
        ("bad char literal", "int main() { char c = 'ab'; return c; }"),
        ("cast missing paren", "int main() { double d = (double 3; return 0; }"),
    ]
    return items


def main():
    st = build_yacc.status()
    use_bison = False
    if st["available"]:
        ok, log = build_yacc.build()
        use_bison = ok
        if not ok:
            print("flex/bison build failed:\n" + log)
    else:
        print(f"flex/bison check skipped (missing: {', '.join(st['missing'])})\n")
    rows, bad = [], 0
    for name, src in corpus():
        h_ast, h_err = hand(src)
        y = Y.parse(src)
        problems = []
        if (h_ast is not None) != y["accepted"]:
            problems.append(f"accept/reject differs (hand {'accepts' if h_ast is not None else 'rejects'}, yacc {'accepts' if y['accepted'] else 'rejects'})")
        elif h_ast is not None:
            d = Y.same_ast(h_ast, y["ast"])
            if d:
                problems.append("different trees at " + d)
            ht = [t.value for t in Lexer(src).tokenize() if t.kind != "EOF"]
            pt = [t[1][1] if t[0] == "CHARLIT" else str(t[1]) for t in Y.tokens_of(src)[0]]
            if ht != pt:
                problems.append("token streams differ")
        b = None
        if use_bison:
            b = build_yacc.check(src)
            if b.get("accepted") != (h_ast is not None):
                problems.append(f"bison {'accepts' if b.get('accepted') else 'rejects'}")
            elif h_ast is not None:
                funcs = [s.name for s in h_ast.statements if isinstance(s, FunctionNode)]
                if b.get("functions") != funcs:
                    problems.append(f"bison sees functions {b.get('functions')} not {funcs}")
                if b.get("statements") != Y.statement_count(h_ast):
                    problems.append(f"bison counts {b.get('statements')} statements, not {Y.statement_count(h_ast)}")
        verdict = "accept" if h_ast is not None else "reject"
        rows.append((name, verdict, problems))
        bad += bool(problems)
        print(f"  {'ok  ' if not problems else 'FAIL'} {verdict:<6} {name[:70]}" + ("" if not problems else "\n         " + "; ".join(problems)))
    who = "hand-written, PLY yacc" + (" and flex/bison" if use_bison else "")
    print(f"\n{len(rows)} programs, {sum(r[1] == 'accept' for r in rows)} valid and {sum(r[1] == 'reject' for r in rows)} broken. "
          + (f"The {who} parsers agree on all of them." if not bad else f"{bad} disagreement(s)."))
    info = Y.grammar_info()
    print(f"LALR grammar: {len(info['productions'])} productions, {info['states']} states, {len(info['conflicts'])} conflicts.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
