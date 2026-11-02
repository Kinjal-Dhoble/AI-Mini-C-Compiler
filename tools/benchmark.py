"""Benchmark the AI features against the compiler's own verification.

    python tools/benchmark.py                                   # default model, all tasks
    python tools/benchmark.py --models qwen2.5-coder:7b,qwen3:8b --tasks fix,optimize,docs --limit 20

What it measures (for each model):
  fix       Correct programs from bench/programs are broken on purpose (mutations below). For each
            broken program: does the model's corrected code compile on the first attempt? after the
            retries? and does the corrected program print/return exactly what the ORIGINAL did?
  optimize  For each correct program: is the optimised version verified (compiles, same behaviour)?
            How much smaller is the IR, and how many fewer VM steps does it take?
  docs      For each correct program: are the comments verified (code tokens unchanged)?
  bugs      For each correct program with one injected runtime bug: is it reported as a real bug,
            and does the proposed fix run without errors?
"caught" counts first answers that were wrong and would have reached the student without the check.

Results go to bench/results/<model>_<time>.csv plus a Markdown summary printed and saved.
"""
import argparse
import csv
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_assistant.ai_features import AIFeatures                     # noqa: E402
from ai_assistant.context_builder import check_source                # noqa: E402
from ai_assistant.ir_interpreter import run_program                  # noqa: E402
from ai_assistant.llm_client import OllamaClient, LLMError           # noqa: E402


# ------------------------------------------------------------------ mutations
def _lines(src):
    return src.split("\n")


def drop_semicolon(src, rng):
    cands = [i for i, l in enumerate(_lines(src)) if l.rstrip().endswith(";") and not l.strip().startswith(("for", "//"))]
    if not cands: return None
    i = rng.choice(cands); ls = _lines(src); ls[i] = ls[i].rstrip()[:-1]
    return "\n".join(ls), f"removed ';' at end of line {i + 1}"


def misspell_variable(src, rng):
    check = check_source(src)
    uses = [t for t in check["tokens"] if t.kind == "IDENTIFIER" and t.value not in check["call_graph"]["functions"] and len(t.value) > 2]
    if not uses: return None
    t = rng.choice(uses)
    bad = t.value[:-1] if rng.random() < .5 else t.value[0] + t.value[2:]
    ls = _lines(src); l = ls[t.line - 1]
    ls[t.line - 1] = l[:t.column - 1] + bad + l[t.column - 1 + len(t.value):]
    return "\n".join(ls), f"misspelled '{t.value}' as '{bad}' on line {t.line}"


def remove_close_paren(src, rng):
    cands = [i for i, l in enumerate(_lines(src)) if re.search(r"^\s*(if|while)\s*\(.*\)\s*\{", l)]
    if not cands: return None
    i = rng.choice(cands); ls = _lines(src)
    ls[i] = re.sub(r"\)\s*\{\s*$", " {", ls[i])
    return "\n".join(ls), f"removed ')' before '{{' on line {i + 1}"


def plus_plus(src, rng):
    m = list(re.finditer(r"(\b(\w+) = \2 \+ 1)\)", src))
    if not m: return None
    x = rng.choice(m)
    return src[:x.start(1)] + f"{x.group(2)}++" + src[x.end(1):], f"wrote {x.group(2)}++ in a for loop"


def drop_brace(src, rng):
    ls = _lines(src)
    cands = [i for i, l in enumerate(ls[:-2]) if l.strip() == "}" and l.startswith(" ")]
    if not cands: return None
    i = rng.choice(cands); del ls[i]
    return "\n".join(ls), f"deleted the '}}' on line {i + 1}"


def wrong_arg_count(src, rng):
    m = list(re.finditer(r"\b(\w+)\(([^()]*,[^()]*)\)", src))
    m = [x for x in m if x.group(1) not in ("if", "while", "for", "print")]
    if not m: return None
    x = rng.choice(m)
    args = x.group(2).rsplit(",", 1)[0]
    return src[:x.start(2)] + args + src[x.end(2):], f"called {x.group(1)}() with one argument missing"


def remove_declaration(src, rng):
    ls = _lines(src)
    cands = [i for i, l in enumerate(ls) if re.match(r"^\s+int \w+;\s*$", l)]
    if not cands: return None
    i = rng.choice(cands); name = ls[i].split()[1].rstrip(";"); del ls[i]
    return "\n".join(ls), f"removed the declaration of '{name}'"


MUTATIONS = [drop_semicolon, misspell_variable, remove_close_paren, plus_plus, drop_brace, wrong_arg_count, remove_declaration]


def make_broken(programs, rng, per_program=3):
    out = []
    for path, src in programs:
        muts = MUTATIONS[:]
        rng.shuffle(muts)
        made = 0
        for m in muts:
            r = m(src, rng)
            if not r: continue
            broken, what = r
            if broken != src and not check_source(broken)["ok"]:
                out.append({"program": path.name, "mutation": m.__name__, "what": what, "original": src, "broken": broken})
                made += 1
            if made >= per_program: break
    return out


def inject_bug(src, rng):
    """A runtime bug the VM can confirm: divide by a variable that becomes 0 just before."""
    ls = _lines(src)
    idx = next((i for i, l in enumerate(ls) if re.match(r"^\s+return 0;\s*$", l)), None)
    if idx is None: return None
    ind = re.match(r"^(\s*)", ls[idx]).group(1)
    ls[idx:idx] = [f"{ind}int zero = 0;", f"{ind}print(100 / zero);"]
    return "\n".join(ls)


def behaviour(src):
    c = check_source(src)
    if not c["ok"]: return None
    r = run_program(c["ir"], max_steps=500_000)
    return (r["return_value"], r["stdout"], r["error_kind"])


# ----------------------------------------------------------------------- run
def run_model(model, tasks, programs, broken, buggy, host, timeout):
    ai = AIFeatures(OllamaClient(model=model, host=host, timeout=timeout))
    rows = []

    def timed(fn, *a, **k):
        t0 = time.perf_counter()
        try:
            return fn(*a, **k), time.perf_counter() - t0, None
        except LLMError as exc:
            return None, time.perf_counter() - t0, str(exc)

    if "fix" in tasks:
        for i, b in enumerate(broken, 1):
            rep, secs, err = timed(ai.suggest_fixes, b["broken"])
            row = {"model": model, "task": "fix", "item": f"{b['program']}: {b['what']}", "seconds": round(secs, 1), "error": err}
            if rep and "result" in rep:
                v = rep["verification"]
                fixed = rep["result"].get("fixed_code", "")
                row.update(verified=v["passed"], first_try=v["passed"] and v["attempts"] == 1, attempts=v["attempts"],
                           caught=not (v["passed"] and v["attempts"] == 1),
                           behaviour_match=v["passed"] and behaviour(fixed) == behaviour(b["original"]))
            rows.append(row); _progress(model, "fix", i, len(broken), row)
    for task in ("optimize", "docs", "bugs"):
        if task not in tasks: continue
        items = buggy if task == "bugs" else programs
        for i, (path, src) in enumerate(items, 1):
            fn = {"optimize": ai.suggest_optimizations, "docs": ai.document_code, "bugs": ai.detect_bugs}[task]
            rep, secs, err = timed(fn, src)
            row = {"model": model, "task": task, "item": path.name, "seconds": round(secs, 1), "error": err or (rep or {}).get("ai_error")}
            if rep and rep.get("verification"):
                v = rep["verification"]
                row.update(verified=v["passed"], first_try=v["passed"] and v["attempts"] == 1, attempts=v["attempts"],
                           caught=not (v["passed"] and v["attempts"] == 1))
                if task == "optimize" and v["passed"]:
                    o, n = check_source(src), check_source(rep["result"]["optimized_code"])
                    so, sn = run_program(o["ir"])["steps"], run_program(n["ir"])["steps"]
                    row.update(ir_reduction=round(100 * (len(o["ir"]) - len(n["ir"])) / max(1, len(o["ir"])), 1),
                               step_reduction=round(100 * (so - sn) / max(1, so), 1))
                if task == "bugs":
                    verdicts = {f["id"]: f.get("verdict") for f in rep["result"].get("findings", [])}
                    target = [f for f in rep["findings"] if f["confirmed"] and f["rule"] in ("possible_div_zero", "runtime_error")]
                    row.update(bug_reported=bool(target) and all(verdicts.get(f["id"]) in ("real_bug", "likely_bug") for f in target),
                               fix_runs=bool(rep["result"].get("fixed_code")) and v["passed"])
            rows.append(row); _progress(model, task, i, len(items), row)
    return rows


def _progress(model, task, i, n, row):
    status = "ERR" if row.get("error") and "verified" not in row else ("ok" if row.get("verified") else "x")
    print(f"  [{model}] {task:<8} {i:>2}/{n}  {status:<3} {row['seconds']:>6.1f}s  {row['item'][:70]}", flush=True)


def pct(rows, key):
    vals = [r.get(key) for r in rows if key in r]
    return f"{100 * sum(bool(v) for v in vals) / len(vals):.0f}%" if vals else "-"


def avg(rows, key):
    vals = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
    return f"{sum(vals) / len(vals):.1f}" if vals else "-"


def summary(rows, models, tasks):
    out = ["| Model | Task | Items | First try | After retries | Caught by compiler | Extra | Avg attempts | Avg seconds |",
           "|---|---|---|---|---|---|---|---|---|"]
    for m in models:
        for t in tasks:
            rs = [r for r in rows if r["model"] == m and r["task"] == t]
            if not rs: continue
            extra = {"fix": f"same behaviour as original: {pct(rs, 'behaviour_match')}",
                     "optimize": f"IR -{avg(rs, 'ir_reduction')}%, steps -{avg(rs, 'step_reduction')}%",
                     "docs": "", "bugs": f"bug reported: {pct(rs, 'bug_reported')}, fix runs: {pct(rs, 'fix_runs')}"}[t]
            errors = sum(1 for r in rs if r.get("error") and "verified" not in r)
            out.append(f"| {m} | {t} | {len(rs)}{f' ({errors} errors)' if errors else ''} | {pct(rs, 'first_try')} | {pct(rs, 'verified')} | "
                       f"{pct(rs, 'caught')} | {extra} | {avg(rs, 'attempts')} | {avg(rs, 'seconds')} |")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", default=None, help="comma-separated Ollama models (default: $OLLAMA_MODEL or qwen2.5-coder:7b)")
    ap.add_argument("--tasks", default="fix,optimize,docs,bugs")
    ap.add_argument("--limit", type=int, default=30, help="maximum broken programs for the fix task")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--programs", default=str(ROOT / "bench" / "programs"))
    ap.add_argument("--ollama-host", default=None)
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    programs = [(p, p.read_text(encoding="utf-8")) for p in sorted(Path(args.programs).glob("*.c"))]
    programs = [(p, s) for p, s in programs if check_source(s)["ok"]]
    broken = make_broken(programs, rng)[:args.limit]
    buggy = [(p, b) for p, s in programs if (b := inject_bug(s, rng)) and check_source(b)["ok"]]
    tasks = [t.strip() for t in args.tasks.split(",") if t.strip()]
    models = [m.strip() for m in (args.models or OllamaClient().model).split(",")]
    print(f"{len(programs)} programs, {len(broken)} broken variants, {len(buggy)} programs with an injected bug; tasks: {', '.join(tasks)}")

    out_dir = ROOT / "bench" / "results"; out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    all_rows = []
    for m in models:
        try:
            OllamaClient(model=m, host=args.ollama_host).health_check()
        except LLMError as exc:
            print(f"Skipping {m}: {exc}"); continue
        rows = run_model(m, tasks, programs, broken, buggy, args.ollama_host, args.timeout)
        all_rows += rows
        keys = sorted({k for r in rows for k in r})
        path = out_dir / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', m)}_{stamp}.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=keys); w.writeheader(); w.writerows(rows)
        print(f"  saved {path}")
    if all_rows:
        table = summary(all_rows, models, tasks)
        print("\n" + table)
        (out_dir / f"summary_{stamp}.md").write_text(f"# Benchmark {stamp}\n\n{table}\n", encoding="utf-8")


if __name__ == "__main__":
    main()
