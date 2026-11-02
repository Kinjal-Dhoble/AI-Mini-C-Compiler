"""Build the flex + bison syntax checker from grammar/minic.l and grammar/minic.y.

    python tools/build_yacc.py            # -> build/yacc/minic_yacc(.exe)
    build/yacc/minic_yacc workspace/hello.c

Needs flex, bison and a C compiler on PATH. On Windows the WinFlexBison package provides
win_flex / win_bison (choco install winflexbison3, or the zip from GitHub), and gcc comes from
MSYS2 (pacman -S mingw-w64-ucrt-x86_64-gcc).
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from compiler.backend import find_c_compiler       # noqa: E402

GRAMMAR = ROOT / "grammar"
OUT = ROOT / "build" / "yacc"
EXE = OUT / ("minic_yacc.exe" if os.name == "nt" else "minic_yacc")


def tools():
    flex = shutil.which("flex") or shutil.which("win_flex")
    bison = shutil.which("bison") or shutil.which("win_bison")
    cc, family = find_c_compiler()
    return flex, bison, cc


def status():
    flex, bison, cc = tools()
    missing = [n for n, p in (("flex", flex), ("bison", bison), ("a C compiler", cc)) if not p]
    return {"available": not missing, "missing": missing, "built": EXE.exists(), "exe": str(EXE),
            "flex": flex, "bison": bison, "cc": cc}


def build(force=False):
    """Returns (ok, log). Rebuilds when the grammar files are newer than the executable."""
    flex, bison, cc = tools()
    if not (flex and bison and cc):
        return False, "Missing: " + ", ".join(n for n, p in (("flex", flex), ("bison", bison), ("C compiler", cc)) if not p)
    newest = max((GRAMMAR / "minic.l").stat().st_mtime, (GRAMMAR / "minic.y").stat().st_mtime)
    if EXE.exists() and not force and EXE.stat().st_mtime >= newest:
        return True, "up to date"
    OUT.mkdir(parents=True, exist_ok=True)
    steps = [[bison, "-d", "-o", str(OUT / "minic.tab.c"), str(GRAMMAR / "minic.y")],
             [flex, "-o", str(OUT / "lex.yy.c"), str(GRAMMAR / "minic.l")],
             [cc, "-w", "-O1", "-I", str(OUT), "-o", str(EXE), str(OUT / "minic.tab.c"), str(OUT / "lex.yy.c")]]
    log = []
    for cmd in steps:
        p = subprocess.run(cmd, capture_output=True, text=True)
        log.append("$ " + " ".join(Path(c).name if os.sep in c else c for c in cmd))
        if p.stdout.strip() or p.stderr.strip():
            log.append((p.stdout + p.stderr).strip())
        if p.returncode != 0:
            return False, "\n".join(log)
    return True, "\n".join(log)


def check(source_text):
    """Run the flex/bison checker on source text. Returns the parsed RESULT/FUNCTIONS/... lines."""
    import tempfile
    ok, log = build()
    if not ok:
        return {"ok": False, "log": log}
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from compiler.preprocess import preprocess
    pre_text, pre_errors = preprocess(source_text)      # #define / #undef, as both Python front ends do
    with tempfile.NamedTemporaryFile("w", suffix=".c", delete=False, encoding="utf-8") as fh:
        fh.write(pre_text)
        path = fh.name
    try:
        p = subprocess.run([str(EXE), path], capture_output=True, text=True, timeout=10)
    finally:
        os.unlink(path)
    res = {"ok": True, "errors": [], "raw": p.stdout}
    for line in p.stdout.splitlines():
        key, _, rest = line.partition(" ")
        if key == "ERROR":
            pos, _, msg = rest.partition(" ")
            l, _, c = pos.partition(":")
            res["errors"].append({"line": int(l or 0), "column": int(c or 0), "message": msg})
        elif key == "RESULT":
            res["accepted"] = rest.strip() == "accept"
        elif key == "FUNCTIONS":
            res["functions"] = [f for f in rest.strip().split(",") if f]
        elif key == "STATEMENTS":
            res["statements"] = int(rest)
    for e in pre_errors:                                # e.g. a function-like #define
        res["errors"].insert(0, {"line": e["line"], "column": e["column"], "message": e["message"]})
        res["accepted"] = False
    return res


if __name__ == "__main__":
    ok, log = build(force="--force" in sys.argv)
    print(log)
    print(f"\nBuilt {EXE}" if ok else "\nBuild failed.")
    sys.exit(0 if ok else 1)
