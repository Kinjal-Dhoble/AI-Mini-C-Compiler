"""Target-code back-end: turns the three-address IR into an executable program.

Two build modes:
  native  - IR -> portable C (one C statement per IR instruction, gotos for jumps) -> compiled
            by gcc / clang / tcc into a real executable (.exe on Windows).
  vm      - no C compiler found: write a standalone Python script that embeds the IR and the
            MiniC virtual machine. Runs anywhere Python runs.

The C translation is deliberately literal so students can line it up with the IR: every IR
instruction becomes exactly one C line, and each line is tagged with its IR index and source line.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = os.name == "nt"
EXE_SUFFIX = ".exe" if IS_WINDOWS else ""
WINDOWS_GCC_PATHS = [
    r"C:\msys64\ucrt64\bin\gcc.exe", r"C:\msys64\mingw64\bin\gcc.exe", r"C:\msys64\clang64\bin\clang.exe",
    r"C:\mingw64\bin\gcc.exe", r"C:\MinGW\bin\gcc.exe", r"C:\TDM-GCC-64\bin\gcc.exe",
    r"C:\Program Files\LLVM\bin\clang.exe", r"C:\tcc\tcc.exe",
]
BINOP_RE = re.compile(r"^(\w+) = (\S+) (\S+) (\S+)$")
CALL_RE = re.compile(r"^(?:(\w+) = )?CALL (\w+), (\d+)$")
TEMP_RE = re.compile(r"^t\d+$")


# ------------------------------------------------------------------ IR -> C
C_TYPE = {"char": "char", "short": "short", "int": "int", "long": "long long", "float": "float", "double": "double"}

# Runtime helpers for the generated C. They check the same mistakes as the MiniC VM (array index
# out of bounds, strings without '\0', strcpy/strcat/scanf("%s") overflowing an array, division
# by zero), so a native build stops with the same message instead of corrupting memory.
C_PRELUDE = r"""#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

static void mc_fail(const char *msg, const char *a, long long x, long long y) {
    fflush(stdout);
    fprintf(stderr, "runtime error: ");
    fprintf(stderr, msg, a, x, y, y - 1);
    fprintf(stderr, "\n");
    exit(1);
}

static void mc_bad_input(int r, const char *what) {
    char w[64] = "";
    fflush(stdout);
    if (r == 0 && scanf("%63s", w) == 1)
        fprintf(stderr, "runtime error: scanf expected %s but the input was '%s'\n", what, w);
    else
        fprintf(stderr, "runtime error: the program asked for %s (scanf) but there is no more input\n", what);
    exit(1);
}
static int mc_read_int(void) {   /* scanf("%d", &x) */
    int v, r;
    fflush(stdout);                 /* show any prompt before waiting */
    if ((r = scanf("%d", &v)) != 1) mc_bad_input(r, "a whole number");
    return v;
}
static long long mc_read_long(void) { long long v; int r; fflush(stdout); if ((r = scanf("%lld", &v)) != 1) mc_bad_input(r, "a whole number"); return v; }
static double mc_read_double(void) { double v; int r; fflush(stdout); if ((r = scanf("%lf", &v)) != 1) mc_bad_input(r, "a number"); return v; }
static int mc_read_char(int skip_space) { char c; fflush(stdout); if (scanf(skip_space ? " %c" : "%c", &c) != 1) mc_bad_input(-1, "a character"); return c; }
static void mc_read_str(char *d, long long n, const char *name) {
    static char w[4096];
    fflush(stdout);
    if (scanf("%4095s", w) != 1) mc_bad_input(-1, "a word");
    if ((long long)strlen(w) + 1 > n) mc_fail("scanf(\"%%s\") needs more places than %s has (%lld): it writes past the end of the array (buffer overflow)", name, n, n);
    strcpy(d, w);
}

static int mc_div_zero(void) {
    fflush(stdout);
    fprintf(stderr, "runtime error: division by zero\n");
    exit(1);
}
static double mc_fdiv(double a, double b) { if (b == 0) mc_div_zero(); return a / b; }

static long long mc_ix(long long i, long long n, const char *name) {
    if (i < 0 || i >= n)
        mc_fail("array index out of bounds: %s[...] uses index %lld, but that dimension has %lld elements (valid indexes are 0 to %lld)", name, i, n);
    return i;
}

static int mc_len(const char *s, long long n, const char *fn, const char *name) {
    long long k;
    for (k = 0; k < n; k++) if (s[k] == 0) return (int)k;
    fflush(stdout);
    fprintf(stderr, "runtime error: %s went past the end of %s (%lld chars) without finding the '\\0' that ends a string\n", fn, name, n);
    exit(1);
}
static void mc_room(long long need, long long n, const char *fn, const char *name) {
    if (need > n) {
        fflush(stdout);
        fprintf(stderr, "runtime error: %s() needs %lld places but %s only has %lld: it writes past the end of the array (buffer overflow)\n", fn, need, name, n);
        exit(1);
    }
}
static const char *mc_cstr(const char *s, long long n, const char *name) { mc_len(s, n, "printf(\"%s\")", name); return s; }
static int mc_strlen(const char *s, long long n, const char *a) { return mc_len(s, n, "strlen()", a); }
static void mc_strcpy(char *d, long long dn, const char *a, const char *s, long long sn, const char *b) {
    int l = mc_len(s, sn, "strcpy()", b); mc_room(l + 1, dn, "strcpy()", a); memmove(d, s, l + 1);
}
static void mc_strncpy(char *d, long long dn, const char *a, const char *s, long long sn, const char *b, int k) {
    int i, l; mc_room(k, dn, "strncpy()", a);
    for (l = 0; l < k && l < sn && s[l]; l++) ;
    if (l < k && l >= sn) mc_len(s, sn, "strncpy()", b);
    for (i = 0; i < k; i++) d[i] = i < l ? s[i] : 0;
}
static void mc_strcat(char *d, long long dn, const char *a, const char *s, long long sn, const char *b, int k) {
    int old = mc_len(d, dn, "strcat()", a), add = mc_len(s, sn, "strcat()", b);
    if (k >= 0 && add > k) add = k;
    mc_room(old + add + 1, dn, k >= 0 ? "strncat" : "strcat()", a);
    memmove(d + old, s, add); d[old + add] = 0;
}
static int mc_strncmp(const char *s, long long sn, const char *a, const char *t, long long tn, const char *b, int k) {
    int i, ls = mc_len(s, sn, k >= 0 ? "strncmp()" : "strcmp()", a), lt = mc_len(t, tn, k >= 0 ? "strncmp()" : "strcmp()", b);
    for (i = 0; k < 0 || i < k; i++) {
        int x = i <= ls ? (unsigned char)s[i] : 0, y = i <= lt ? (unsigned char)t[i] : 0;
        if (x != y || x == 0) return x - y;
    }
    return 0;
}
static int mc_strcmp(const char *s, long long sn, const char *a, const char *t, long long tn, const char *b) { return mc_strncmp(s, sn, a, t, tn, b, -1); }
static void mc_puts(const char *s, long long n, const char *a) { mc_len(s, n, "puts()", a); puts(s); }
static int mc_toupper(int c) { return c >= 'a' && c <= 'z' ? c - 32 : c; }
static int mc_tolower(int c) { return c >= 'A' && c <= 'Z' ? c + 32 : c; }
static int mc_isupper(int c) { return c >= 'A' && c <= 'Z'; }
static int mc_islower(int c) { return c >= 'a' && c <= 'z'; }
static int mc_isalpha(int c) { return mc_isupper(c) || mc_islower(c); }
static int mc_isdigit(int c) { return c >= '0' && c <= '9'; }
static int mc_isalnum(int c) { return mc_isalpha(c) || mc_isdigit(c); }
static int mc_isspace(int c) { return c == ' ' || (c >= 9 && c <= 13); }
static int mc_abs(int x) { return x < 0 ? (int)(0u - (unsigned)x) : x; }
static double mc_sqrt(double x) { if (x < 0) { fflush(stdout); fprintf(stderr, "runtime error: sqrt() of %g has no real-number answer\n", x); exit(1); } return sqrt(x); }
static double mc_pow(double x, double y) { if (x < 0 && floor(y) != y) { fflush(stdout); fprintf(stderr, "runtime error: pow() of %g, %g has no real-number answer\n", x, y); exit(1); } return pow(x, y); }
static double mc_fabs(double x) { return fabs(x); }
static double mc_floor(double x) { return floor(x); }
static double mc_ceil(double x) { return ceil(x); }
"""

STRING_FUNCS = {"strlen": 1, "strcpy": 2, "strncpy": 2, "strcat": 2, "strncat": 2, "strcmp": 2, "strncmp": 2, "puts": 1}
LOAD_RE = re.compile(r"^(\w+) = (\w+)((?:\[[^\]\s]+\])+)$")
STORE_AT_RE = re.compile(r"^(\w+)((?:\[[^\]\s]+\])+) = (\S+)$")
CAST_RE = re.compile(r"^(\w+) = \((\w+)\) (\S+)$")
ADDR_RE = re.compile(r"^(\w+) = ADDR_(EQ|NE) (.*)$")
INDEX_RE = re.compile(r"\[([^\]\s]+)\]")


def _c_string(text):
    """A JSON string literal from the IR as a C string literal."""
    s = json.loads(text)
    out = []
    for ch in s:
        o = ord(ch)
        out.append({"\n": "\\n", "\t": "\\t", "\r": "\\r", "\\": "\\\\", '"': '\\"', "\0": "\\0", "\a": "\\a"}.get(ch)
                   or (ch if 32 <= o < 127 else "\\%03o" % (o & 0xFF)))
    return '"' + "".join(out) + '"'


def ir_to_c(ir, line_map=None, source_name="program.c"):
    """Translate IR to C: one C statement per IR instruction, one C function per Mini C function.
    Mini C function f becomes mc_f; top-level code becomes mc_init(), run before main.
    Arrays become flat C arrays (m[3][4] -> 12 elements); an array parameter is a pointer plus
    its length, so every access can be checked."""
    from ai_assistant.ir_interpreter import split_functions, split_operands
    top, funcs = split_functions(ir)
    global_names = sorted(n for n in top.types if not TEMP_RE.match(n))
    func_names = list(funcs)

    def nparams(fn):
        idx = [int(ins.split(", ")[1]) for _, ins in fn.code if ins.startswith("GET_PARAM ")]
        return max(idx) + 1 if idx else 0

    def param_names(fn):
        return {int(ins.split(", ")[1]): ins[10:].split(", ")[0] for _, ins in fn.code if ins.startswith("GET_PARAM ")}

    def signature(fn):
        names, parts = param_names(fn), []
        for k in range(nparams(fn)):
            base, dims = fn.types.get(names.get(k), ("int", ()))
            parts.append(f"{C_TYPE[base]} *p{k}, long long p{k}_n" if dims else f"{C_TYPE[base]} p{k}")
        ret = C_TYPE.get(fn.result_type, "int")
        return f"static {ret} mc_{fn.name}({', '.join(parts) or 'void'})"

    def count(dims):
        n = 1
        for d in dims:
            n *= d or 1
        return n

    out = [f"/* Generated by the MiniC compiler back-end from {source_name}.",
           " * Each line inside a function is one IR instruction: [IR index | source line]. */",
           C_PRELUDE]
    for g in global_names:
        base, dims = top.types[g]
        out.append(f"{C_TYPE[base]} v_{g}" + (f"[{count(dims)}]" if dims else " = 0") + ";   /* global variable */")
    for name in func_names:
        out.append(signature(funcs[name]) + ";")
    out.append("")

    def body(fn, header):
        is_top = fn is top
        info = {}                                           # name -> (base, dims, is_param)
        for g in global_names:
            info[g] = top.types[g] + (False,)
        params = set(param_names(fn).values())
        for name, (base, dims) in fn.types.items():
            if not is_top or TEMP_RE.match(name):
                info[name] = (base, dims, name in params)
        local_vars = sorted(v for v in fn.locals if not TEMP_RE.match(v))
        temps = sorted((v for v in fn.locals if TEMP_RE.match(v)), key=lambda t: int(t[1:]))
        lines = [header]
        decl = {}
        for v in local_vars + temps:
            base, dims, is_param = info.get(v, ("int", (), False))
            ct = C_TYPE[base]
            if dims and is_param:
                decl.setdefault(ct, []).append(f"*v_{v} = 0")
                decl.setdefault("long long", []).append(f"v_{v}_n = 0")
            elif dims:
                lines.append(f"    {ct} v_{v}[{count(dims)}] = {{0}};   /* local array */")
            else:
                decl.setdefault(ct, []).append(f"v_{v} = 0")
        for ct, names in decl.items():
            lines.append(f"    {ct} " + ", ".join(names) + ";")
        pending = []

        def arr(name):
            """(pointer expression, length expression, dims) of an array variable."""
            base, dims, is_param = info.get(name, ("int", (), False))
            return f"v_{name}", (f"v_{name}_n" if is_param else str(count(dims))), dims, base

        def op(x):
            if re.fullmatch(r"-?\d+", x):
                return x
            if re.fullmatch(r"-?(?:\d+\.\d*(?:[eE][+-]?\d+)?|\d+[eE][+-]?\d+)", x):
                return x
            if x[:1] == '"':
                return _c_string(x)
            if "[" in x:
                return place(x)[0]
            return f"v_{x}"

        def place(text):
            """name[i][j] -> (C expression, length of the row if it is a partial index, base)."""
            name = text[:text.index("[")]
            idx = INDEX_RE.findall(text)
            ptr, length, dims, base = arr(name)
            inner = count(dims[1:])
            sizes = [f"({length}) / {inner}" if dims[0] is None else str(dims[0])] + [str(d) for d in dims[1:]]
            terms = []
            stride = count(dims[1:])
            for k, i in enumerate(idx):
                terms.append(f"mc_ix({op(i)}, {sizes[k]}, \"{name}\")" + (f" * {stride}" if stride != 1 else ""))
                stride = stride // (dims[k + 1] or 1) if k + 1 < len(dims) else 1
            flat = " + ".join(terms)
            if len(idx) < len(dims):
                return f"({ptr} + {flat})", str(count(dims[len(idx):])), base
            return f"{ptr}[{flat}]", None, base

        def array_arg(x):
            """(pointer, length, label) when x denotes an array, else None."""
            if x[:1] == '"':
                label = "the text " + re.sub(r"[^A-Za-z0-9 _.,!?-]", "", json.loads(x))[:30]
                return f"(char *){_c_string(x)}", str(len(json.loads(x)) + 1), label
            if "[" in x:
                p, length, _ = place(x)
                return (p, length, x[:x.index("[")]) if length else None
            if x in info and info[x][1]:
                ptr, length, _, _ = arr(x)
                return ptr, length, x
            return None

        def vtype(x):
            return info.get(x, ("int", (), False))[0]

        for i, ins in fn.code:
            tag = f"/* [{i:>3} | line {line_map[i] if line_map and i < len(line_map) else '?':>3}] {ins} */"
            c = None
            if ins.startswith("LABEL "):
                c = f"{ins[6:].rstrip(':')}: ;"
            elif ins.startswith(("DECLARE ", "RESULT_TYPE ", "FALLTHROUGH ")):
                c = ";"
            elif ins.startswith("CONST "):
                t, n = ins[6:].split(", "); c = f"v_{t} = {n};"
            elif ins.startswith("STORE "):
                name, src = ins[6:].split(", "); c = f"v_{name} = {op(src)};"
            elif ins.startswith("ARRAY_INIT "):
                name, values = ins[11:].split(", ", 1)
                vals = json.loads(values)
                ptr, length, _, base = arr(name)
                if vals:
                    c = (f"{{ static const {C_TYPE[base]} init[] = {{{', '.join(repr(v) for v in vals)}}}; long long k; "
                         f"for (k = 0; k < {length}; k++) {ptr}[k] = k < {len(vals)} ? init[k] : 0; }}")
                else:
                    c = f"memset({ptr}, 0, sizeof({ptr}));"
            elif ins.startswith("GET_PARAM "):
                name, k = ins[10:].split(", ")
                c = f"v_{name} = p{k}; v_{name}_n = p{k}_n;" if info.get(name, ("", ()))[1] else f"v_{name} = p{k};"
            elif ins.startswith("PARAM "):
                x = ins[6:].strip()
                a = array_arg(x)
                pending.append({"c": a[0] if a else op(x), "n": a[1] if a else None, "label": a[2] if a else x})
                c = ";"
            elif ins.startswith("WRITE_STR "):
                c = f"fputs({_c_string(ins[10:])}, stdout);"
            elif ins.startswith("READ_INT "):
                c = f"v_{ins[9:].strip()} = mc_read_int();"
            elif ins.startswith("READ "):
                spec, target = split_operands(ins[5:])
                spec = json.loads(spec)
                conv = spec.strip()
                if conv == "%s":
                    a = array_arg(target)
                    c = f"mc_read_str({a[0]}, {a[1]}, \"{a[2]}\");"
                else:
                    dest = place(target)[0] if "[" in target else f"v_{target}"
                    fn_name = {"%c": f"mc_read_char({int(spec.startswith(' '))})", "%ld": "mc_read_long()",
                               "%d": "mc_read_int()", "%i": "mc_read_int()", "%hd": "mc_read_int()"}.get(conv, "mc_read_double()")
                    c = f"{dest} = {fn_name};"
            elif ins.startswith("WRITE_INT "):
                c = f'printf("%d", {op(ins[10:].strip())});'
            elif ins.startswith("WRITE_FMT "):
                spec, x = split_operands(ins[10:])
                spec = json.loads(spec)
                conv = spec[-1]
                if conv == "s":
                    a = array_arg(x)
                    c = f"printf({json.dumps(spec)}, mc_cstr({a[0]}, {a[1]}, \"{a[2]}\"));"
                else:
                    cspec = re.sub(r"l(?=[diuxXo])", "ll", spec) if "ll" not in spec else spec
                    cast = "(double)" if conv in "fFeEgG" else "(long long)" if "ll" in cspec else "(int)"
                    c = f"printf({json.dumps(cspec)}, {cast}{op(x)});"
            elif ins.startswith("PRINT "):
                c = f'printf("%d\\n", {op(ins[6:].strip())});'
            elif ins.startswith("IF_FALSE "):
                cond, _, label = ins[9:].split(" "); c = f"if (!{op(cond)}) goto {label};"
            elif ins.startswith("GOTO "):
                c = f"goto {ins[5:]};"
            elif ins.startswith("RETURN"):
                rest = ins[6:].strip(); c = f"return {op(rest) if rest else 0};"
            elif CALL_RE.match(ins):
                dest, name, n = CALL_RE.match(ins).groups(); n = int(n)
                args = pending[len(pending) - n:] if n else []
                del pending[len(pending) - n:]
                if name not in funcs and name in STRING_FUNCS:
                    parts = []
                    for a in args[:STRING_FUNCS[name]]:
                        parts += [a["c"], a["n"] or "0", f"\"{a['label']}\""]
                    parts += [a["c"] for a in args[STRING_FUNCS[name]:]]
                    if name == "strcat":
                        parts.append("-1")
                    call = f"mc_{'strcat' if name == 'strncat' else name}({', '.join(parts)})"
                elif name not in funcs and name == "putchar":
                    call = f"putchar({args[0]['c']})"
                elif name not in funcs:
                    call = f"mc_{name}({', '.join(a['c'] for a in args)})"
                else:
                    call = f"mc_{name}({', '.join(a['c'] + (', ' + a['n'] if a['n'] else '') for a in args)})"
                c = f"v_{dest} = {call};" if dest else f"{call};"
            elif ins.startswith("t") and CAST_RE.match(ins):
                t, typ, src = CAST_RE.match(ins).groups()
                c = f"v_{t} = ({C_TYPE[typ]}){op(src)};"
            elif ADDR_RE.match(ins):
                t, kind, rest = ADDR_RE.match(ins).groups()
                a, b = [array_arg(x) for x in split_operands(rest)]
                c = f"v_{t} = (void *){a[0]} {'==' if kind == 'EQ' else '!='} (void *){b[0]};"
            elif LOAD_RE.match(ins):
                t = ins.split(" = ")[0]
                c = f"v_{t} = {place(ins.split(' = ', 1)[1])[0]};"
            elif STORE_AT_RE.match(ins):
                target, src = ins.split(" = ", 1)
                c = f"{place(target)[0]} = {op(src)};"
            else:
                m = BINOP_RE.match(ins)
                if not m:
                    raise ValueError(f"cannot translate IR instruction: {ins}")
                t, a, o, b = m.groups()
                if o == "/" and vtype(t) in ("float", "double"):
                    c = f"v_{t} = mc_fdiv({op(a)}, {op(b)});"
                elif o in ("/", "%"):
                    c = f"v_{t} = {op(b)} == 0 ? mc_div_zero() : {op(a)} {o} {op(b)};"
                else:
                    c = f"v_{t} = {op(a)} {o} {op(b)};"
            lines.append(f"    {c:<44} {tag}")
        lines += ["    return 0;", "}", ""]
        return lines

    out += body(top, "static void mc_init(void) {   /* top-level code: initialises globals */")
    out[-3] = "    return;"
    for name in func_names:
        out += body(funcs[name], signature(funcs[name]) + " {")
    main_call = "mc_main()" if "main" in funcs and nparams(funcs["main"]) == 0 else ("mc_main(" + ", ".join("0" for _ in range(nparams(funcs["main"]))) + ")" if "main" in funcs else "0")
    out += ["int main(void) {", "    mc_init();", f"    return (int){main_call};", "}", ""]
    return "\n".join(out)


# ------------------------------------------------------------ toolchain
def find_c_compiler():
    """Return (path, family) or (None, None). MINIC_CC overrides the search."""
    candidates = [os.environ.get("MINIC_CC")] if os.environ.get("MINIC_CC") else []
    candidates += ["gcc", "clang", "cc", "tcc"]
    for c in candidates:
        path = shutil.which(c) if c else None
        if path:
            return path, _family(path)
    if IS_WINDOWS:
        for p in WINDOWS_GCC_PATHS:
            if Path(p).exists():
                return p, _family(p)
    return None, None


def _family(path):
    name = Path(path).name.lower()
    return "tcc" if "tcc" in name else "clang" if "clang" in name else "gcc"


def toolchain_info():
    path, family = find_c_compiler()
    if not path:
        return {"mode": "vm", "compiler": None, "version": None}
    try:
        ver = subprocess.run([path, "--version"] if family != "tcc" else [path, "-v"],
                             capture_output=True, text=True, timeout=10)
        version = (ver.stdout or ver.stderr).strip().splitlines()[0]
    except (OSError, subprocess.SubprocessError, IndexError):
        version = family
    return {"mode": "native", "compiler": path, "family": family, "version": version}


# ------------------------------------------------------------------ build
def build(ir, line_map, out_dir, name, source_name="program.c", optimize=False):
    """Build an executable. Returns dict with mode, exe, c_source, log, ok."""
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9_-]", "_", name) or "program"
    c_code = ir_to_c(ir, line_map, source_name)
    c_path = out_dir / f"{stem}.c"
    c_path.write_text(c_code, encoding="utf-8")
    cc, family = find_c_compiler()
    if cc:
        exe = out_dir / f"{stem}{EXE_SUFFIX}"
        cmd = [cc, "-O2" if optimize else "-O0"]
        if family in ("gcc", "clang"):
            cmd += ["-fwrapv", "-fsigned-char", "-w"]
        cmd += ["-o", str(exe), str(c_path)]
        if family != "tcc" or not IS_WINDOWS:
            cmd.append("-lm")                       # sqrt, pow, ... live in the maths library
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "mode": "native", "exe": None, "c_source": c_code,
                    "c_path": str(c_path), "command": cmd, "log": f"could not run the C compiler: {exc}"}
        return {"ok": p.returncode == 0 and exe.exists(), "mode": "native", "exe": str(exe),
                "c_source": c_code, "c_path": str(c_path), "command": cmd,
                "log": (p.stdout + p.stderr).strip()}
    script = out_dir / f"{stem}.py"
    script.write_text(_vm_script(ir, source_name), encoding="utf-8")
    return {"ok": True, "mode": "vm", "exe": str(script), "c_source": c_code, "c_path": str(c_path),
            "command": None, "log": "No C compiler found: built a portable MiniC VM program instead."}


def assembly(c_path):
    """Assembly the C compiler produces for the generated C (for the IDE's Assembly view)."""
    cc, family = find_c_compiler()
    if not cc or family == "tcc":
        return None
    s_path = Path(c_path).with_suffix(".s")
    base = [cc, "-S", "-O1", "-w", "-fno-asynchronous-unwind-tables", "-o", str(s_path), str(c_path)]
    for extra in (["-masm=intel"], []):
        try:
            p = subprocess.run(base[:2] + extra + base[2:], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            return None
        if p.returncode == 0 and s_path.exists():
            return s_path.read_text(encoding="utf-8", errors="replace")
    return None


def run_executable(build_result, timeout=5, stdin=""):
    """Run what build() produced with `stdin` as its input. Returns stdout, stderr, exit_code,
    timed_out, seconds."""
    import time
    exe = build_result["exe"]
    cmd = [sys.executable, exe] if build_result["mode"] == "vm" else [exe]
    start = time.perf_counter()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout, input=stdin or "")
        return {"stdout": p.stdout, "stderr": p.stderr, "exit_code": p.returncode,
                "timed_out": False, "seconds": time.perf_counter() - start, "command": cmd}
    except subprocess.TimeoutExpired as exc:
        return {"stdout": exc.stdout or "", "stderr": exc.stderr or "", "exit_code": None,
                "timed_out": True, "seconds": timeout, "command": cmd}


def _vm_script(ir, source_name):
    vm_src = (Path(__file__).resolve().parent.parent / "ai_assistant" / "ir_interpreter.py").read_text(encoding="utf-8")
    return (f"# MiniC VM executable built from {source_name}. Run: python {Path(source_name).stem}.py\n"
            + vm_src + "\n\nIR = " + json.dumps(ir, indent=1) + "\n\n"
            "if __name__ == '__main__':\n"
            "    import sys as _sys\n"
            "    def _next():\n"
            "        _sys.stdout.flush()               # show a prompt before waiting for input\n"
            "        line = _sys.stdin.readline()\n"
            "        return line if line else None\n"
            "    r = run_program(IR, stdin=_next, on_output=_sys.stdout.write)\n"
            "    _sys.stdout.flush()\n"
            "    if r['error']:\n"
            "        print('runtime error: ' + r['error'], file=_sys.stderr)\n"
            "        _sys.exit(1)\n"
            "    _sys.exit((r['return_value'] or 0) & 0xFF if _sys.platform != 'win32' else (r['return_value'] or 0))\n")
