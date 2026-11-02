"""Tests for data types, arrays, strings and the bug rules that go with them."""
import tempfile
import unittest
from pathlib import Path

from compiler import backend, yacc_frontend
from compiler.lexer import Lexer
from compiler.parser import Parser
from compiler.pipeline import analyze
from compiler.printer import stmt
from compiler.refactor import find_refactorings
from ai_assistant.context_builder import check_source
from ai_assistant.ir_interpreter import run_program, c_format
from ai_assistant.bug_detector import detect

ROOT = Path(__file__).resolve().parent.parent
WS = ROOT / "workspace"


def run(src, stdin=None):
    r = analyze(src)
    assert r["success"], r["errors"]
    return run_program(r["ir"], r["line_map"], stdin=stdin)


def messages(src):
    return " | ".join(e["message"] for e in analyze(src)["errors"] + analyze(src)["warnings"])


class ParsingTests(unittest.TestCase):
    SOURCES = [
        'int m[2][3] = {{1, 2, 3}, {4, 5, 6}};\ndouble f(int a[], double g[][3]) { return (double) a[0] / g[1][2]; }\n'
        'int main() { char s[] = "hi\\n"; char c = \'\\\'\'; float x = 2.5f; long n = 3000000000; s[0] = c; return sizeof(int) + sizeof(s); }',
        'int main() { char w[10]; int a[3]; scanf("%s %d", w, &a[1]); printf("%s %c %5.2f\\n", w, w[0], 1.5); return 0; }',
        'int main() { int a[] = {1, 2, 3,}; int i; for (i = 0; i < 3; i = i + 1) a[i] = -a[i] * 2; return a[2]; }',
    ]

    def test_both_parsers_build_the_same_tree(self):
        for src in self.SOURCES:
            y = yacc_frontend.parse(src)
            self.assertTrue(y["accepted"], y["errors"])
            self.assertIsNone(yacc_frontend.same_ast(Parser(Lexer(src).tokenize()).parse(), y["ast"]), src)

    def test_grammar_still_has_no_conflicts(self):
        self.assertEqual(yacc_frontend.grammar_info()["conflicts"], [])

    def test_printer_round_trip(self):
        for f in ("arrays.c", "strings.c", "datatypes.c"):
            ast = Parser(Lexer((WS / f).read_text()).tokenize()).parse()
            text = "\n".join(l for s in ast.statements for l in stmt(s))
            again = Parser(Lexer(text).tokenize()).parse()
            self.assertIsNone(yacc_frontend.same_ast(ast, again), f)

    def test_bad_char_literal_is_a_lexical_error(self):
        errs = analyze("int main() { char c = 'ab'; return 0; }")["errors"]
        self.assertEqual(errs[0]["category"], "Lexical Error")


class SemanticTests(unittest.TestCase):
    def test_array_rules(self):
        m = messages('int main() { int a[3] = {1, 2, 3, 4}; int n = 3; int b[n]; int c[2]; a = c; a[1.5] = 2; int x = 1; x[0] = 2; return 0; }')
        for part in ("Too many initialisers", "must be a constant number", "whole array", "must be a whole number", "not an array"):
            self.assertIn(part, m)

    def test_string_rules(self):
        m = messages('int main() { char s[3] = "hello"; char t[5] = "hello"; char c = "x"; int k = 5; '
                     'strcpy("hi", s); k = strlen(k); if (s == t) { k = 1; } return 0; }')
        for part in ("too long", "no room for the '\\0'", "single quotes", "writes into its first argument", "needs a string", "addresses"):
            self.assertIn(part, m)

    def test_printf_and_scanf_types(self):
        m = messages('int main() { double d = 1.0; int x = 1; long l = 2; char w[5]; printf("%d %s %d\\n", d, x, l); scanf("%f", &x); scanf("%d", x); scanf("%s", &w); return 0; }')
        for part in ("for a float or double use %f", "%s prints a string", "needs %ld", "use %d", "write &x", "needs no &"):
            self.assertIn(part, m)

    def test_numbers_convert_like_c(self):
        self.assertTrue(analyze("int main() { double d = 7; int i = 2.9; char c = 65; float f = d / 2; return i + c; }")["success"])


class ExecutionTests(unittest.TestCase):
    def test_types_behave_like_c(self):
        r = run('int main() { int i = 9.99; char c = 300; short s = 40000; double d = 7 / 2; float f = 0.1f; '
                'printf("%d %d %d %.1f %.10f %d %ld\\n", i, c, s, d, f, 7 / 2, 3000000000); return (double) 7 / 2 * 2; }')
        self.assertEqual(r["stdout"], "9 44 -25536 3.0 0.1000000015 3 3000000000\n")
        self.assertEqual(r["return_value"], 7)

    def test_arrays_strings_demo_programs(self):
        out = run((WS / "arrays.c").read_text())["stdout"]
        self.assertIn("Sorted: 64 72 78 85 90", out)
        out = run((WS / "strings.c").read_text())["stdout"]
        self.assertIn("Full name: Kinjal Dhoble (13 characters)", out)
        self.assertIn("Reversed: LAJNIK", out)
        out = run((WS / "datatypes.c").read_text())["stdout"]
        self.assertIn("Average with (double): 76.167", out)

    def test_runtime_checks(self):
        cases = {
            "int main() { int a[3]; a[3] = 1; return 0; }": "out_of_bounds",
            "int main() { int a[3]; return a[1]; }": "uninit",
            'int main() { char s[4]; strcpy(s, "toolong"); return 0; }': "buffer_overflow",
            'int main() { char s[2] = "ab"; return strlen(s); }': "no_terminator",
            "int main() { int m[2][2]; m[0][2] = 1; return 0; }": "out_of_bounds",
            "int f(int v[]) { return v[5]; } int main() { int a[5] = {0}; return f(a); }": "out_of_bounds",
        }
        for src, kind in cases.items():
            self.assertEqual(run(src)["error_kind"], kind, src)

    def test_array_parameters_share_the_array(self):
        r = run("void fill(int v[], int n) { int i; for (i = 0; i < n; i = i + 1) v[i] = i * i; }\n"
                "int main() { int a[4]; fill(a, 4); return a[3]; }")
        self.assertEqual(r["return_value"], 9)

    def test_scanf_strings_chars_and_numbers(self):
        src = 'int main() { char w[8]; int n; char c; double d; scanf("%s %d", w, &n); scanf(" %c %lf", &c, &d); printf("%s %d %c %.2f\\n", w, n, c, d); return 0; }'
        self.assertEqual(run(src, "Ann 19 Y 2.5")["stdout"], "Ann 19 Y 2.50\n")
        self.assertEqual(run(src, "Annabelle 19 Y 2.5")["error_kind"], "buffer_overflow")
        self.assertEqual(run('int main() { int n; char c; scanf("%d", &n); scanf("%c", &c); return c; }', "5\nY")["return_value"], 10)

    def test_printf_formats(self):
        self.assertEqual(c_format("%-5s", "ab"), "ab   ")
        self.assertEqual(c_format("%05.1f", 3.14159), "003.1")
        self.assertEqual(c_format("%x", -1), "ffffffff")
        self.assertEqual(c_format("%c", 65), "A")

    @unittest.skipUnless(backend.find_c_compiler()[0], "needs a C compiler")
    def test_native_build_matches_vm(self):
        for f in ("arrays.c", "strings.c", "datatypes.c"):
            r = analyze((WS / f).read_text())
            with tempfile.TemporaryDirectory() as d:
                b = backend.build(r["ir"], r["line_map"], d, "p")
                self.assertTrue(b["ok"], b["log"])
                nat = backend.run_executable(b)
            self.assertEqual(nat["stdout"], run_program(r["ir"], r["line_map"])["stdout"], f)

    @unittest.skipUnless(backend.find_c_compiler()[0], "needs a C compiler")
    def test_native_build_checks_bounds_too(self):
        r = analyze((WS / "bug_01_off_by_one.c").read_text())
        with tempfile.TemporaryDirectory() as d:
            nat = backend.run_executable(backend.build(r["ir"], r["line_map"], d, "p"))
        self.assertEqual(nat["exit_code"], 1)
        self.assertIn("out of bounds", nat["stderr"])


class BugSampleTests(unittest.TestCase):
    """Every bug_*.c sample in the workspace is found by its rule; most are confirmed by running."""
    EXPECTED = {
        "bug_01_off_by_one.c": ("off_by_one", True),
        "bug_02_last_element.c": ("array_out_of_bounds", True),
        "bug_03_sizeof_loop.c": ("sizeof_bytes", True),
        "bug_04_sizeof_parameter.c": ("sizeof_pointer", False),
        "bug_05_wrong_length.c": ("array_size_argument", True),
        "bug_06_uninitialised_count.c": ("array_read_before_write", True),
        "bug_07_no_room_for_null.c": ("no_room_for_terminator", True),
        "bug_08_strcpy_overflow.c": ("strcpy_overflow", True),
        "bug_09_strcat_overflow.c": ("strcpy_overflow", True),
        "bug_10_missing_terminator.c": ("missing_terminator", True),
        "bug_11_string_equals.c": ("string_compare_eq", True),
        "bug_12_strcmp_condition.c": ("strcmp_as_bool", False),
        "bug_13_integer_division.c": ("integer_division", True),
        "bug_14_float_equality.c": ("float_equality", True),
        "bug_15_lost_decimals.c": ("lost_decimals", True),
        "bug_16_char_overflow.c": ("char_overflow", True),
        "bug_17_scanf_char.c": ("scanf_char_newline", True),
        "bug_18_strncpy.c": ("strncpy_no_terminator", True),
        "bug_19_matrix_bounds.c": ("off_by_one", True),
        "bug_20_stray_semicolon.c": ("empty_body", True),
        "bug_21_switch_fallthrough.c": ("switch_fallthrough", True),
        "bug_22_counter_changed.c": ("counter_changed", False),
        "bug_23_impossible_condition.c": ("impossible_condition", False),
        "bug_24_constant_overflow.c": ("overflow", True),
    }

    def test_every_sample(self):
        self.assertEqual(sorted(p.name for p in WS.glob("bug_*.c")), sorted(self.EXPECTED))
        for name, (rule, confirmed) in self.EXPECTED.items():
            check = check_source((WS / name).read_text())
            self.assertTrue(check["ok"], name)
            found = {f["rule"]: f for f in detect(check, stdin="20 Y")["findings"]}
            self.assertIn(rule, found, name)
            self.assertEqual(found[rule]["confirmed"], confirmed, name)
            self.assertTrue(found[rule]["fix_hint"], name)

    def test_no_false_alarms_on_correct_programs(self):
        new_rules = {r for r, _ in self.EXPECTED.values()} | {"buffer_overflow", "uninit_array", "missing_terminator"}
        for f in ("arrays.c", "strings.c", "datatypes.c", "hello.c", "primes.c", "functions.c"):
            found = detect(check_source((WS / f).read_text()))["findings"]
            self.assertFalse([x for x in found if x["rule"] in new_rules], f)


class RefactorTests(unittest.TestCase):
    def test_while_to_for_keeps_the_type(self):
        src = "int main() {\n    double x = 0.5;\n    while (x < 4) {\n        print(1);\n        x = x * 2;\n    }\n    return 0;\n}\n"
        items = find_refactorings(src, check_source(src))["items"]
        w = [i for i in items if i["kind"] == "while_to_for"]
        self.assertTrue(w and "double x = 0.5" in w[0]["after"], items)
        self.assertTrue(all(i["verified"] for i in items))


if __name__ == "__main__":
    unittest.main()
