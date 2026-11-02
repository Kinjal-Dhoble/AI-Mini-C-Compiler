"""The compiler as referee, on demand.

judge(source, feature, candidate)  runs ONE proposed AI answer through the real check for that
                                   feature (the same AIFeatures code path used with Ollama, with
                                   retries switched off) and returns the verdict.
challenge(source)                  builds typical wrong answers FROM THE GIVEN PROGRAM (a changed
                                   constant, an off-by-one bound, a deleted statement,
                                   documentation that sneaks in a code change, an empty or
                                   gutted fix ...) plus answers that should pass, and judges each.

Nothing here decides whether an answer is right: the verdict always comes from verify() in
ai_features.py, i.e. a full compile, a token comparison, or a run on the MiniC VM.
"""
import dataclasses
import re
from difflib import get_close_matches

from compiler.lexer import Lexer, LexErrors
from ai_assistant.ai_features import AIFeatures
from ai_assistant.context_builder import check_source
from ai_assistant.ir_interpreter import run_program

FEATURES = {"fix": ("suggest_fixes", "fixed_code"), "optimize": ("suggest_optimizations", "optimized_code"),
            "docs": ("document_code", "documented_code"), "bugs": ("detect_bugs", "fixed_code")}


class _OneAnswer:
    """Plays the model: returns the candidate answer once."""
    def __init__(self, answer):
        self.answer = answer

    def chat(self, system, user, schema=None, temperature=0.2, history=None):
        return dict(self.answer)


def judge(source, feature, candidate, stdin=None):
    """Verdict for one candidate answer: {passed, notes} or {skipped}."""
    method, key = FEATURES[feature]
    answer = {"summary": "", "overview": "", "fixes": [], "suggestions": [], "findings": [], key: candidate}
    ai = AIFeatures(client=_OneAnswer(answer), stdin=stdin)
    real_skill = ai.skill
    ai.skill = lambda name: dataclasses.replace(real_skill(name), max_retries=0)   # judge exactly one answer
    report = getattr(ai, method)(source)
    if "skipped" in report:
        return {"skipped": report["skipped"]}
    v = report.get("verification")
    if v is None:                                   # bugs without a usable model answer
        return {"skipped": report.get("ai_error", "no verdict")}
    return {"passed": v["passed"], "notes": v["notes"]}


# ------------------------------------------------------------------ building wrong answers
def _tokens(source):
    try:
        return Lexer(source).tokenize()
    except LexErrors as e:
        return e.tokens


def _replace_token(source, tok, new):
    lines = source.split("\n")
    i = tok.line - 1
    line = lines[i]
    lines[i] = line[:tok.column - 1] + new + line[tok.column - 1 + len(tok.value):]
    return "\n".join(lines)


def _in_function(tokens):
    """Tokens inside braces (function bodies), with their brace depth."""
    depth, out = 0, []
    for t in tokens:
        if t.value == "{":
            depth += 1
        elif t.value == "}":
            depth -= 1
        elif depth > 0:
            out.append(t)
    return out


def change_constant(source):
    made = [(_replace_token(source, t, str(int(t.value) + 1)), f"line {t.line}: {t.value} became {int(t.value) + 1}")
            for t in _in_function(_tokens(source)) if t.kind == "NUMBER"]
    return _pick(source, made[:8])


def _changes_behaviour(source, candidate):
    """Does the mutated program visibly behave differently? (so a 'wrong answer' really is wrong)"""
    from ai_assistant.ir_interpreter import observed
    a, b = check_source(source), check_source(candidate)
    if not (a["ok"] and b["ok"]):
        return True
    return observed(a["ir"], max_steps=200_000) != observed(b["ir"], max_steps=200_000)


def _pick(source, made):
    """The first mutation that really changes what the program does (else the first one)."""
    made = [m for m in made if m]
    return next((m for m in made if _changes_behaviour(source, m[0])), made[0] if made else None)


def off_by_one(source):
    swap = {"<": "<=", "<=": "<", ">": ">=", ">=": ">"}
    lines = source.split("\n")
    made = [(_replace_token(source, t, swap[t.value]), f"line {t.line}: loop test {t.value} became {swap[t.value]}")
            for t in _in_function(_tokens(source)) if t.value in swap and re.search(r"\b(for|while)\b", lines[t.line - 1])]
    return _pick(source, made[:6])


def swap_operator(source):
    swap = {"+": "-", "-": "+", "*": "+", "<": ">", ">": "<", "==": "!="}
    for t in _in_function(_tokens(source)):
        if t.kind == "OP" and t.value in swap:
            return _replace_token(source, t, swap[t.value]), f"line {t.line}: {t.value} became {swap[t.value]}"
    return None


def delete_statement(source):
    lines, depth, options = source.split("\n"), 0, []
    for i, line in enumerate(lines):
        s = line.strip()
        if depth > 0 and s.endswith(";") and "{" not in s and "}" not in s and not re.match(r"(int|char|short|long|float|double|void|return)\b", s) and not s.startswith("//"):
            options.append(i)
        depth += line.count("{") - line.count("}")
    options.sort(key=lambda i: not re.match(r"(printf|print)\s*\(", lines[i].strip()))   # output lines first
    made = [("\n".join(lines[:i] + lines[i + 1:]), f"line {i + 1} removed: {lines[i].strip()}") for i in options[:6]]
    return _pick(source, made)


def rename_variable(source):
    toks = _tokens(source)
    for a, b in zip(toks, toks[1:]):
        if a.value in ("int", "char", "short", "long", "float", "double") and b.kind == "IDENTIFIER":
            nxt = toks[toks.index(b) + 1]
            if nxt.value in (";", "=") and b.value != "main":
                new, out = b.value + "_value", source
                for t in reversed([t for t in toks if t.kind == "IDENTIFIER" and t.value == b.value]):
                    out = _replace_token(out, t, new)     # only real uses: never inside strings or comments
                return out, f"'{b.value}' renamed to '{new}' everywhere"
    return None


def fold_constants(source):
    """NUMBER op NUMBER in real code (never in comments or strings), folded when nothing around it
    binds tighter, e.g. `int scale = 2 * 3;` -> `int scale = 6;`."""
    toks = [t for t in _tokens(source) if t.kind != "EOF"]
    for i in range(1, len(toks) - 2):
        a, op, b = toks[i], toks[i + 1], toks[i + 2]
        if a.kind == "NUMBER" and op.value in ("*", "+") and b.kind == "NUMBER" and a.line == b.line:
            before, after = toks[i - 1].value, toks[i + 3].value if i + 3 < len(toks) else ";"
            if before in ("*", "/", "%", "-") or after in ("*", "/", "%"):
                continue
            val = int(a.value) * int(b.value) if op.value == "*" else int(a.value) + int(b.value)
            lines = source.split("\n")
            L = lines[a.line - 1]
            end = b.column - 1 + len(b.value)
            lines[a.line - 1] = L[:a.column - 1] + str(val) + L[end:]
            return "\n".join(lines), f"line {a.line}: {L[a.column - 1:end]} folded to {val}"
    return None


def add_comments(source):
    out = ["// Documented by the AI assistant."]
    for line in source.split("\n"):
        m = re.match(r"\s*(int|void)\s+(\w+)\s*\([^;]*$", line)
        if m:
            out.append(f"// {m.group(2)}(): explained for beginners.")
        out.append(line)
    return "\n".join(out)


def compiler_repair(source):
    """What the compiler itself knows how to repair: missing ; or ) and misspelt names."""
    check = check_source(source)
    lines = source.split("\n")
    edits = []
    declared = sorted({b.value for a, b in zip(check["tokens"], check["tokens"][1:]) if a.value in ("int", "void", "char", "short", "long", "float", "double") and b.kind == "IDENTIFIER"})
    for e in check["errors"]:
        if e.get("recovery_kind") == "insert" and e.get("fix_line"):
            tok = e.get("expected", "").strip("'")
            edits.append((e["fix_line"], e["fix_column"], tok, ""))
        m = re.match(r"Undeclared variable '(\w+)'", e.get("message", ""))
        if m:
            near = get_close_matches(m.group(1), declared, n=1)
            if near:
                edits.append((e["line"], e["column"], near[0], m.group(1)))
    if not edits:
        return None
    for line, col, text, old in sorted(edits, reverse=True):
        L = lines[line - 1]
        lines[line - 1] = L[:col - 1] + text + L[col - 1 + len(old):]
    return "\n".join(lines), "inserted the tokens the parser assumed, corrected misspelt names"


# ------------------------------------------------------------------ the challenge
def challenge(source, stdin=None):
    check = check_source(source)
    cases = []

    def add(feature, label, made, expect):
        if made:
            code, what = made
            cases.append({"feature": feature, "label": label, "change": what, "expect": expect, "code": code})

    if check["ok"]:
        add("docs", "Comments only", (add_comments(source), "a comment above each function"), "accept")
        for label, fn in [("Comments, plus a changed constant", change_constant), ("Comments, plus a swapped operator", swap_operator),
                          ("Comments, plus a deleted statement", delete_statement)]:
            m = fn(source)
            add("docs", label, m and (add_comments(m[0]), m[1]), "reject")
        add("optimize", "'Optimisation' that changes a constant", change_constant(source), "reject")
        add("optimize", "'Optimisation' with an off-by-one loop bound", off_by_one(source), "reject")
        add("optimize", "'Optimisation' that drops a statement", delete_statement(source), "reject")
        add("optimize", "Constant folding", fold_constants(source), "accept")
        add("optimize", "Renaming a variable consistently", rename_variable(source), "accept")
        r = run_program(check["ir"], check["line_map"], stdin=stdin, max_steps=300_000)
        if r["error"]:
            add("bugs", "Bug 'fix' that only adds a comment", ("// fixed\n" + source, "no code changed"), "reject")
    else:
        add("fix", "Empty program", ("", "everything deleted"), "reject")
        funcs = re.findall(r"\b(?:int|void)\s+(\w+)\s*\(", source)
        tiny = "\n".join(f"int {f}() {{\n    return 0;\n}}" for f in (funcs or ["main"])) + "\n"
        add("fix", "Rewrite that throws the student's code away", (tiny, "every function body replaced by return 0"), "reject")
        bad = {e["line"] for e in check["errors"] if e.get("line")}
        gutted = "\n".join(l for i, l in enumerate(source.split("\n"), 1) if i not in bad)
        add("fix", "Delete the lines with errors", (gutted, f"removed line{'s' if len(bad) > 1 else ''} {', '.join(map(str, sorted(bad)))}"), "reject")
        add("fix", "Repair using the compiler's own hints", compiler_repair(source), "accept")
        add("docs", "Comments only (on the broken code)", (add_comments(source), "a comment above each function"), "accept")
        m = change_constant(source)
        add("docs", "Comments, plus a changed constant", m and (add_comments(m[0]), m[1]), "reject")

    for c in cases:
        c.update(judge(source, c["feature"], c["code"], stdin))
    return {"compiles": check["ok"], "cases": cases}
