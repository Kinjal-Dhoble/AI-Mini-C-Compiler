"""Differential testing: does our compiler agree with a real C compiler?

    python tools/difftest.py                       # bench/programs and workspace
    python tools/difftest.py my_program.c other.c

Every Mini C program is (almost) valid C. For each program this tool runs it three ways:
  1. the MiniC virtual machine (interpreting our IR)
  2. our compiler's native build (IR -> C -> gcc)
  3. plain gcc on the ORIGINAL source, as normal C
and checks that all three print exactly the same text and return the same exit code.
`print(x);` is mapped to printf("%d\\n", x) for step 3 with a one-line macro.

(This tool found that early versions evaluated both sides of && and ||; they now short-circuit like C.)
Requires gcc or clang on PATH (or MINIC_CC).
"""
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_assistant.context_builder import check_source   # noqa: E402
from ai_assistant.ir_interpreter import run_program, SAMPLE_INPUT     # noqa: E402
from compiler import backend                            # noqa: E402

PRELUDE = ('#include <stdio.h>\n#include <string.h>\n#include <ctype.h>\n#include <math.h>\n#include <stdlib.h>\n'
           '#define print(x) printf("%d\\n", (int)(x))\n')
# Mistakes the MiniC VM and our native build stop at, but plain C does not check: C keeps going
# with garbage (or crashes later). For these the agreement check is "our two builds both stop".
MEMORY_ERRORS = ("out_of_bounds", "buffer_overflow", "no_terminator", "uninit")
WINDOWS = os.name == "nt"


def exit_code(v):
    if v is None:
        return 0
    return v if WINDOWS else v & 0xFF


def run_exe(cmd, timeout=5, stdin=SAMPLE_INPUT):
    """Programs that use scanf get the same sample input in all three runs."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout, input=stdin)
        return p.stdout, p.returncode, None
    except subprocess.TimeoutExpired:
        return None, None, "timed out"


def gcc_reference(source, cc, family, workdir, name):
    src = Path(workdir) / f"{name}_ref.c"
    exe = Path(workdir) / (f"{name}_ref" + (".exe" if WINDOWS else ""))
    # keep line numbers aligned: the prelude goes on the same first line via a #line directive
    src.write_text(PRELUDE + "#line 1\n" + source, encoding="utf-8")
    flags = ["-O0", "-w"] + (["-fwrapv", "-fsigned-char"] if family in ("gcc", "clang") else [])
    p = subprocess.run([cc, *flags, "-o", str(exe), str(src), "-lm"], capture_output=True, text=True)
    if p.returncode != 0:
        return None, None, "gcc could not compile it as C: " + (p.stderr.strip().splitlines() or ["?"])[0]
    return run_exe([str(exe)])


def compare(path, cc, family, workdir):
    source = Path(path).read_text(encoding="utf-8")
    check = check_source(source)
    name = re.sub(r"\W", "_", Path(path).stem)
    if not check["ok"]:
        return {"file": str(path), "status": "skip", "why": "does not compile in Mini C"}
    if any(getattr(f, "name", "") == "main" and f.params for f in check["ast"].statements):
        return {"file": str(path), "status": "skip", "why": "main takes input"}
    vm = run_program(check["ir"], check["line_map"], max_steps=2_000_000, stdin=SAMPLE_INPUT)
    b = backend.build(check["ir"], check["line_map"], workdir, name, Path(path).name)
    if not b["ok"]:
        return {"file": str(path), "status": "MISMATCH", "why": "our back-end failed to build it: " + b["log"][:200]}
    nat_out, nat_code, nat_err = run_exe([b["exe"]] if b["mode"] == "native" else [sys.executable, b["exe"]])
    ref_out, ref_code, ref_err = gcc_reference(source, cc, family, workdir, name)
    results = {
        "vm": (vm["stdout"], exit_code(vm["return_value"]) if not vm["error"] else "error"),
        "minic": (nat_out, nat_code if nat_err is None else nat_err),
        "gcc": (ref_out, ref_code if ref_err is None else ref_err),
    }
    if ref_err and "could not compile" in ref_err:
        return {"file": str(path), "status": "skip", "why": ref_err, "results": results}
    crashed = vm["error"] is not None
    if crashed and vm["error_kind"] in MEMORY_ERRORS:
        stopped = nat_code not in (0, None) or vm["error_kind"] == "uninit"
        status = "checked" if stopped else "MISMATCH"
        why = (f"MiniC stops at a mistake plain C does not check: {vm['error']} (line {vm['error_line']}); "
               f"plain gcc exits with {ref_code} and prints {(ref_out or '')[:40]!r}")
        return {"file": str(path), "status": status, "why": why, "results": results}
    if crashed and vm["error_kind"] == "step_limit":       # a program that never ends: all three must hang
        same = nat_err == "timed out" and ref_err == "timed out"
        status = "match" if same else "MISMATCH"
        why = "never finishes: the VM stops at its step limit, the native and gcc builds both time out" if same else "runtime behaviour differs"
    elif crashed:
        same = nat_code not in (0, None) and ref_code not in (0, None)
        status = "match" if same else "MISMATCH"
        why = f"all three fail at runtime ({vm['error']})" if same else "runtime behaviour differs"
    else:
        same = results["vm"] == results["minic"] == results["gcc"]
        status = "match" if same else "MISMATCH"
        why = ""
        if not same:
            parts = [k for k in ("minic", "gcc") if results[k] != results["vm"]]
            why = "differs from the VM: " + ", ".join(parts)

    return {"file": str(path), "status": status, "why": why, "results": results}


def main():
    files = sys.argv[1:] or sorted(str(p) for d in ("bench/programs", "workspace") for p in (ROOT / d).glob("*.c"))
    cc, family = backend.find_c_compiler()
    if not cc or family == "tcc":
        print("Differential testing needs gcc or clang on PATH (or set MINIC_CC).")
        return 2
    print(f"Reference compiler: {backend.toolchain_info()['version']}\n")
    bad = 0
    with tempfile.TemporaryDirectory() as tmp:
        for f in files:
            r = compare(f, cc, family, tmp)
            mark = {"match": "ok  ", "MISMATCH": "FAIL", "skip": "skip", "checked": "safe"}[r["status"]]
            print(f"  {mark} {Path(f).relative_to(ROOT) if Path(f).is_absolute() and ROOT in Path(f).parents else f}"
                  + (f"  ({r['why']})" if r.get("why") else ""))
            if r["status"] == "MISMATCH":
                bad += 1
                for k, (out, code) in r["results"].items():
                    print(f"        {k:<6} exit {code}, output {(out or '')[:70]!r}")
    print(f"\n{'All programs agree with gcc.' if not bad else f'{bad} program(s) disagree.'}"
          + "\n'safe' = MiniC stopped at an array/string mistake that plain C silently runs past.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
