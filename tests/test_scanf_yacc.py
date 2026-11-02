"""Tests for scanf input and the lex/yacc front ends (PLY LALR(1) and flex/bison)."""
import tempfile
import unittest

from compiler import backend, yacc_frontend
from compiler.parser import Parser
from compiler.lexer import Lexer
from compiler.pipeline import analyze
from ai_assistant.ir_interpreter import run_program, SAMPLE_INPUT

READER = """#include <stdio.h>
int main() {
    int a;
    int b;
    printf("a? ");
    scanf("%d", &a);
    scanf("%d %d", &b, &a);
    printf("sum=%d\\n", a + b);
    return a;
}
"""

PROGRAMS = [
    READER,
    "int sq(int x) { return x * x; }\nint main() { int i; for (i = 0; i < 3; i = i + 1) { print(sq(i)); } return 0; }",
    "int main() { int x = 1; if (x > 0) if (x > 5) x = 2; else x = 3; return x; }",
    "int g;\nvoid f() { g = g + 1; }\nint main() { while (g < 4) f(); return g; }",
]


def hand_ast(src):
    return Parser(Lexer(src).tokenize()).parse()


class ScanfTests(unittest.TestCase):
    def test_scanf_compiles_and_reads_in_vm(self):
        r = analyze(READER)
        self.assertTrue(r["success"], r["errors"])
        self.assertIn("READ_INT", "\n".join(map(str, r["ir"])))
        out = run_program(r["ir"], r["line_map"], stdin="4 10 7")
        self.assertIsNone(out["error"])
        self.assertEqual(out["return_value"], 7)
        self.assertIn("sum=17", out["stdout"])

    def test_missing_input_is_reported(self):
        r = analyze(READER)
        out = run_program(r["ir"], r["line_map"], stdin="4")
        self.assertEqual(out["error_kind"], "input")

    def test_sample_input_used_when_none_given(self):
        r = analyze(READER)
        out = run_program(r["ir"], r["line_map"], stdin=None)
        self.assertIsNone(out["error"])
        self.assertTrue(out["used_sample_input"])
        first = [int(t) for t in SAMPLE_INPUT.split()[:3]]
        self.assertEqual(out["return_value"], first[2])

    def test_scanf_errors(self):
        bad = {
            'int main() { int x; scanf("%d", x); return 0; }': "&",
            'int main() { int x; scanf("%d %d", &x); return 0; }': None,
            'int main() { scanf("%d", &y); return 0; }': None,
        }
        for src, word in bad.items():
            errs = analyze(src)["errors"]
            self.assertTrue(errs, src)
            if word:
                self.assertIn(word, " ".join(e["message"] + " " + str(e.get("hint", "")) for e in errs))

    def test_scanf_initialises_variable(self):
        r = analyze('int main() { int x; scanf("%d", &x); return x; }')
        self.assertFalse([w for w in r["warnings"] if "initiali" in w.get("message", "").lower()])

    def test_native_build_reads_stdin(self):
        if not backend.find_c_compiler()[0]:
            self.skipTest("no C compiler")
        r = analyze(READER)
        with tempfile.TemporaryDirectory() as d:
            b = backend.build(r["ir"], r["line_map"], d, "reader")
            self.assertTrue(b["ok"], b["log"])
            run = backend.run_executable(b, stdin="4 10 7\n")
        self.assertEqual(run["exit_code"], 7)
        self.assertIn("sum=17", run["stdout"])


class YaccTests(unittest.TestCase):
    def test_grammar_has_no_conflicts(self):
        info = yacc_frontend.grammar_info()
        self.assertEqual(info["conflicts"], [])
        self.assertGreater(len(info["productions"]), 40)

    def test_yacc_and_hand_parser_build_same_tree(self):
        for src in PROGRAMS:
            res = yacc_frontend.parse(src)
            self.assertTrue(res["accepted"], (src, res["errors"]))
            self.assertIsNone(yacc_frontend.same_ast(hand_ast(src), res["ast"]), src)

    def test_dangling_else_binds_to_inner_if(self):
        src = PROGRAMS[2]
        for frontend in ("hand", "yacc"):
            r = analyze(src, frontend=frontend)
            self.assertEqual(run_program(r["ir"], r["line_map"])["return_value"], 3, frontend)

    def test_pipeline_with_yacc_frontend_runs(self):
        r = analyze(READER, frontend="yacc")
        self.assertTrue(r["success"], r["errors"])
        self.assertEqual(r["frontend"], "yacc")
        self.assertEqual(run_program(r["ir"], r["line_map"], stdin="1 2 3")["return_value"], 3)

    def test_syntax_errors_recover_and_list_expected(self):
        src = "int main() {\n  int x = ;\n  x = 5 +;\n  return x;\n}"
        res = yacc_frontend.parse(src)
        self.assertFalse(res["accepted"])
        self.assertEqual([e["line"] for e in res["errors"]], [2, 3])
        self.assertTrue(any("expected" in e["message"].lower() for e in res["errors"]))

    def test_trace_shows_shift_reduce_accept(self):
        res = yacc_frontend.parse("int main() { return 1 + 2; }", trace=True)
        kinds = {row["kind"] for row in res["trace"]}
        self.assertTrue({"shift", "reduce", "accept"} <= kinds, kinds)
        for row in res["trace"]:
            self.assertEqual(set(row), {"state", "stack", "lookahead", "action", "kind"})

    def test_flex_bison_checker(self):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        import build_yacc
        if not build_yacc.status()["available"]:
            self.skipTest("flex/bison not installed")
        ok = build_yacc.check(READER)
        self.assertTrue(ok["ok"], ok.get("log"))
        self.assertTrue(ok["accepted"])
        self.assertEqual(ok["functions"], ["main"])
        bad = build_yacc.check("int main() {\n  int x = ;\n  return x;\n}")
        self.assertFalse(bad["accepted"])
        self.assertEqual(bad["errors"][0]["line"], 2)


if __name__ == "__main__":
    unittest.main()
