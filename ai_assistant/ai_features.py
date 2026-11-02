"""LLM-powered features: fix suggestions, optimisation suggestions, beginner documentation.

Every feature follows the same loop:
    1. build a grounded prompt (source + diagnostics + IR + compiler facts)
    2. ask the model, with the skill file as system prompt and a JSON schema for the output
    3. VERIFY the answer with the real compiler
    4. if verification fails, send the failure back to the model and retry (max_retries)

So the model's output is never trusted blindly: fixes must compile, optimisations must compile
and return the same value, documentation must contain exactly the original code tokens.

Bug detection, refactoring and flow explanation start from deterministic compiler analysis
(rules, execution on the VM, AST rewriting, CFG loops). They still return those results when
Ollama is unavailable; the model adds judgement and explanations on top.
"""
from compiler.parser import FunctionNode
from ai_assistant.context_builder import build_context, check_source, lenient_signature
from ai_assistant.ir_interpreter import behaviour, observed, describe_observed, IRRuntimeError, run_program
from ai_assistant import bug_detector
from compiler.refactor import find_refactorings
from ai_assistant.llm_client import OllamaClient, LLMError
from ai_assistant.skill_loader import load_skill

# ---------------------------------------------------------------- JSON schemas
_STR = {"type": "string"}
FIX_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": _STR,
        "fixes": {"type": "array", "items": {"type": "object", "properties": {
            "line": {"type": "integer"}, "error": _STR,
            "source": {"type": "string", "enum": ["compiler", "found_by_ai"]},
            "why": _STR, "before": _STR, "after": _STR},
            "required": ["line", "error", "why", "before", "after"]}},
        "fixed_code": _STR,
        "lesson": _STR,
    },
    "required": ["summary", "fixes", "fixed_code"],
}
OPT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": _STR,
        "suggestions": {"type": "array", "items": {"type": "object", "properties": {
            "line": {"type": "integer"}, "technique": _STR,
            "impact": {"type": "string", "enum": ["high", "medium", "low"]},
            "before": _STR, "after": _STR, "explanation": _STR, "safe_because": _STR},
            "required": ["line", "technique", "impact", "explanation"]}},
        "optimized_code": _STR,
    },
    "required": ["summary", "suggestions", "optimized_code"],
}
DOC_SCHEMA = {
    "type": "object",
    "properties": {
        "overview": _STR,
        "concepts": {"type": "array", "items": _STR},
        "documented_code": _STR,
    },
    "required": ["overview", "documented_code"],
}


BUG_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": _STR,
        "findings": {"type": "array", "items": {"type": "object", "properties": {
            "id": _STR, "verdict": {"type": "string", "enum": ["real_bug", "likely_bug", "not_a_bug"]},
            "explanation": _STR, "fix": _STR}, "required": ["id", "verdict", "explanation"]}},
        "extra": {"type": "array", "items": {"type": "object", "properties": {
            "line": {"type": "integer"}, "title": _STR, "explanation": _STR, "fix": _STR},
            "required": ["line", "title", "explanation"]}},
        "fixed_code": _STR,
    },
    "required": ["summary", "findings", "fixed_code"],
}
REFACTOR_SCHEMA = {
    "type": "object",
    "properties": {"summary": _STR, "tip": _STR,
                   "explanations": {"type": "array", "items": {"type": "object", "properties": {
                       "id": _STR, "explanation": _STR}, "required": ["id", "explanation"]}}},
    "required": ["summary", "explanations"],
}
FLOW_SCHEMA = {
    "type": "object",
    "properties": {
        "overview": _STR,
        "functions": {"type": "array", "items": {"type": "object", "properties": {
            "name": _STR, "purpose": _STR, "calls": {"type": "array", "items": _STR},
            "called_by": {"type": "array", "items": _STR}}, "required": ["name", "purpose"]}},
        "loops": {"type": "array", "items": {"type": "object", "properties": {
            "line": {"type": "integer"}, "runs": _STR, "explanation": _STR}, "required": ["line", "explanation"]}},
        "complexity": {"type": "object", "properties": {"big_o": _STR, "reasoning": _STR}, "required": ["big_o", "reasoning"]},
        "hints": {"type": "array", "items": {"type": "object", "properties": {
            "line": {"type": "integer"}, "issue": _STR, "suggestion": _STR, "before": _STR, "after": _STR},
            "required": ["line", "issue", "suggestion"]}},
        "improved_code": _STR,
    },
    "required": ["overview", "functions", "loops", "complexity", "hints", "improved_code"],
}


def _strip_line_numbers(code):
    """Defensive: remove `NN | ` prefixes if the model copied them from the prompt."""
    import re
    lines = code.splitlines()
    if lines and all(re.match(r"^\s*\d+ \| ", l) or not l.strip() for l in lines):
        return "\n".join(re.sub(r"^\s*\d+ \| ", "", l) for l in lines)
    return code


def _error_list(check):
    return "\n".join(f"- [{e['category']}] {e['message']}"
                     + (f" (line {e['line']}, column {e.get('column')})" if "line" in e else "")
                     for e in check["errors"])


def _defined_functions(src):
    import re
    return set(re.findall(r"\b(?:int|void)\s+(\w+)\s*\(", src))


def _same_program(original, proposed, min_ratio=0.6):
    """Guard against 'fixes' that compile only because they threw the program away.
    Returns None if fine, else a reason."""
    from difflib import SequenceMatcher
    if not proposed.strip():
        return "the corrected program is empty"
    missing = _defined_functions(original) - _defined_functions(proposed)
    if missing:
        return f"the corrected program no longer defines {', '.join(sorted(missing))}()"
    ratio = SequenceMatcher(None, lenient_signature(original), lenient_signature(proposed), autojunk=False).ratio()
    if ratio < min_ratio:
        return f"the corrected program keeps only {ratio:.0%} of the original code; fix the mistakes instead of rewriting it"
    return None


def _main_has_no_params(ast):
    return any(isinstance(n, FunctionNode) and n.name == "main" and not n.params
               for n in ast.statements)


class AIFeatures:
    def __init__(self, client=None, skills_dir=None, stdin=None, on_event=None):
        self.client = client or OllamaClient()
        self.on_event = on_event         # live progress for the IDE: ask / progress / check events
        self.stdin = stdin               # input for scanf when programs are run to verify answers
        self.skills_dir = skills_dir
        self._skills = {}

    def skill(self, name):
        if name not in self._skills:
            self._skills[name] = (load_skill(name, self.skills_dir) if self.skills_dir
                                  else load_skill(name))
        return self._skills[name]

    def _event(self, **e):
        if self.on_event:
            try:
                self.on_event(e)
            except Exception:
                pass

    # --------------------------------------------------------------- core loop
    def _run(self, skill_name, schema, user_prompt, verify, code_key):
        """Ask -> verify -> feed back failures. Returns (answer_dict, verification_dict)."""
        skill = self.skill(skill_name)
        history, answer, verdict = [], None, {"passed": False, "notes": []}
        log = []          # the referee's record: every attempt, why it was rejected, what was sent back
        for attempt in range(skill.max_retries + 1):
            prompt = user_prompt if attempt == 0 else verdict["feedback"]
            self._event(type="ask", attempt=attempt + 1, of=skill.max_retries + 1)
            extra = {}
            if self.on_event and getattr(self.client, "streams", False):
                extra["on_progress"] = lambda chars, thinking, a=attempt + 1: self._event(type="progress", attempt=a, chars=chars, thinking=thinking)
            answer = self.client.chat(skill.system_prompt, prompt, schema=schema,
                                      temperature=skill.temperature, history=history, **extra)
            answer[code_key] = _strip_line_numbers(answer.get(code_key, ""))
            self._event(type="checking", attempt=attempt + 1)
            verdict = verify(answer[code_key])
            verdict["attempts"] = attempt + 1
            self._event(type="check", attempt=attempt + 1, passed=verdict["passed"], notes=verdict.get("notes", []))
            log.append({"attempt": attempt + 1, "passed": verdict["passed"], "notes": list(verdict.get("notes", [])),
                        "feedback": verdict.get("feedback", "") if not verdict["passed"] else ""})
            verdict["log"] = log
            if verdict["passed"]:
                break
            import json
            history += [{"role": "user", "content": prompt},
                        {"role": "assistant", "content": json.dumps(answer)}]
        return answer, verdict

    # ------------------------------------------------------------ feature: fix
    def suggest_fixes(self, source):
        check = check_source(source)
        if check["ok"]:
            return {"skipped": "Code already compiles; no fixes needed."}
        prompt = ("The program below FAILED to compile. Suggest fixes.\n\n"
                  + build_context(source, check, include_ir=False))

        def verify(code):
            why = _same_program(source, code)
            if why:
                return {"passed": False, "notes": [why],
                        "feedback": f"Verification failed: {why}. Return the user's whole program with only the mistakes corrected, as the complete JSON object."}
            c = check_source(code)
            if c["ok"]:
                return {"passed": True, "notes": ["fixed_code compiles with no errors", "keeps the original functions and most of the code"]}
            return {"passed": False, "notes": ["fixed_code still fails: " + _error_list(c)],
                    "feedback": ("Verification failed: your fixed_code was compiled and produced "
                                 f"these errors:\n{_error_list(c)}\nReturn the corrected JSON.")}
        answer, verdict = self._run("fix_suggestions", FIX_SCHEMA, prompt, verify, "fixed_code")
        return {"result": answer, "verification": verdict}

    # ------------------------------------------------------- feature: optimise
    def suggest_optimizations(self, source):
        check = check_source(source)
        if not check["ok"]:
            return {"skipped": "Code does not compile; run fix suggestions first."}
        prompt = ("The program below compiled successfully. Suggest optimisations.\n\n"
                  + build_context(source, check))
        baseline = None
        if _main_has_no_params(check["ast"]):
            baseline = observed(check["ir"], stdin=self.stdin)       # (return value, printed text, runtime error)
        describe = describe_observed

        def verify(code):
            c = check_source(code)
            if not c["ok"]:
                return {"passed": False, "notes": ["optimized_code does not compile"],
                        "feedback": ("Verification failed: optimized_code does not compile:\n"
                                     f"{_error_list(c)}\nReturn the corrected JSON.")}
            notes = ["optimized_code compiles",
                     f"IR size {len(check['ir'])} -> {len(c['ir'])} instructions"]
            if baseline is not None:
                new = observed(c["ir"], stdin=self.stdin)
                new_text = describe(new)
                if new != baseline:
                    return {"passed": False,
                            "notes": notes + [f"original {describe(baseline)}; optimised {new_text}"],
                            "feedback": (f"Verification failed: the original program {describe(baseline)}, "
                                         f"but your optimized_code {new_text}. An optimisation must not change "
                                         "behaviour. Fix optimized_code and return the corrected JSON.")}
                notes.append(f"behaviour preserved: like the original, it {describe(baseline)}")
            else:
                notes.append("behaviour not executed (main has parameters)")
            return {"passed": True, "notes": notes}
        answer, verdict = self._run("optimization", OPT_SCHEMA, prompt, verify, "optimized_code")
        return {"result": answer, "verification": verdict}

    # ---------------------------------------------------------- feature: docs
    def document_code(self, source, corrected_from=None):
        """Add beginner comments. Works for every program:
        - code that compiled          -> normal documentation
        - code that failed to compile -> documentation of what it is trying to do, with each
                                         error explained in a comment at its location
        - AI-corrected code           -> pass `corrected_from` (the original source); the
                                         comments also point out what was fixed and why
        Verification: comments are stripped and the remaining tokens must be identical."""
        check = check_source(source)
        original = lenient_signature(source)
        if not original:
            return {"skipped": "Nothing to document: the file contains no code."}
        if corrected_from is not None:
            orig_check = check_source(corrected_from)
            header = ("MODE: corrected. This is the AI-CORRECTED version of the user's program. "
                      "Add beginner-friendly comments, and mark each line that differs from the "
                      "original with a `// FIXED:` comment saying what was wrong and why the change works.\n\n"
                      "## Original program (before correction)\n```c\n" + corrected_from.rstrip() + "\n```\n"
                      "## Errors the compiler reported for the original\n"
                      + (_error_list(orig_check) or "- (none)") + "\n\n## Corrected program to document\n")
        elif check["ok"]:
            header = "MODE: compiled. Add beginner-friendly comments to this program that compiled successfully.\n\n"
        else:
            header = ("MODE: failed. This program did NOT compile. Document it AS WRITTEN: explain what each "
                      "part is trying to do, and at each error location add a `// ERROR:` comment that "
                      "explains the problem in plain words and how to fix it. Do NOT fix the code itself.\n\n")
        prompt = header + build_context(source, check, include_ir=False)

        def verify(code):
            new = lenient_signature(code)
            if new != original:
                idx = next((i for i, (a, b) in enumerate(zip(original, new)) if a != b),
                           min(len(original), len(new)))
                want = original[idx][1] if idx < len(original) else "(end of code)"
                got = new[idx][1] if idx < len(new) else "(end of code)"
                return {"passed": False, "notes": [f"code changed at token {idx}: expected {want!r}, got {got!r}"],
                        "feedback": (f"Verification failed: you changed the code. At code token #{idx} "
                                     f"the original has {want!r} but yours has {got!r}. Only add comments "
                                     "(check every /* has a matching */). Return the corrected JSON.")}
            notes = ["code tokens identical to the original; only comments added"]
            if not check["ok"]:
                notes.append("documents code that does not compile (errors are explained, not fixed)")
            return {"passed": True, "notes": notes}
        answer, verdict = self._run("beginner_docs", DOC_SCHEMA, prompt, verify, "documented_code")
        return {"result": answer, "verification": verdict}



# ------------------------------------------------------------ feature: bugs
def _bugs(self, source, full_fix=False):
    """Rules + execution (always) and the LLM's judgement (when Ollama is available).
    full_fix=False (default, fast): the model only judges and explains each finding, with the
      changed line(s) as the fix. No whole-program rewrite, so the answer is short and nothing
      needs re-checking.
    full_fix=True: the model returns the whole corrected program, which is compiled and run."""
    check = check_source(source)
    if not check["ok"]:
        return {"skipped": "Bug detection runs on code that compiles; fix the compile errors first."}
    det = bug_detector.detect(check, stdin=self.stdin)
    report = {"findings": det["findings"], "run": det["run"], "full_fix": full_fix}
    self._event(type="partial", report=report)          # the compiler's findings, shown before the model answers
    lines = [f"- {f['id']} line {f['line']} [{f['rule']}, severity {f['severity']}, "
             f"{'CONFIRMED by running' if f['confirmed'] else 'suspected by a rule'}]: {f['message']}"
             + (f" Evidence: {f['evidence']}" if f.get("evidence") else "") for f in det["findings"]]
    run = det["run"]
    run_text = (f"The program was run{' with sample input ' + str(run.get('inputs')) if run.get('used_sample_input') else ''}: output {run['output']}, returned {run['return_value']}"
                + (f", stopped with error '{run['error']}' at line {run['error_line']}" if run.get("error") else "")
                if run.get("ran") else f"The program was not run ({run.get('reason')}).")
    mode = ("## What to return now\nONLY the corrected program: put it in fixed_code. Return findings and extra as "
            "empty lists and a one-sentence summary.\n\n" if full_fix else
            "## What to return now\nBe brief: for each finding a verdict, at most two short sentences of explanation, "
            "and as fix only the changed line(s). At most 3 extra findings. Do NOT write the whole program: "
            "return fixed_code as an empty string.\n\n")
    prompt = (mode + "Review these bug findings for the program below.\n\n## Findings from the compiler\n"
              + ("\n".join(lines) or "- (no rule fired and the program ran without problems)")
              + f"\n\n## Execution\n{run_text}\n\n" + build_context(source, check, include_ir=False))
    confirmed = {f["rule"] for f in det["findings"] if f["confirmed"]}

    def verify(code):
        if not code.strip():
            return {"passed": True, "notes": ["no corrected program was proposed"]}
        why = _same_program(source, code, 0.5)
        if why:
            return {"passed": False, "notes": [why], "feedback": f"Verification failed: {why}. Return the corrected JSON."}
        c = check_source(code)
        if not c["ok"]:
            return {"passed": False, "notes": ["fixed_code does not compile"],
                    "feedback": f"Verification failed: fixed_code does not compile:\n{_error_list(c)}\nReturn the corrected JSON."}
        notes = ["fixed_code compiles"]
        if run.get("ran"):
            r = run_program(c["ir"], c["line_map"], max_steps=300_000, stdin=self.stdin)
            problems = []
            if r["error"]:
                problems.append(f"it still fails when run: {r['error']} (line {r['error_line']})")
            if r["overflows"] and "overflow" in confirmed:
                problems.append(f"it still overflows: {r['overflows'][0]['message']}")
            if problems:
                return {"passed": False, "notes": notes + problems,
                        "feedback": "Verification failed: " + "; ".join(problems) + ". Fix fixed_code and return the corrected JSON."}
            notes.append(f"runs without errors (prints {r['stdout'][:60]!r}{'...' if len(r['stdout']) > 60 else ''}, returns {r['return_value']})")
        return {"passed": True, "notes": notes}
    try:
        answer, verdict = self._run("bug_detection", BUG_SCHEMA, prompt, verify, "fixed_code")
        report.update({"result": answer, "verification": verdict})
    except LLMError as exc:
        report.update({"ai_error": str(exc)})
    return report


# -------------------------------------------------------- feature: refactor
def _refactor(self, source):
    check = check_source(source)
    if not check["ok"]:
        return {"skipped": "Refactoring needs code that compiles; fix the compile errors first."}
    ref = find_refactorings(source, check)
    items = ref["items"]
    report = {"items": items, "all_code": ref.get("all_code"), "all_ids": ref.get("all_ids"),
              "all_verified": ref.get("all_verified")}
    if not items:
        report["summary"] = "No refactoring patterns matched: the loops and conditions are already in a simple form."
        return report
    prompt = ("Explain these refactorings to a beginner.\n\n" + "\n\n".join(
        f"### {it['id']}: {it['title']} (line {it['line']})\nBefore:\n```c\n{it.get('before_display', it['before'])}\n```\nAfter:\n```c\n{it.get('after_display', it['after'])}\n```\n"
        f"Checked: {', '.join(it.get('notes', []))}" for it in items))
    try:
        skill = self.skill("refactoring")
        ans = self.client.chat(skill.system_prompt, prompt, schema=REFACTOR_SCHEMA, temperature=skill.temperature)
        by_id = {e.get("id"): e.get("explanation") for e in ans.get("explanations", [])}
        for it in items:
            if by_id.get(it["id"]):
                it["ai_explanation"] = by_id[it["id"]]
        report.update({"summary": ans.get("summary"), "tip": ans.get("tip")})
    except LLMError as exc:
        report["ai_error"] = str(exc)
    return report


# ------------------------------------------------------------ feature: flow
def _flow(self, source):
    from ai_assistant.context_builder import structure_facts
    check = check_source(source)
    if not check["ok"]:
        return {"skipped": "Flow analysis needs code that compiles; fix the compile errors first."}
    cfg = check["cfg"]
    facts = {"call_graph": {k: check["call_graph"][k] for k in ("functions", "calls", "recursive", "unreachable")},
             "loops": check["loops"], "ast_loops": check["ast_loops"], "complexity": check["complexity"],
             "blocks": len(cfg["blocks"]), "edges": len(cfg["edges"])}
    report = {"facts": facts, "structure": structure_facts(check)}
    prompt = (f"Explain the control flow and complexity of this program. Its CFG has {len(cfg['blocks'])} basic blocks "
              f"and {len(cfg['edges'])} edges.\n\n" + build_context(source, check))
    base = None
    if _main_has_no_params(check["ast"]):
        base = observed(check["ir"], max_steps=500_000, stdin=self.stdin)

    def verify(code):
        if not code.strip():
            return {"passed": True, "notes": ["no improved program was proposed"]}
        c = check_source(code)
        if not c["ok"]:
            return {"passed": False, "notes": ["improved_code does not compile"],
                    "feedback": f"Verification failed: improved_code does not compile:\n{_error_list(c)}\nReturn the corrected JSON."}
        notes = ["improved_code compiles"]
        if base is not None:
            new = observed(c["ir"], max_steps=500_000, stdin=self.stdin)
            if new != base:
                return {"passed": False, "notes": notes + [f"original {describe_observed(base)}; improved {describe_observed(new)}"],
                        "feedback": (f"Verification failed: the original program {describe_observed(base)}, but "
                                     f"improved_code {describe_observed(new)}. It must behave exactly the same. Return the corrected JSON.")}
            orig_steps = run_program(check["ir"], max_steps=500_000, stdin=self.stdin)["steps"]
            new_steps = run_program(c["ir"], max_steps=500_000, stdin=self.stdin)["steps"]
            notes.append(f"same output and return value; {orig_steps:,} -> {new_steps:,} VM steps")
        return {"passed": True, "notes": notes}
    try:
        answer, verdict = self._run("flow_explanation", FLOW_SCHEMA, prompt, verify, "improved_code")
        report.update({"result": answer, "verification": verdict})
    except LLMError as exc:
        report["ai_error"] = str(exc)
    return report


AIFeatures.detect_bugs = _bugs
AIFeatures.refactor = _refactor
AIFeatures.explain_flow = _flow


# --------------------------------------------------------------- printing
def print_referee(v):
    """The referee's log: each attempt, accepted or rejected, and why."""
    log = v.get("log") or []
    if not any(not e["passed"] for e in log):
        return
    print("  Referee log (the compiler checked every answer):")
    for e in log:
        if e["passed"]:
            print(f"    attempt {e['attempt']}: ACCEPTED - {'; '.join(e['notes'])}")
        else:
            print(f"    attempt {e['attempt']}: REJECTED - {'; '.join(e['notes'])}")
            print("               sent back to the model: " + e["feedback"].splitlines()[0][:150])


def print_report(kind, report, title=None):
    title = title or {"fix": "AI FIX SUGGESTIONS", "optimize": "AI OPTIMISATION SUGGESTIONS",
                      "docs": "AI BEGINNER DOCUMENTATION", "bugs": "BUG DETECTION (rules + execution + AI)",
                      "refactor": "REFACTORING (AST pattern matching)", "flow": "CONTROL FLOW AND COMPLEXITY"}[kind]
    print("\n" + "=" * 64 + f"\n{title}\n" + "=" * 64)
    if "skipped" in report:
        print(" ", report["skipped"]); return
    if kind in ("bugs", "refactor", "flow"):
        return _print_analysis(kind, report)
    r, v = report["result"], report["verification"]
    status = "VERIFIED" if v["passed"] else "NOT VERIFIED - review carefully"
    print(f"  [{status}] after {v['attempts']} attempt(s)")
    for note in v["notes"]:
        print("   -", note)
    print_referee(v)
    if kind == "fix":
        print("\n ", r.get("summary", ""))
        for i, f in enumerate(r.get("fixes", []), 1):
            tag = " (found by AI)" if f.get("source") == "found_by_ai" else ""
            print(f"\n  Fix {i} - line {f.get('line')}{tag}: {f.get('error')}")
            print(f"    Why:    {f.get('why')}")
            print(f"    Before: {f.get('before', '').strip()}")
            print(f"    After:  {f.get('after', '').strip()}")
        print("\n  Corrected program:\n" + _indent(r.get("fixed_code", "")))
        if r.get("lesson"):
            print("\n  Tip:", r["lesson"])
    elif kind == "optimize":
        print("\n ", r.get("summary", ""))
        for i, s in enumerate(r.get("suggestions", []), 1):
            print(f"\n  {i}. [{s.get('impact', '?').upper()}] {s.get('technique')} - line {s.get('line')}")
            print(f"    {s.get('explanation')}")
            if s.get("before"):
                print(f"    Before: {s['before'].strip()}")
                print(f"    After:  {s.get('after', '').strip()}")
            if s.get("safe_because"):
                print(f"    Safe because: {s['safe_because']}")
        print("\n  Optimised program:\n" + _indent(r.get("optimized_code", "")))
    else:
        print("\n ", r.get("overview", ""))
        if r.get("concepts"):
            print("  Concepts:", ", ".join(r["concepts"]))
        print("\n  Documented program:\n" + _indent(r.get("documented_code", "")))


def _print_analysis(kind, report):
    if report.get("ai_error"):
        print(f"  (AI unavailable, showing compiler results only: {report['ai_error']})")
    r, v = report.get("result") or {}, report.get("verification")
    if v:
        print(f"  [{'VERIFIED' if v['passed'] else 'NOT VERIFIED'}] after {v['attempts']} attempt(s)")
        for note in v["notes"]:
            print("   -", note)
        print_referee(v)
    if kind == "bugs":
        run = report["run"]
        print(f"\n  Ran the program: {'yes' if run.get('ran') else 'no, ' + run.get('reason', '')}"
              + (f"; stopped with '{run['error']}' at line {run['error_line']}" if run.get("error") else ""))
        verdicts = {f["id"]: f for f in r.get("findings", [])}
        for f in report["findings"]:
            ai = verdicts.get(f["id"], {})
            print(f"\n  {f['id']} line {f['line']} [{f['severity']}] {f['title']}"
                  f"{' - CONFIRMED by running' if f['confirmed'] else ''}{' - AI: ' + ai['verdict'] if ai.get('verdict') else ''}")
            print(f"     {ai.get('explanation') or f['message']}")
            if f.get("evidence"):
                print(f"     {f['evidence']}")
            if ai.get("fix"):
                print("     Fix: " + ai["fix"].replace("\n", "\n          "))
            elif f.get("fix_hint"):
                print("     How to fix: " + f["fix_hint"])
        for e in r.get("extra", []):
            print(f"\n  AI also suspects line {e['line']}: {e['title']}\n     {e['explanation']}")
        if r.get("summary"):
            print("\n  " + r["summary"])
        if r.get("fixed_code"):
            print("\n  Corrected program:\n" + _indent(r["fixed_code"]))
    elif kind == "refactor":
        if report.get("summary"):
            print("\n  " + report["summary"])
        for it in report["items"]:
            print(f"\n  {it['id']} line {it['line']}: {it['title']} [{'verified' if it.get('verified') else 'NOT verified'}]")
            print("     " + (it.get("ai_explanation") or it["explanation"]))
            print("     Before:\n" + _indent(it.get("before_display", it["before"]), 8) + "\n     After:\n" + _indent(it.get("after_display", it["after"]), 8))
        if report.get("tip"):
            print("\n  Tip: " + report["tip"])
    else:
        print("\n  Compiler facts:")
        for f in report["structure"]:
            print("   " + f)
        if r:
            print("\n  " + r.get("overview", ""))
            for fn in r.get("functions", []):
                print(f"   {fn['name']}(): {fn['purpose']}")
            for L in r.get("loops", []):
                print(f"   loop at line {L['line']}: runs {L.get('runs', '?')}. {L['explanation']}")
            c = r.get("complexity", {})
            print(f"\n  Complexity: {c.get('big_o')} - {c.get('reasoning')}")
            for h in r.get("hints", []):
                print(f"\n  Hint (line {h['line']}): {h['issue']}\n     {h['suggestion']} ({h.get('before', '?')} -> {h.get('after', '?')})")
            if r.get("improved_code"):
                print("\n  Improved program:\n" + _indent(r["improved_code"]))


def _indent(code, n=4):
    return "\n".join(" " * n + l for l in code.splitlines())


def _indent_old(code):
    return "\n".join("    " + l for l in code.splitlines())


__all__ = ["AIFeatures", "print_report", "print_referee", "LLMError"]
