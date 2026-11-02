"""Show the compiler acting as the referee of the AI.

    python tools/referee_demo.py              # all four scenarios
    python tools/referee_demo.py fix docs     # only some of them

A real model only sometimes answers wrongly, so a live demo may never show a rejection. This
script makes the rejections repeatable: a scripted "model" gives typical WRONG answers first
(an empty fix, a rewrite that throws the student's code away, documentation that quietly
changes the code, an optimisation that changes the output, a bug fix that still crashes)
and a correct answer last.

Only the answers are scripted. Everything that judges them is the real code the IDE and the
CLI use with Ollama: AIFeatures._run(), each feature's verify(), check_source() (a full
compile) and the MiniC VM. Nothing here is special-cased for the demo.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_assistant.ai_features import AIFeatures, print_report      # noqa: E402

BOLD, RED, GREEN, DIM, END = ("\033[1m", "\033[31m", "\033[32m", "\033[2m", "\033[0m") if sys.stdout.isatty() else ("",) * 5


class ScriptedModel:
    """Stands in for Ollama: returns the next prepared answer and shows what the referee sent back."""
    def __init__(self, answers):
        self.answers = list(answers)
        self.n = 0

    def chat(self, system, user, schema=None, temperature=0.2, history=None):
        if self.n:
            print(f"{DIM}      referee → model: {user.splitlines()[0][:110]}{END}")
        self.n += 1
        label, answer = self.answers.pop(0)
        print(f"   model answer {self.n}: {label}")
        return dict(answer)


def read(name):
    return (ROOT / "workspace" / name).read_text(encoding="utf-8")


# ------------------------------------------------------------------ scenarios
BROKEN = read("broken.c")
BROKEN_FIXED = BROKEN.replace("int total = 0\n", "int total = 0;\n").replace("print(totl);", "print(total);")


def fix_answer(code, summary):
    return {"summary": summary, "fixed_code": code,
            "fixes": [{"line": 3, "error": "Expected ';'", "why": "Every statement ends with ;", "before": "int total = 0", "after": "int total = 0;"},
                      {"line": 5, "error": "Undeclared variable 'totl'", "why": "Misspelt name", "before": "print(totl);", "after": "print(total);"}]}


SCENARIOS = {}

SCENARIOS["fix"] = ("Fix errors on workspace/broken.c (missing ';' and misspelt 'totl')", "fix", BROKEN, [
    ("empty program (the model gave up)", fix_answer("", "I rewrote the program.")),
    ("a different, much shorter program that compiles", fix_answer("int main() {\n    return 5;\n}\n", "Simplified the program.")),
    ("the student's program with both mistakes corrected", fix_answer(BROKEN_FIXED, "Added the missing ';' and fixed the spelling of total.")),
])

DOC_SRC = """int square(int n) {
    return n * n;
}

int main() {
    int limit = 5;
    int i;
    for (i = 1; i <= limit; i = i + 1) {
        print(square(i));
    }
    return 0;
}
"""
DOC_SNEAKY = """// square: multiplies n by itself.
int square(int n) {
    return n * n;
}

// main: prints the squares of 1 to 5.
int main() {
    int limit = 6;          // how many squares to print
    int i;
    for (i = 1; i <= limit; i = i + 1) {
        print(square(i));   // print one square per line
    }
    return 0;
}
"""
SCENARIOS["docs"] = ("Document a program: the first answer adds comments but also quietly changes 5 to 6", "docs", DOC_SRC, [
    ("comments, plus a changed value on line 7", {"overview": "Prints squares.", "documented_code": DOC_SNEAKY}),
    ("comments only", {"overview": "Prints squares.", "documented_code": DOC_SNEAKY.replace("limit = 6;", "limit = 5;")}),
])

OPT_SRC = """int main() {
    int total = 0;
    int i;
    for (i = 0; i < 10; i = i + 1) {
        total = total + 2 * 3;
    }
    printf("total = %d\\n", total);
    return 0;
}
"""
OPT_WRONG = """int main() {
    printf("total = %d\\n", 66);
    return 0;
}
"""
OPT_RIGHT = OPT_SRC.replace("total = total + 2 * 3;", "total = total + 6;")
SCENARIOS["optimize"] = ("Optimise a loop: the first answer 'pre-computes' the result but gets it wrong (66, not 60)", "optimize", OPT_SRC, [
    ("replace the loop with a pre-computed (wrong) total", {"summary": "The loop always gives the same total, so print it directly.",
        "suggestions": [{"line": 4, "technique": "loop elimination", "impact": "high", "explanation": "Pre-compute the total."}], "optimized_code": OPT_WRONG}),
    ("fold the constant 2 * 3 into 6", {"summary": "2 * 3 is computed on every pass; fold it.",
        "suggestions": [{"line": 5, "technique": "constant folding", "impact": "low", "explanation": "2 * 3 is always 6."}], "optimized_code": OPT_RIGHT}),
])

BUGS = read("bugs.c")
BUGS_HALF = BUGS.replace("int big = 50000;", "int big = 40000;").replace("    sum == 100;\n", "")
BUGS_FIXED = (BUGS_HALF.replace("    return total / count;", "    if (count == 0) { return 0; }\n    return total / count;")
              .replace("    return n * factorial(n - 1);", "    if (n <= 1) { return 1; }\n    return n * factorial(n - 1);")
              .replace("    if (x < 0) { return 0 - 1; }\n}", "    if (x < 0) { return 0 - 1; }\n    return 0;\n}"))
SCENARIOS["bugs"] = ("Find bugs on workspace/bugs.c: the first fix misses the division by zero", "bugs", BUGS, [
    ("fixes the overflow but not average()", {"summary": "Fixed the overflow.", "findings": [], "fixed_code": BUGS_HALF}),
    ("guards the division, adds the base case and the missing return", {"summary": "Fixed all confirmed bugs.", "findings": [], "fixed_code": BUGS_FIXED}),
])


def run(key):
    title, feature, src, answers = SCENARIOS[key]
    print(f"\n{BOLD}{'=' * 72}\n{title}\n{'=' * 72}{END}")
    ai = AIFeatures(client=ScriptedModel(answers))
    report = {"fix": ai.suggest_fixes, "docs": ai.document_code, "optimize": ai.suggest_optimizations,
              "bugs": lambda s: ai.detect_bugs(s, full_fix=True)}[feature](src)
    v = report["verification"]
    print()
    for e in v["log"]:
        mark = f"{GREEN}ACCEPTED{END}" if e["passed"] else f"{RED}REJECTED{END}"
        print(f"   attempt {e['attempt']}: {mark}  {'; '.join(e['notes'])}")
    shown = "verified" if v["passed"] else "NOT verified"
    print(f"\n   The student is shown only the final answer, marked {BOLD}{shown}{END}.")
    return v["passed"] and any(not e["passed"] for e in v["log"])


def main(argv):
    keys = argv or list(SCENARIOS)
    print("Referee demo: the wrong answers are scripted on purpose; the checks are the real ones.")
    ok = all(run(k) for k in keys)
    print(f"\n{BOLD}{'Every wrong answer was rejected and every correct one accepted.' if ok else 'Unexpected result: see above.'}{END}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
