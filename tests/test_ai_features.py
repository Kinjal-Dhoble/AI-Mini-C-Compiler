"""Tests for the LLM integration. A fake client stands in for Ollama, so these run offline
and check our side of the contract: prompts, skill loading, verification and retries."""
import unittest
from pathlib import Path
from ai_assistant.ai_features import AIFeatures
from ai_assistant.context_builder import build_context, check_source, compiler_facts
from ai_assistant.ir_interpreter import run_main
from ai_assistant.llm_client import parse_json, strip_thinking
from ai_assistant.skill_loader import load_skill


class FakeClient:
    """Returns queued answers in order and records every call."""
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def chat(self, system, user, schema=None, temperature=0.2, history=None):
        self.calls.append({"system": system, "user": user, "history": list(history or [])})
        return dict(self.answers.pop(0))


GOOD = "int main() {\n    int x = 10 + 20 * 3;\n    if (x >= 50) { x = x - 1; }\n    return x;\n}"
BROKEN = "int main() {\n    int x = 5\n    return x;\n}"


class SkillTests(unittest.TestCase):
    def test_skills_load_with_shared_rules(self):
        for name in ("fix_suggestions", "optimization", "beginner_docs"):
            s = load_skill(name)
            self.assertIn("NOT supported", s.system_prompt)   # shared language file included
            self.assertIn("JSON", s.system_prompt)
            self.assertGreaterEqual(s.max_retries, 1)


class ContextTests(unittest.TestCase):
    def test_context_contains_errors_and_numbered_source(self):
        ctx = build_context(BROKEN)
        self.assertIn("FAILED", ctx)
        self.assertIn("  2 |     int x = 5", ctx)
        self.assertIn("Expected ';'", ctx)

    def test_constant_folding_fact(self):
        facts = compiler_facts(check_source("int main() { int y = 2 * 3; return y; }")["ast"])
        self.assertTrue(any("constant expression" in f for f in facts))

    def test_unreachable_fact(self):
        facts = compiler_facts(check_source("int main() { int y = 1; return y; y = 2; }")["ast"])
        self.assertTrue(any("unreachable" in f for f in facts))


class InterpreterTests(unittest.TestCase):
    def test_runs_sample(self):
        self.assertEqual(run_main(check_source(GOOD)["ir"]), 69)

    def test_runs_loop(self):
        src = "int main() { int s = 0; int i; for (i = 0; i < 5; i = i + 1) { s = s + i; } return s; }"
        self.assertEqual(run_main(check_source(src)["ir"]), 10)

    def test_c_division_truncates(self):
        self.assertEqual(run_main(check_source("int main() { return -7 / 2; }")["ir"]), -3)


class FixTests(unittest.TestCase):
    def test_verified_fix(self):
        fake = FakeClient({"summary": "missing ;", "fixes": [], "fixed_code": BROKEN.replace("= 5", "= 5;")})
        rep = AIFeatures(fake).suggest_fixes(BROKEN)
        self.assertTrue(rep["verification"]["passed"])
        self.assertEqual(len(fake.calls), 1)

    def test_retry_when_fix_still_broken(self):
        fake = FakeClient({"summary": "", "fixes": [], "fixed_code": BROKEN},
                          {"summary": "", "fixes": [], "fixed_code": BROKEN.replace("= 5", "= 5;")})
        rep = AIFeatures(fake).suggest_fixes(BROKEN)
        self.assertTrue(rep["verification"]["passed"])
        self.assertEqual(rep["verification"]["attempts"], 2)
        self.assertIn("Verification failed", fake.calls[1]["user"])
        self.assertEqual(len(fake.calls[1]["history"]), 2)

    def test_skips_when_code_compiles(self):
        self.assertIn("skipped", AIFeatures(FakeClient()).suggest_fixes(GOOD))


class OptimizeTests(unittest.TestCase):
    def test_behaviour_change_is_rejected(self):
        wrong = GOOD.replace("10 + 20 * 3", "90")            # main would return 89, not 69
        right = GOOD.replace("10 + 20 * 3", "70")
        fake = FakeClient({"summary": "", "suggestions": [], "optimized_code": wrong},
                          {"summary": "", "suggestions": [], "optimized_code": right})
        rep = AIFeatures(fake).suggest_optimizations(GOOD)
        self.assertTrue(rep["verification"]["passed"])
        self.assertIn("returns 69", fake.calls[1]["user"])

    def test_prompt_includes_ir_and_facts(self):
        fake = FakeClient({"summary": "", "suggestions": [], "optimized_code": GOOD})
        AIFeatures(fake).suggest_optimizations(GOOD)
        self.assertIn("Intermediate code", fake.calls[0]["user"])
        self.assertIn("can be folded", fake.calls[0]["user"])


class DocsTests(unittest.TestCase):
    def test_comments_only_passes(self):
        doc = "/* Header */\n" + GOOD.replace("    return x;", "    // give back x\n    return x;")
        rep = AIFeatures(FakeClient({"overview": "o", "documented_code": doc})).document_code(GOOD)
        self.assertTrue(rep["verification"]["passed"])

    def test_code_change_is_rejected(self):
        changed = "// hi\n" + GOOD.replace("x - 1", "x - 2")
        fake = FakeClient({"overview": "o", "documented_code": changed},
                          {"overview": "o", "documented_code": changed},
                          {"overview": "o", "documented_code": changed})
        rep = AIFeatures(fake).document_code(GOOD)
        self.assertFalse(rep["verification"]["passed"])
        self.assertIn("'1'", rep["verification"]["notes"][0])

    def test_line_numbers_are_stripped(self):
        numbered = "\n".join(f"{i:>3} | {l}" for i, l in enumerate(GOOD.splitlines(), 1))
        rep = AIFeatures(FakeClient({"overview": "o", "documented_code": numbered})).document_code(GOOD)
        self.assertTrue(rep["verification"]["passed"])


class DocsAnyCodeTests(unittest.TestCase):
    def test_documents_code_that_failed_to_compile(self):
        doc = BROKEN.replace("    int x = 5", "    // ERROR: missing ;\n    int x = 5")
        fake = FakeClient({"overview": "o", "documented_code": doc})
        rep = AIFeatures(fake).document_code(BROKEN)
        self.assertTrue(rep["verification"]["passed"])
        self.assertTrue(fake.calls[0]["user"].startswith("MODE: failed"))

    def test_documents_code_with_lexical_error(self):
        bad = "int main() { int x = 5 @ 2; return x; }"
        rep = AIFeatures(FakeClient({"overview": "o", "documented_code": "// bad char\n" + bad})).document_code(bad)
        self.assertTrue(rep["verification"]["passed"])

    def test_corrected_mode_includes_original_and_errors(self):
        fixed = BROKEN.replace("= 5", "= 5;")
        fake = FakeClient({"overview": "o", "documented_code": fixed.replace("= 5;", "= 5; // FIXED: added ;")})
        rep = AIFeatures(fake).document_code(fixed, corrected_from=BROKEN)
        self.assertTrue(rep["verification"]["passed"])
        user = fake.calls[0]["user"]
        self.assertTrue(user.startswith("MODE: corrected"))
        self.assertIn("Expected ';'", user)
        self.assertIn("int x = 5\n", user)       # original shown


class ClientHelperTests(unittest.TestCase):
    def test_parse_json_with_fences_and_thinking(self):
        raw = strip_thinking('<think>hmm</think>```json\n{"a": 1}\n```')
        self.assertEqual(parse_json(raw), {"a": 1})


if __name__ == "__main__":
    unittest.main()


class RefereeTests(unittest.TestCase):
    def test_log_records_every_rejection_and_the_feedback(self):
        good = "int main() {\n    int total = 0;\n    total = total + 5;\n    print(total);\n    return total;\n}"
        broken = good.replace("int total = 0;", "int total = 0")
        client = FakeClient({"summary": "", "fixes": [], "fixed_code": ""},
                            {"summary": "", "fixes": [], "fixed_code": good})
        rep = AIFeatures(client=client).suggest_fixes(broken)
        log = rep["verification"]["log"]
        self.assertEqual([e["passed"] for e in log], [False, True])
        self.assertIn("empty", log[0]["notes"][0])
        self.assertIn("Verification failed", log[0]["feedback"])
        self.assertEqual(client.calls[1]["user"], log[0]["feedback"])   # the rejection reason is what the model is told

    def test_referee_demo_scenarios(self):
        import importlib.util, io, contextlib
        spec = importlib.util.spec_from_file_location("referee_demo", Path(__file__).resolve().parent.parent / "tools" / "referee_demo.py")
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mod.main([]), 0)


class RefereeChallengeTests(unittest.TestCase):
    def test_challenge_rejects_wrong_answers_built_from_the_program(self):
        from ai_assistant.referee import challenge
        src = (Path(__file__).resolve().parent.parent / "workspace" / "functions.c").read_text()
        cases = challenge(src)["cases"]
        wrong = [c for c in cases if c["expect"] == "reject"]
        self.assertGreaterEqual(len(wrong), 4)
        self.assertTrue(all(not c["passed"] for c in wrong), [c["label"] for c in wrong if c["passed"]])
        self.assertTrue(all(c["passed"] for c in cases if c["expect"] == "accept"))

    def test_broken_program_gets_fix_cases_and_compiler_repair_passes(self):
        from ai_assistant.referee import challenge
        src = (Path(__file__).resolve().parent.parent / "workspace" / "broken.c").read_text()
        cases = {c["label"]: c for c in challenge(src)["cases"]}
        self.assertFalse(cases["Empty program"]["passed"])
        self.assertTrue(cases["Repair using the compiler's own hints"]["passed"])

    def test_judge_and_crashing_program_behaviour(self):
        from ai_assistant.referee import judge
        src = "int main() {\n    print(1);\n    print(10 / 0);\n    return 0;\n}"
        same = judge(src, "optimize", src.replace("print(1);", "print(0 + 1);"))
        changed = judge(src, "optimize", src.replace("print(1);", "print(2);"))
        self.assertTrue(same["passed"])
        self.assertFalse(changed["passed"])           # a program that crashes is still compared
        self.assertFalse(judge(src, "docs", "// hi\n" + src.replace("1)", "2)"))["passed"])


class QuickBugReviewTests(unittest.TestCase):
    SRC = "int average(int t, int c) {\n    return t / c;\n}\nint main() {\n    print(average(10, 0));\n    return 0;\n}\n"

    def test_quick_review_asks_for_no_program_and_shows_findings_first(self):
        events = []
        client = FakeClient({"summary": "one bug", "findings": [{"id": "B1", "verdict": "real_bug", "explanation": "c is 0."}], "fixed_code": ""})
        rep = AIFeatures(client=client, on_event=events.append).detect_bugs(self.SRC)
        self.assertIn("Do NOT write the whole program", client.calls[0]["user"])
        self.assertEqual(events[0]["type"], "partial")                 # compiler findings come before the model
        self.assertTrue(any(f["confirmed"] for f in events[0]["report"]["findings"]))
        self.assertEqual(len(client.calls), 1)                         # nothing to re-check, so no retries
        self.assertTrue(rep["verification"]["passed"])

    def test_full_fix_is_compiled_and_run(self):
        still_broken = {"summary": "", "findings": [], "fixed_code": self.SRC}
        fixed = {"summary": "", "findings": [], "fixed_code": self.SRC.replace("    return t / c;", "    if (c == 0) { return 0; }\n    return t / c;")}
        client = FakeClient(still_broken, fixed)
        rep = AIFeatures(client=client).detect_bugs(self.SRC, full_fix=True)
        self.assertIn("ONLY the corrected program", client.calls[0]["user"])
        self.assertEqual([e["passed"] for e in rep["verification"]["log"]], [False, True])


class RefereeDemoFileTests(unittest.TestCase):
    def test_every_case_in_referee_demo_matches_its_expectation(self):
        from ai_assistant.referee import challenge
        src = (Path(__file__).resolve().parent.parent / "workspace" / "referee_demo.c").read_text()
        cases = challenge(src)["cases"]
        self.assertGreaterEqual(len(cases), 9)
        for c in cases:
            self.assertEqual(c["passed"], c["expect"] == "accept", c["label"])
