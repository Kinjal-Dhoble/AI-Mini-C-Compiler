import argparse
from pathlib import Path
from compiler.pipeline import analyze
from compiler.diagnostics import format_diagnostic, enrich


def print_source_location(source_code, line, column):
    """Print the source line and a caret pointing at the reported location."""
    lines = source_code.splitlines()
    if line and 1 <= line <= len(lines):
        print(f"    {line:>4} | {lines[line - 1]}")
        print(f"         | {' ' * max((column or 1) - 1, 0)}^")


def _show(source_code, d):
    d = enrich(d)
    print(" ", format_diagnostic(d))
    print("      Explanation:", d["explanation"])
    if d.get("suggestion"):
        print("      Suggested fix:", d["suggestion"])
    if d.get("recovery"):
        print("      Recovery:", d["recovery"])
    print_source_location(source_code, d.get("line"), d.get("column"))


FRONTEND = "hand"


def compile_code(source_code, show_tokens=True):
    print("=" * 64)
    print("AI-ASSISTED MINI C COMPILER")
    print("=" * 64)
    r = analyze(source_code, frontend=FRONTEND)
    syntax = [e for e in r["errors"] if e["category"] in ("Syntax Error", "Lexical Error")]
    semantic = [e for e in r["errors"] if e not in syntax]

    print("\n[1/5] Lexical analysis")
    if show_tokens:
        for t in r["tokens"]:
            print(f"  {t.kind:<10} {t.value!r:<12} line {t.line}, column {t.column}")
    print(f"  {len(r['tokens'])} tokens." if not (r["stage"] == "lex" and r["errors"]) else "  Lexical errors found.")

    print("\n[2/5] Parsing and AST construction" + ("  (LALR(1) yacc grammar)" if FRONTEND == "yacc" else ""))
    if r["stage"] == "lex":
        print("  Skipped: fix the lexical errors first.")
    elif syntax:
        print(f"  {len(syntax)} syntax error{'s' if len(syntax) > 1 else ''} (the parser recovered and kept checking):")
    else:
        print("  Parsing successful.")
    for e in syntax:
        _show(source_code, e)

    print("\n[3/5] Semantic analysis and error checking")
    if r["ast"] is None:
        print("  Skipped: the syntax tree is incomplete.")
    else:
        for d in semantic + r["warnings"]:
            _show(source_code, d)
        if not semantic and not r["warnings"]:
            print("  No semantic errors found.")

    if r["errors"]:
        print(f"\nCOMPILATION FAILED: {len(r['errors'])} error{'s' if len(r['errors']) > 1 else ''} listed above.")
        return {"success": False, "ast": r["ast"], "diagnostics": r["errors"] + r["warnings"], "ir": [], "analysis": r}

    real_warnings = [w for w in r["warnings"] if w.get("category") != "Note"]
    if real_warnings:
        print(f"\nNote: {len(real_warnings)} warning(s) found. Compilation will continue.")

    print("\n[4/5] Intermediate code generation")
    for i, line in enumerate(r["ir"]):
        print(f"  {i:>3}  {line}")
    cfg, cg = r["cfg"], r["call_graph"]
    print("\nControl-flow graph")
    print("  Blocks:", len(cfg["blocks"]), "| Edges:", len(cfg["edges"]))
    for edge in cfg["edges"]:
        print(" ", edge[0], "--" + edge[2] + "-->", edge[1])
    print("\nCall graph")
    for f in cg["functions"]:
        tags = (" (recursive)" if f in cg["recursive"] else "") + (" (never called)" if f in cg["unreachable"] else "")
        print(f"  {f}() -> {', '.join(c + '()' for c in cg['calls'][f]) or '-'}{tags}")
    if r["loops"]:
        print("\nLoops (natural loops found from CFG back edges)")
        for L in r["loops"]:
            print(f"  line {L['header_line']} in {L['function']}(): depth {L['depth']}, back edge {', '.join('->'.join(e) for e in L['back_edges'])}")
    for f, c in r["complexity"].items():
        print(f"  Estimated complexity of {f}(): {c['big_o']}{' + recursion' if c.get('recursive') else ''}")

    print("\n[5/5] Static analysis")
    for finding in r["static"]:
        _show(source_code, finding)
    if not r["static"]:
        print("  No static-analysis findings.")

    print("\nCOMPILATION SUCCEEDED.")
    return {"success": True, "ast": r["ast"], "diagnostics": r["warnings"] + r["static"], "ir": r["ir"], "analysis": r}


def run_program_cli(result, name, stdin=None):
    """--run: build an executable with the back-end and execute it.
    With --input the text is fed to scanf; otherwise you type the input yourself."""
    from compiler import backend
    r = result["analysis"]
    b = backend.build(r["ir"], r["line_map"], Path("build"), Path(name).stem, name)
    print("\n" + "=" * 64 + "\nBUILD AND RUN\n" + "=" * 64)
    print(f"  {b['log'] or 'Built with ' + (b['command'][0] if b['command'] else 'the MiniC VM')}")
    if not b["ok"]:
        return
    print(f"  Executable: {b['exe']}")
    reads = any(i.startswith(("READ_INT", "READ ")) for i in r["ir"])
    if reads and stdin is None:
        import subprocess, sys
        print("  (this program reads input: type the numbers and press Enter)\n")
        cmd = [sys.executable, b["exe"]] if b["mode"] == "vm" else [b["exe"]]
        code = subprocess.run(cmd).returncode
        print(f"\n  Exit code: {code}")
        return
    run = backend.run_executable(b, stdin=stdin or "")
    for line in run["stdout"].splitlines():
        print("  " + line)
    if run["stderr"]:
        print("  " + run["stderr"].strip())
    print(f"  Exit code: {run['exit_code']}" if not run["timed_out"] else "  Stopped: the program did not finish within 5 seconds.")


def run_ai_features(source, result, features, args, source_file=None):
    """Run the LLM features after compilation. Documentation ALWAYS runs.

    Compilation failed:
      1. fix suggestions (offer to write the corrected code into the file)
      2. documentation of the user's code AS WRITTEN, with each error explained
      3. optimisation of the corrected code (if requested and the fix was applied)
      4. documentation of the AI-CORRECTED code, with each fix marked
         -> written into the file if the fix was applied, else offered as <name>.fixed.c
    Compilation succeeded:
      1. optimisation (if requested)   2. documentation of the final code
    Returns True if the final code in the file compiles.
    """
    from ai_assistant.ai_features import AIFeatures, print_report, LLMError
    from ai_assistant.file_writer import offer_new_file
    from ai_assistant.llm_client import OllamaClient

    client = OllamaClient(model=args.model, host=args.ollama_host, timeout=args.timeout)
    try:
        client.health_check()
    except LLMError as exc:
        offline = [f for f in features if f in ("bugs", "refactor", "flow")]
        if offline and result["success"]:
            # these three work without the model: the compiler's own rules, runs and patterns
            print(f"\nOllama is not available ({str(exc).split('.')[0]}).\n"
                  f"Showing the compiler's own analysis for: {', '.join(offline)}.")
            ai = AIFeatures(client, stdin=getattr(args, "input", None))
            for f in offline:
                print_report(f, {"bugs": ai.detect_bugs, "refactor": ai.refactor, "flow": ai.explain_flow}[f](source))
            return result["success"]
        print(f"\nAI features skipped: {exc}")
        return result["success"]
    import time
    clock = {"t": time.monotonic()}

    def progress(e):
        """One live status line per attempt, so a slow local model is never a silent wait."""
        k = e["type"]
        if k == "ask":
            clock["t"] = time.monotonic()
            print(f"\n  attempt {e['attempt']} of {e['of']}: asking the model...", end="", flush=True)
        elif k == "progress":
            what = f"thinking ({e['thinking']:,} chars)" if e["thinking"] and not e["chars"] else f"writing ({e['chars']:,} chars)"
            print(f"\r  attempt {e['attempt']}: model {what}, {time.monotonic() - clock['t']:.0f} s      ", end="", flush=True)
        elif k == "checking":
            print(f"\r  attempt {e['attempt']}: checking the answer with the compiler...            ", end="", flush=True)
        elif k == "check":
            verdict = "accepted" if e["passed"] else "REJECTED: " + "; ".join(e["notes"])[:100]
            print(f"\r  attempt {e['attempt']}: {verdict} ({time.monotonic() - clock['t']:.0f} s)            ")

    ai = AIFeatures(client, on_event=progress)
    print(f"\nAI assistant: using Ollama model '{client.model}' at {client.host}")
    try:
        client.warmup()                       # load the model now; later requests skip the load time
    except LLMError:
        pass
    can_write = source_file is not None and not args.no_write
    if source_file is None and not args.no_write:
        print("  (built-in sample: nothing to write back; pass a file to enable writing)")

    def offer(report, key, what):
        """Ask to write report[key] into the source file; return the new source or None."""
        if not can_write or "result" not in report:
            return None
        try:
            if source_file.offer(report["result"][key], report["verification"]["passed"],
                                 what, auto_yes=args.yes):
                return source_file.text
        except (OSError, RuntimeError) as exc:
            print(f"  Could not write file: {exc}")
        return None

    def deliver_docs(report, into_file):
        """--docs-out wins; otherwise write into the file or offer a separate .fixed.c."""
        if "result" not in report:
            return
        code, ok = report["result"]["documented_code"], report["verification"]["passed"]
        if args.docs_out:
            if ok:
                Path(args.docs_out).write_text(code + "\n", encoding="utf-8")
                print(f"\n  Documented source written to {args.docs_out}")
            else:
                print("\n  Documented source NOT written: the model changed code tokens.")
        elif into_file:
            offer(report, "documented_code", "beginner comments")
        elif can_write:
            target = source_file.path.with_name(source_file.path.stem + ".fixed.c")
            print(f"\n  Your original file was left unchanged.")
            offer_new_file(target, code, ok, "documented corrected code", auto_yes=args.yes)

    current, success = source, result["success"]
    try:
        if not success:
            # 1. fixes
            fix = ai.suggest_fixes(current)
            print_report("fix", fix)
            corrected = fix.get("result", {}).get("fixed_code")
            applied = offer(fix, "fixed_code", "corrected code") is not None

            # 2. documentation of the user's code as written
            original_docs = ai.document_code(source)
            print_report("docs", original_docs, "AI DOCUMENTATION - YOUR CODE (as written, errors explained)")
            if not applied and not args.docs_out:
                offer(original_docs, "documented_code", "commented original code")

            if applied:
                current = source_file.text
                print("\nRecompiling the corrected file...")
                success = compile_code(current, show_tokens=False)["success"]
                # 3. optimisation of the corrected code
                if success and "optimize" in features:
                    opt = ai.suggest_optimizations(current)
                    print_report("optimize", opt)
                    if offer(opt, "optimized_code", "optimised code") is not None:
                        current = source_file.text
            elif features & {"optimize", "bugs", "refactor", "flow"}:
                print("\n(Optimisation, bug detection, refactoring and flow analysis run on the corrected code once it is written to the file.)")

            # 4. documentation of the AI-corrected code
            target = current if applied else corrected
            if target:
                docs = ai.document_code(target, corrected_from=source)
                print_report("docs", docs, "AI DOCUMENTATION - AI-CORRECTED CODE (fixes marked)")
                deliver_docs(docs, into_file=applied)
            return success

        if "optimize" in features:
            opt = ai.suggest_optimizations(current)
            print_report("optimize", opt)
            if offer(opt, "optimized_code", "optimised code") is not None:
                current = source_file.text
        if "bugs" in features:
            bugs = ai.detect_bugs(current)               # quick: verdicts and explanations only
            print_report("bugs", bugs)
            serious = [f for f in bugs.get("findings", []) if f.get("confirmed") or f.get("severity") == "high"]
            if serious and bugs.get("result") is not None and can_write:
                want = args.yes or input("\n  Ask the model for a full corrected program? It is compiled and run "
                                         "before you see it, but takes longer. [y/N] ").strip().lower() in ("y", "yes")
                if want:
                    bugs = ai.detect_bugs(current, full_fix=True)
                    print_report("bugs", bugs)
            if bugs.get("result", {}).get("fixed_code") and offer(bugs, "fixed_code", "bug fixes") is not None:
                current = source_file.text
        if "refactor" in features:
            ref = ai.refactor(current)
            print_report("refactor", ref)
            good = [it for it in ref.get("items", []) if it.get("verified")]
            code = ref.get("all_code") if ref.get("all_verified") else (good[0]["after_code"] if len(good) == 1 else None)
            if code and offer({"result": {"code": code}, "verification": {"passed": True}}, "code", "refactored code") is not None:
                current = source_file.text
        if "flow" in features:
            flow = ai.explain_flow(current)
            print_report("flow", flow)
            if flow.get("result", {}).get("improved_code") and offer(flow, "improved_code", "faster algorithm") is not None:
                current = source_file.text
        docs = ai.document_code(current)
        print_report("docs", docs)
        deliver_docs(docs, into_file=True)
    except LLMError as exc:
        print(f"\nAI assistant error: {exc}")
    return success


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="AI-Assisted Mini C Compiler (educational subset)")
    ap.add_argument("file", nargs="?", help="C-like source file to compile")
    ap.add_argument("--ai", default="docs",
                    help="LLM features: fix,optimize,docs,bugs,refactor,flow | all | none. Documentation always runs "
                         "(and fixes always run when compilation fails) unless --ai none. Default: docs")
    ap.add_argument("--model", default=None, help="Ollama model (default: $OLLAMA_MODEL or qwen2.5-coder:7b)")
    ap.add_argument("--ollama-host", default=None, help="Ollama URL (default: $OLLAMA_HOST or http://localhost:11434)")
    ap.add_argument("--timeout", type=int, default=180, help="seconds to wait for each LLM reply")
    ap.add_argument("--docs-out", default=None, help="write verified documented source to this file")
    ap.add_argument("--no-tokens", action="store_true", help="hide the token listing")
    ap.add_argument("--run", action="store_true", help="build an executable and run it after a successful compile")
    ap.add_argument("--input", default=None, help='input for scanf when using --run, e.g. --input "5 7"')
    ap.add_argument("--frontend", choices=["hand", "yacc"], default="hand",
                    help="parser: hand-written recursive descent (default) or the LALR(1) yacc grammar")
    ap.add_argument("--no-write", action="store_true", help="never offer to write AI changes into the file")
    ap.add_argument("--yes", action="store_true",
                    help="apply VERIFIED AI changes without asking (unverified ones still ask)")
    args = ap.parse_args()
    sample = """int main() {
  int x = 10 + 20 * 3;
  if (x >= 50) { x = x - 1; }
  return x;
}"""
    source_file = None
    try:
        if args.file:
            from ai_assistant.file_writer import SourceFile
            source_file = SourceFile(args.file)
            source = source_file.text
        else:
            source = sample
    except (OSError, UnicodeDecodeError) as exc:
        print(f"Could not read input file: {exc}")
        raise SystemExit(2)
    FRONTEND = args.frontend
    result = compile_code(source, show_tokens=not args.no_tokens)
    if args.run and result["success"]:
        run_program_cli(result, args.file or "sample.c", args.input)

    requested = {f.strip().lower() for f in args.ai.split(",") if f.strip()}
    if "all" in requested:
        requested = {"fix", "optimize", "docs", "bugs", "refactor", "flow"}
    if "none" in requested:
        requested = set()
    elif requested:
        requested |= {"docs"}                      # documentation always runs
        if not result["success"]:
            requested |= {"fix"}                   # needed to document the corrected code
    unknown = requested - {"fix", "optimize", "docs", "bugs", "refactor", "flow"}
    if unknown:
        print(f"Unknown --ai feature(s): {', '.join(sorted(unknown))}")
        raise SystemExit(2)
    success = result["success"]
    if requested:
        success = run_ai_features(source, result, requested, args, source_file)
    raise SystemExit(0 if success else 1)
