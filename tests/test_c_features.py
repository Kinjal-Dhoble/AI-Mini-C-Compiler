"""The C features added in version 10 (++, +=, !, break, continue, do-while, switch, int a, b;, const,
#define), the logic-error rules, and the robustness guarantees (nothing crashes the compiler)."""
import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from compiler.pipeline import analyze
from compiler.semantic import const_value
from compiler.parser import BinOpNode, NumNode
from ai_assistant.ir_interpreter import run_program
from ai_assistant.context_builder import check_source
from ai_assistant.bug_detector import detect


def run(src, frontend="hand"):
    r = analyze(src, frontend=frontend)
    assert r["success"], [e["message"] for e in r["errors"]]
    return run_program(r["ir"], r["line_map"])


def errors(src, frontend="hand"):
    return " | ".join(e["message"] for e in analyze(src, frontend=frontend)["errors"])


class Language(unittest.TestCase):
    def both(self, src, expected):
        for fe in ("hand", "yacc"):
            with self.subTest(frontend=fe):
                out = run(src, fe)
                self.assertIsNone(out["error"], out["error"])
                self.assertEqual(out["stdout"], expected)

    def test_increment_and_compound_assignment(self):
        self.both("""int main() { int a = 5, b = 3; int arr[3] = {1, 2, 3};
            a++; ++a; b--; a += 10; a -= 2; a *= 3; a /= 4; b %= 2; arr[1] += 40; arr[2]++;
            printf("%d %d %d %d\\n", a, b, arr[1], arr[2]); return 0; }""", "11 0 42 4\n")

    def test_not_operator(self):
        self.both('int main() { int x = 6; printf("%d %d %d\\n", !x, !!x, !(x > 9)); return 0; }', "0 1 1\n")

    def test_break_continue_do_while(self):
        self.both("""int main() { int s = 0; int i;
            for (i = 0; i < 10; i++) { if (i == 7) break; if (i % 2) continue; s += i; }
            int k = 0; do { k++; } while (k < 3);
            int w = 0; while (1) { w++; if (w == 4) break; }
            printf("%d %d %d %d\\n", s, i, k, w); return 0; }""", "12 7 3 4\n")

    def test_switch_with_fallthrough_and_default(self):
        src = """int name(int d) { switch (d) { case 1: printf("one "); break;
            case 2: case 3: printf("two-or-three "); break; default: printf("other "); } return 0; }
            int main() { name(1); name(2); name(3); name(9);
            int n = 0; switch (2) { case 2: n += 1; case 3: n += 10; break; case 4: n += 100; }
            printf("%d\\n", n); return 0; }"""
        self.both(src, "one two-or-three two-or-three other 11\n")

    def test_define_const_and_comma_declarations(self):
        self.both("""#define SIZE 4
            #define TWICE (SIZE * 2)
            const int LIMIT = 3;
            int main() { int a[SIZE] = {1, 2, 3, 4}, total = 0, i;
            for (i = 0; i < SIZE; i++) total += a[i];
            printf("%d %d %d\\n", total, TWICE, LIMIT); return 0; }""", "10 8 3\n")

    def test_globals_start_at_zero(self):
        self.both('int count; int main() { count++; printf("%d\\n", count); return 0; }', "1\n")

    def test_semantic_errors_for_new_features(self):
        cases = {
            "int main() { break; return 0; }": "break",
            "int main() { continue; return 0; }": "continue",
            "int main() { const int x = 1; x = 2; return 0; }": "const",
            "int main() { const int x = 1; x++; return 0; }": "const",
            "int main() { switch (1) { case 1: break; case 1: break; } return 0; }": "1",
            "int main() { int v = 2; switch (1) { case v: break; } return 0; }": "constant",
            "#define SQ(x) ((x)*(x))\nint main() { return 0; }": "Function-like macro",
        }
        for src, word in cases.items():
            for fe in ("hand", "yacc"):
                with self.subTest(src=src, frontend=fe):
                    msg = errors(src, fe)
                    self.assertTrue(msg, "expected an error")
                    self.assertIn(word.lower(), msg.lower())


class LogicRules(unittest.TestCase):
    def rules(self, path):
        check = check_source((ROOT / "workspace" / path).read_text(encoding="utf-8"))
        return {f["rule"]: f["confirmed"] for f in detect(check)["findings"]}

    def test_new_bug_samples(self):
        self.assertTrue(self.rules("bug_20_stray_semicolon.c").get("empty_body"))
        self.assertTrue(self.rules("bug_21_switch_fallthrough.c").get("switch_fallthrough"))
        self.assertIn("counter_changed", self.rules("bug_22_counter_changed.c"))
        self.assertIn("impossible_condition", self.rules("bug_23_impossible_condition.c"))
        self.assertTrue(self.rules("bug_24_constant_overflow.c").get("overflow"))

    def test_rules_do_not_fire_on_correct_programs(self):
        logic = {"empty_body", "switch_fallthrough", "self_comparison", "impossible_condition", "counter_changed",
                 "constant_overflow", "endless_loop"}
        for p in ("c_features.c", "primes.c", "fibonacci.c", "functions.c", "arrays.c", "strings.c"):
            with self.subTest(program=p):
                r = analyze((ROOT / "workspace" / p).read_text(encoding="utf-8"))
                self.assertFalse([s for s in r["static"] if s.get("rule") in logic])

    def test_switch_that_returns_everywhere_needs_no_final_return(self):
        check = check_source("""int grade(int m) { switch (m / 10) { case 10: case 9: return 1; default: return 3; } }
            int main() { printf("%d\\n", grade(95)); return 0; }""")
        self.assertTrue(check["ok"])
        self.assertNotIn("missing_return", {f["rule"] for f in detect(check)["findings"]})


class Robustness(unittest.TestCase):
    def assert_clean(self, src):
        for fe in ("hand", "yacc"):
            r = analyze(src, frontend=fe)                  # must not raise
            for e in r["errors"]:
                self.assertIsInstance(e["line"], int)
                self.assertTrue(e["message"])
            if r["success"]:
                run_program(r["ir"], r["line_map"], max_steps=10_000)
            return r

    def test_extreme_inputs_never_crash(self):
        from tools.fuzz import extremes
        for src in extremes():
            with self.subTest(src=src[:60]):
                self.assert_clean(src)

    def test_deep_nesting_is_an_error_not_a_crash(self):
        src = "int main() { " + "{" * 1000 + "}" * 1000 + " return 0; }"
        for fe in ("hand", "yacc"):
            self.assertIn("nested too deeply", errors(src, fe))

    def test_huge_array_is_rejected(self):
        self.assertIn("more than", errors("int main() { int a[2147483647]; return 0; }"))

    def test_constant_folding_with_zero(self):
        self.assertEqual(const_value(BinOpNode(left=NumNode(value=5), op="+", right=NumNode(value=0))), 5)
        self.assertIsNone(const_value(BinOpNode(left=NumNode(value=5), op="/", right=NumNode(value=0))))
        self.assertEqual(const_value(BinOpNode(left=NumNode(value=-7), op="/", right=NumNode(value=2))), -3)
        self.assertEqual(const_value(BinOpNode(left=NumNode(value=-7), op="%", right=NumNode(value=2))), -1)

    def test_mutated_programs_smoke(self):
        from tools.fuzz import crash_test
        res = crash_test(150, random.Random(11), None, False)
        self.assertEqual(res["failures"], [], res["failures"][:1])


if __name__ == "__main__":
    unittest.main()


class DemoFiles(unittest.TestCase):
    """The numbered demo programs (1_compile_*, 2_warn_*, 3_bugs_*, 4_crash_*) show what DEMO_GUIDE.md says."""
    WS = ROOT / "workspace"

    def test_compile_demos_report_every_error(self):
        expected = {"1_compile_lexical.c": 5, "1_compile_syntax.c": 4, "1_compile_semantic.c": 8, "1_compile_types.c": 10}
        for name, count in expected.items():
            for fe in ("hand", "yacc"):
                with self.subTest(file=name, frontend=fe):
                    r = analyze((self.WS / name).read_text(encoding="utf-8"), frontend=fe)
                    self.assertFalse(r["success"])
                    self.assertGreaterEqual(len(r["errors"]), count)

    def test_warning_demos_compile_run_and_warn(self):
        rules = {"2_warn_logic_mistakes.c": {"endless_loop", "impossible_condition", "self_comparison", "counter_changed",
                                             "empty_body", "switch_fallthrough", "constant_overflow"},
                 "2_warn_unused_and_dead_code.c": {"constant_condition", "unreachable", "unused_function"}}
        for name, want in rules.items():
            with self.subTest(file=name):
                r = analyze((self.WS / name).read_text(encoding="utf-8"))
                self.assertTrue(r["success"])
                self.assertTrue(want <= {s.get("rule") for s in r["static"]}, want - {s.get("rule") for s in r["static"]})
                self.assertIsNone(run_program(r["ir"], r["line_map"])["error"])

    def test_bug_demos_are_confirmed_by_running(self):
        confirmed = {"3_bugs_numbers.c": {"integer_division", "lost_decimals", "char_overflow", "overflow", "float_equality"},
                     "3_bugs_strings.c": {"string_compare_eq", "strncpy_no_terminator", "strcpy_overflow"},
                     "3_bugs_arrays.c": {"sizeof_pointer", "off_by_one"},
                     "3_bugs_control_flow.c": {"switch_fallthrough", "counter_changed", "empty_body"}}
        for name, want in confirmed.items():
            with self.subTest(file=name):
                found = detect(check_source((self.WS / name).read_text(encoding="utf-8")))["findings"]
                self.assertTrue(want <= {f["rule"] for f in found if f["confirmed"]})

    def test_crash_demo_menu(self):
        r = analyze((self.WS / "4_crash_runtime_errors.c").read_text(encoding="utf-8"))
        kinds = {1: "out_of_bounds", 2: "div_zero", 3: "uninit", 4: "stack_overflow", 5: "buffer_overflow",
                 6: "input", 7: "step_limit", 8: None}
        for choice, kind in kinds.items():
            with self.subTest(choice=choice):
                self.assertEqual(run_program(r["ir"], r["line_map"], stdin=str(choice), max_steps=100_000)["error_kind"], kind)
