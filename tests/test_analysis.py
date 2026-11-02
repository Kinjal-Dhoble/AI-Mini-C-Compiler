"""Tests for error recovery, functions, loops/CFG, call graph, static checks, refactoring,
bug detection and the benchmark mutations."""
import random
import unittest
from compiler.lexer import Lexer, LexErrors
from compiler.parser import Parser, ParseErrors
from compiler.pipeline import analyze
from compiler.refactor import find_refactorings
from compiler import backend
from ai_assistant.context_builder import check_source
from ai_assistant.ir_interpreter import run_program
from ai_assistant.bug_detector import detect
from ai_assistant.ai_features import _same_program


def errors(src):
    return analyze(src)["errors"]


class RecoveryTests(unittest.TestCase):
    def test_reports_all_syntax_errors(self):
        src = "int main() {\n  int x = ;\n  x = 5 +;\n  int y = 2;\n  return y\n}"
        errs = errors(src)
        self.assertEqual([e["line"] for e in errs], [2, 3, 6])
        self.assertTrue(all(e.get("recovery") for e in errs))

    def test_missing_semicolon_keeps_tree_and_finds_semantic_errors(self):
        src = "int main() {\n    int total = 0\n    total = total + 5;\n    print(totl);\n    return total;\n}"
        errs = errors(src)
        self.assertEqual([e["category"] for e in errs], ["Syntax Error", "Semantic Error"])
        self.assertIn("Assumed a missing ';'", errs[0]["recovery"])

    def test_unclosed_brace_hint_names_the_line(self):
        errs = errors("int main() {\n  int a = 1;\n  if (a) {\n    a = 2;\n  return a;\n}")
        self.assertIn("line 3", errs[-1]["hint"])

    def test_plus_plus_inside_an_expression_gets_a_hint(self):
        self.assertEqual(errors("int main() { int a = 1; a++; ++a; a += 2; return a; }"), [])
        errs = errors("int main() { int a = 1; int b = a++ + 1; return b; }")
        self.assertIn("on its own", errs[0]["hint"])

    def test_all_lexical_errors(self):
        with self.assertRaises(LexErrors) as cm:
            Lexer("int main() { int a = 5 @ 2; # \n return a$; }").tokenize()
        self.assertEqual([e["found"] for e in cm.exception.errors], ["@", "#", "$"])

    def test_still_a_syntax_error_for_old_callers(self):
        with self.assertRaises(SyntaxError):
            Parser(Lexer("int x = 1").tokenize()).parse()


class FunctionTests(unittest.TestCase):
    SRC = """int calls = 0;
int fact(int n) { calls = calls + 1; if (n <= 1) { return 1; } return n * fact(n - 1); }
int gcd(int a, int b) { while (b != 0) { int t = b; b = a % b; a = t; } return a; }
void show(int x) { print(x); }
int main() { show(fact(5)); print(gcd(84, 36)); print(calls); return gcd(10, 4); }"""

    def test_semantic_call_checks(self):
        msgs = [e["message"] for e in errors("int f(int a) { return a; }\nvoid g() { }\nint main() { int x = f(1, 2); int y = g(); return h(3); }")]
        self.assertTrue(any("expects 1 argument, got 2" in m for m in msgs))
        self.assertTrue(any("with void" in m for m in msgs))
        self.assertTrue(any("Undeclared function 'h'" in m for m in msgs))

    def test_vm_runs_recursion_and_globals(self):
        c = check_source(self.SRC)
        r = run_program(c["ir"], c["line_map"])
        self.assertEqual((r["output"], r["return_value"]), ([120, 12, 5], 2))

    def test_vm_stack_overflow_and_overflow(self):
        c = check_source("int f(int n) { return f(n + 1); }\nint main() { return f(1); }")
        self.assertEqual(run_program(c["ir"])["error_kind"], "stack_overflow")
        c = check_source("int main() { int big = 50000; print(big * big); return 0; }")
        self.assertEqual(run_program(c["ir"])["overflows"][0]["exact"], 2500000000)

    @unittest.skipUnless(backend.find_c_compiler()[0], "no C compiler installed")
    def test_native_functions_match_vm(self):
        import tempfile, shutil
        c = check_source(self.SRC)
        out = tempfile.mkdtemp()
        try:
            b = backend.build(c["ir"], c["line_map"], out, "fn")
            self.assertTrue(b["ok"], b["log"])
            r = backend.run_executable(b)
            self.assertEqual(r["stdout"].split(), ["120", "12", "5"])
            self.assertEqual(r["exit_code"], 2)
        finally:
            shutil.rmtree(out, ignore_errors=True)


class FlowTests(unittest.TestCase):
    SRC = """int fact(int n) { if (n <= 1) { return 1; } return n * fact(n - 1); }
int unused(int a) { return a; }
int main() {
    int n = 20; int count = 0; int i; int j;
    for (i = 0; i < n; i = i + 1) {
        for (j = 0; j < n; j = j + 1) { if (i + j == 17) { count = count + 1; } }
    }
    int k = 1;
    while (k < n) { k = k * 2; }
    print(count + fact(4));
    return count;
    print(1);
}"""

    def test_natural_loops_and_depth(self):
        r = analyze(self.SRC)
        self.assertEqual([(L["header_line"], L["depth"]) for L in r["loops"]], [(5, 1), (6, 2), (9, 1)])
        self.assertTrue(all(L["back_edges"] for L in r["loops"]))

    def test_complexity_estimates(self):
        cx = analyze(self.SRC)["complexity"]
        self.assertEqual(cx["main"]["big_o"], "O(n^2)")
        self.assertEqual(cx["fact"]["big_o"], "recursive")
        self.assertEqual(analyze("int g(int a, int b) { while (b != 0) { int r = a % b; a = b; b = r; } return a; }\nint main() { return g(4, 6); }")["complexity"]["g"]["big_o"], "data-dependent")

    def test_call_graph(self):
        cg = analyze(self.SRC)["call_graph"]
        self.assertEqual(cg["recursive"], ["fact"])
        self.assertEqual(cg["unreachable"], ["unused"])
        self.assertEqual(cg["calls"]["main"], ["fact"])

    def test_static_rules(self):
        rules = {d.get("rule") for d in analyze(self.SRC)["static"]}
        self.assertTrue({"magic_number", "unreachable", "unused_function"} <= rules)
        rules = {d.get("rule") for d in analyze("int main() { if (0) { print(5); } return 0; }")["static"]}
        self.assertIn("constant_condition", rules)


class RefactorTests(unittest.TestCase):
    SRC = """int is_even(int n) {
    if (n % 2 == 0) {
        return 1;
    } else {
        return 0;
    }
}
int main() {
    int total = 0;
    int i = 0;
    while (i < 10) {
        // keep this comment
        if (is_even(i) == 1) {
            if (i > 2) {
                total = total + i;
            }
        }
        i = i + 1;
    }
    int d = 3;
    if (d != 0) {
        if (total / d > 5) { print(total / d); }
    }
    print(total);
    return 0;
}"""

    def test_patterns_found_and_verified(self):
        r = find_refactorings(self.SRC)
        kinds = [it["kind"] for it in r["items"]]
        self.assertIn("return_condition", kinds)
        self.assertIn("while_to_for", kinds)
        self.assertIn("merge_nested_if", kinds)
        self.assertTrue(all(it["verified"] for it in r["items"]))
        self.assertTrue(r["all_verified"])

    def test_comments_kept_and_division_merge_safe(self):
        r = find_refactorings(self.SRC)
        w2f = next(it for it in r["items"] if it["kind"] == "while_to_for")
        self.assertIn("// keep this comment", w2f["after"])
        self.assertFalse(w2f["comments_lost"])
        merged = [it for it in r["items"] if it["kind"] == "merge_nested_if" and it["line"] == 21]
        self.assertTrue(merged and merged[0]["verified"])   # && short-circuits, so d != 0 still guards the division

    def test_short_circuit_like_c(self):
        from ai_assistant.context_builder import check_source
        c = check_source("int main() { int d = 0; if (d != 0 && 10 / d > 1) { print(1); } if (d == 0 || 10 / d > 1) { print(2); } return 0; }")
        r = run_program(c["ir"])
        self.assertEqual((r["error"], r["stdout"]), (None, "2\n"))


class BugTests(unittest.TestCase):
    SRC = """int average(int total, int count) { return total / count; }
int sign(int x) {
    if (x > 0) { return 1; }
    if (x < 0) { return 0 - 1; }
}
int main() {
    int sum = 0;
    sum == 100;
    int big = 50000;
    print(big * big);
    print(sign(0));
    print(average(sum, 0));
    return 0;
}"""

    def test_rules_confirmed_by_running(self):
        r = detect(check_source(self.SRC))
        confirmed = {f["rule"] for f in r["findings"] if f["confirmed"]}
        self.assertTrue({"possible_div_zero", "overflow", "missing_return"} <= confirmed)
        self.assertIn("no_effect", {f["rule"] for f in r["findings"]})
        self.assertEqual(r["run"]["error_kind"], "div_zero")

    def test_infinite_loop_rules(self):
        r = detect(check_source("int main() { int i = 0; int n = 5; while (i < n) { print(i); } return 0; }"))
        self.assertTrue(any(f["rule"] == "infinite_loop" and f["confirmed"] for f in r["findings"]))
        r = detect(check_source("int main() { int i; for (i = 0; i < 5; i = i - 1) { print(i); } return 0; }"))
        self.assertTrue(any(f["rule"] == "infinite_loop" for f in r["findings"]))


class VerificationGuardTests(unittest.TestCase):
    def test_rejects_empty_or_rewritten_programs(self):
        orig = "int f(int a) { return a + 1; }\nint main() { int x = 5\n print(f(x)); return 0; }"
        self.assertIn("empty", _same_program(orig, ""))
        self.assertIn("f()", _same_program(orig, "int main() { return 0; }"))
        self.assertIsNone(_same_program(orig, orig.replace("= 5", "= 5;")))


class BenchmarkMutationTests(unittest.TestCase):
    def test_mutations_break_programs(self):
        import sys, pathlib
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))
        import benchmark
        src = (pathlib.Path(__file__).resolve().parent.parent / "bench" / "programs" / "collatz.c").read_text()
        out = benchmark.make_broken([(pathlib.Path("collatz.c"), src)], random.Random(1), per_program=5)
        self.assertGreaterEqual(len(out), 3)
        self.assertTrue(all(not check_source(b["broken"])["ok"] for b in out))


if __name__ == "__main__":
    unittest.main()


class IncludeTests(unittest.TestCase):
    def test_include_lines_are_skipped_with_a_note(self):
        r = analyze("#include <stdio.main>\n#include \"my.h\"\nint main() { print(1); return 0; }")
        self.assertTrue(r["success"])
        self.assertEqual([w["category"] for w in r["warnings"]], ["Note", "Note"])
        self.assertIn("<stdio.h>", r["warnings"][0]["message"])

    def test_define_constants_work_and_function_macros_are_errors(self):
        self.assertTrue(analyze("#define N 5\nint a[N];\nint main() { return N; }")["success"])
        errs = analyze("#define SQ(x) x*x\nint main() { return 0; }")["errors"]
        self.assertIn("Function-like macro", errs[0]["message"])
        self.assertIn("#if", analyze("#ifdef X\nint main() { return 0; }")["errors"][0]["hint"])


class PrintfTests(unittest.TestCase):
    SRC = '#include <stdio.h>\nint main() {\n    int a = 6;\n    printf("a = %d, a*a = %d (100%%)\\n", a, a * a);\n    printf("tab\\there \\"q\\"\\n");\n    print(a);\n    return 0;\n}'

    def test_output_text(self):
        c = check_source(self.SRC)
        self.assertTrue(c["ok"], c["errors"])
        self.assertEqual(run_program(c["ir"])["stdout"], 'a = 6, a*a = 36 (100%)\ntab\there "q"\n6\n')

    def test_format_errors(self):
        msgs = lambda s: " ".join(e["message"] for e in analyze(s)["errors"])
        self.assertIn("2 %d but 1 value", msgs('int main() { printf("%d %d\\n", 1); return 0; }'))
        self.assertIn("%s does not match", msgs('int main() { printf("%s\\n", 1); return 0; }'))        # strings now exist
        self.assertIn("'%p' is not supported", msgs('int main() { printf("%p\\n", 1); return 0; }'))
        self.assertIn("format string", msgs('int main() { printf(5); return 0; }'))
        self.assertIn("with a string", msgs('int main() { int s = "hi"; return 0; }'))

    @unittest.skipUnless(backend.find_c_compiler()[0] and backend.find_c_compiler()[1] != "tcc", "needs gcc or clang")
    def test_agrees_with_gcc(self):
        import sys, pathlib, tempfile
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))
        import difftest
        cc, fam = backend.find_c_compiler()
        root = pathlib.Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as tmp:
            for f in sorted((root / "bench" / "programs").glob("*.c")) + [root / "workspace" / "hello.c"]:
                r = difftest.compare(f, cc, fam, tmp)
                self.assertEqual(r["status"], "match", f"{f.name}: {r.get('why')} {r.get('results')}")
