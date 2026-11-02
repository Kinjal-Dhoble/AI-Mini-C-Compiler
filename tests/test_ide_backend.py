"""Tests for print(), the IR VM, the C back-end and the IDE's compile API."""
import shutil
import tempfile
import unittest
from compiler.lexer import Lexer
from compiler.parser import Parser
from compiler.code_gen import CodeGenerator
from compiler import backend
from ai_assistant.ir_interpreter import run_program
from ide.server import compile_source

PROGRAM = """int g = 3;
int main() {
    int i; int s = 0;
    for (i = 1; i <= 5; i = i + 1) { s = s + i * g; print(s); }
    int big = 2147483647;
    big = big + 1;
    print(big);
    print(-7 / 2);
    print(-7 % 2);
    return s;
}"""
EXPECTED = [3, 9, 18, 30, 45, -2147483648, -3, -1]


def ir_of(src):
    cg = CodeGenerator()
    ir = cg.generate(Parser(Lexer(src).tokenize()).parse())
    return ir, cg.line_map


class PrintAndVmTests(unittest.TestCase):
    def test_print_parses_and_generates(self):
        ir, lm = ir_of("int main() { print(1 + 2); return 0; }")
        self.assertTrue(any(i.startswith("PRINT ") for i in ir))
        self.assertEqual(len(ir), len(lm))

    def test_vm_semantics(self):
        ir, lm = ir_of(PROGRAM)
        r = run_program(ir, lm, trace=True)
        self.assertEqual(r["output"], EXPECTED)
        self.assertEqual(r["return_value"], 45)
        self.assertTrue(all(e["line"] for e in r["trace"]))

    def test_vm_reports_division_by_zero_and_infinite_loop(self):
        ir, _ = ir_of("int main() { int z = 0; return 5 / z; }")
        self.assertIn("division by zero", run_program(ir)["error"])
        ir, _ = ir_of("int main() { int x = 1; while (x) { x = 1; } return 0; }")
        self.assertIn("infinite loop", run_program(ir, max_steps=1000)["error"])


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.out = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.out, ignore_errors=True)

    @unittest.skipUnless(backend.find_c_compiler()[0], "no C compiler installed")
    def test_native_build_matches_vm(self):
        ir, lm = ir_of(PROGRAM)
        b = backend.build(ir, lm, self.out, "prog")
        self.assertTrue(b["ok"], b["log"])
        r = backend.run_executable(b)
        self.assertEqual([int(x) for x in r["stdout"].split()], EXPECTED)
        self.assertEqual(r["exit_code"], 45)

    def test_vm_build_runs_without_c_compiler(self):
        import os
        old = os.environ.get("PATH", "")
        os.environ["PATH"] = ""
        try:
            ir, lm = ir_of(PROGRAM)
            b = backend.build(ir, lm, self.out, "prog")
        finally:
            os.environ["PATH"] = old
        self.assertEqual(b["mode"], "vm")
        r = backend.run_executable(b)
        self.assertEqual([int(x) for x in r["stdout"].split()], EXPECTED)

    def test_c_has_one_line_per_instruction(self):
        ir, lm = ir_of(PROGRAM)
        c = backend.ir_to_c(ir, lm)
        self.assertEqual(sum("/* [" in l for l in c.splitlines()), len([i for i in ir if not i.startswith(("FUNC", "END_FUNC"))]))


class IdeApiTests(unittest.TestCase):
    def test_compile_success(self):
        r = compile_source(PROGRAM)
        self.assertTrue(r["success"])
        self.assertEqual(r["ast"]["type"], "Program")
        self.assertEqual(len(r["ir"]), len(r["line_map"]))

    def test_compile_syntax_error_has_location_and_explanation(self):
        r = compile_source("int main() {\n    int x = 5\n    return x;\n}")
        self.assertFalse(r["success"])
        d = r["diagnostics"][0]
        self.assertEqual((d["severity"], d["line"]), ("error", 3))
        self.assertTrue(d["explanation"])


if __name__ == "__main__":
    unittest.main()
