"""Virtual machine for the compiler's three-address IR.

Used for:
  - the AI optimiser's / refactoring verifier (same printed output and return value?)
  - the bug detector (does the suspected bug really happen when the program runs?)
  - the IDE debugger (step-by-step trace with call stack, variables and source lines)
  - the portable "MiniC VM" executable when no C compiler is installed

Semantics match the native back-end: 32-bit wrap-around `int` (8-bit char, 16-bit short, 64-bit
long), float rounded to 32 bits, C-style division that truncates toward zero, comparisons/logic
produce 0 or 1, globals are initialised by the top-level code before main runs, each call gets
its own frame (so recursion works).

Arrays are shared by reference (an array parameter sees the caller's array). Unlike real C, the
VM CHECKS every array access, so the classic C mistakes stop with a clear message instead of
silently corrupting memory:
  out_of_bounds    a[5] in an array of 5 elements (valid indexes 0..4)
  uninit           reading a variable or element that was never given a value
  buffer_overflow  strcpy / strcat / scanf("%s") writing more characters than the array holds
  no_terminator    strlen / printf("%s") / strcmp on a char array with no '\\0' at its end
This file is self-contained (the portable VM build copies it), so it imports nothing from the compiler.
"""
import json
import math
import re
import struct


class IRRuntimeError(Exception):
    def __init__(self, message, kind="error"):
        super().__init__(message)
        self.kind = kind


BITS = {"char": 8, "short": 16, "int": 32, "long": 64}
FLOATING = ("float", "double")


def wrap_bits(v, bits):
    mask = (1 << bits) - 1
    v &= mask
    return v - (1 << bits) if v >> (bits - 1) else v


def wrap32(v):
    v &= 0xFFFFFFFF
    return v - 0x100000000 if v >= 0x80000000 else v


def to_f32(x):
    try:
        return struct.unpack("f", struct.pack("f", x))[0]
    except OverflowError:
        return math.copysign(math.inf, x)


def convert(v, typ):
    """Value v stored in a variable of type typ (C's conversion rules)."""
    if typ in FLOATING:
        v = float(v)
        return to_f32(v) if typ == "float" else v
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            raise IRRuntimeError(f"cannot store {v} in a {typ}: it is not a finite number", "error")
        v = int(v)                                   # truncates toward zero, like C
    return wrap_bits(v, BITS.get(typ, 32))


def _c_div(a, b):
    if b == 0:
        raise IRRuntimeError("division by zero", "div_zero")
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q


def _c_mod(a, b):
    if b == 0:
        raise IRRuntimeError("remainder (%) by zero", "div_zero")
    return a - _c_div(a, b) * b


def _f_div(a, b):
    if b == 0:
        raise IRRuntimeError("division by zero", "div_zero")
    return a / b


OPS = {
    "+": lambda a, b: a + b, "-": lambda a, b: a - b, "*": lambda a, b: a * b,
    "/": _c_div, "%": _c_mod,
    "<": lambda a, b: int(a < b), ">": lambda a, b: int(a > b),
    "<=": lambda a, b: int(a <= b), ">=": lambda a, b: int(a >= b),
    "==": lambda a, b: int(a == b), "!=": lambda a, b: int(a != b),
    "&&": lambda a, b: int(bool(a) and bool(b)), "||": lambda a, b: int(bool(a) or bool(b)),
}
COMPARISONS = ("<", ">", "<=", ">=", "==", "!=", "&&", "||")
BINOP_RE = re.compile(r"^(\w+) = (\S+) (\S+) (\S+)$")
CALL_RE = re.compile(r"^(?:(\w+) = )?CALL (\w+), (\d+)$")
TEMP_RE = re.compile(r"^t\d+$")
NUM_RE = re.compile(r"^-?\d+$")
FLOAT_RE = re.compile(r"^-?(?:\d+\.\d*(?:[eE][+-]?\d+)?|\d+[eE][+-]?\d+|inf|nan)$")
DECL_RE = re.compile(r"^DECLARE (\w+) (\w+)((?:\[\d*\])*)$")
CAST_RE = re.compile(r"^(\w+) = \((\w+)\) (\S+)$")
LOAD_RE = re.compile(r"^(\w+) = (\w+)((?:\[[^\]\s]+\])+)$")
STORE_AT_RE = re.compile(r"^(\w+)((?:\[[^\]\s]+\])+) = (\S+)$")
ADDR_RE = re.compile(r"^(\w+) = ADDR_(EQ|NE) (.*)$")
INDEX_RE = re.compile(r"\[([^\]\s]+)\]")


def split_operands(text):
    """'"%s", name[t1]' -> ['"%s"', 'name[t1]'] (strings may contain commas and spaces)."""
    out, i, dec = [], 0, json.JSONDecoder()
    while i < len(text):
        if text[i] == '"':
            _, end = dec.raw_decode(text, i)
            out.append(text[i:end]); i = end
        else:
            j = text.find(", ", i)
            j = len(text) if j < 0 else j
            out.append(text[i:j]); i = j
        if text.startswith(", ", i):
            i += 2
    return out


class ArrayRef:
    """An array (or one row of a 2-D array): a shared list of values plus where this view starts."""
    __slots__ = ("buf", "off", "dims", "base", "name")

    def __init__(self, buf, off, dims, base, name):
        self.buf, self.off, self.dims, self.base, self.name = buf, off, tuple(dims), base, name

    def size(self):
        n = 1
        for d in self.dims:
            n *= d
        return n


def _literal(text):
    s = json.loads(text)
    return ArrayRef([ord(c) if ord(c) < 128 else ord(c) - 256 if ord(c) < 256 else ord(c) for c in s] + [0], 0, (len(s) + 1,), "char", text)


def show_value(v, typ="int"):
    """How the debugger shows a value: arrays as {1, 2, ?}, strings as "text", floats shortly."""
    if isinstance(v, ArrayRef):
        return show_array(v)
    if typ == "float" and isinstance(v, float):
        return float(f"{v:.7g}") if math.isfinite(v) else str(v)
    if isinstance(v, float) and not math.isfinite(v):
        return str(v)
    return v


def show_array(ref, limit=24):
    items = ref.buf[ref.off:ref.off + ref.size()]
    if ref.base == "char" and len(ref.dims) == 1:
        if all(c is None for c in items):
            return "(not set yet)"
        text, ended = [], False
        for c in items:
            if c is None:
                text.append("?...")
                break
            if c == 0:
                ended = True; break
            text.append(chr(c & 0xFF) if 32 <= (c & 0xFF) < 127 else "\\x%02x" % (c & 0xFF))
        s = '"' + "".join(text) + '"'
        return s if ended else s + "  (no '\\0' yet)"
    if len(ref.dims) > 1:
        inner = ref.size() // ref.dims[0]
        rows = [show_array(ArrayRef(ref.buf, ref.off + k * inner, ref.dims[1:], ref.base, ref.name), limit)
                for k in range(min(ref.dims[0], 8))]
        return "{" + ", ".join(rows) + (", ..." if ref.dims[0] > 8 else "") + "}"
    shown = ["?" if x is None else str(show_value(x, ref.base)) for x in items[:limit]]
    return "{" + ", ".join(shown) + (", ..." if len(items) > limit else "") + "}"


class Function:
    def __init__(self, name, code):
        self.name, self.code = name, code                      # code: list of (ir_index, ins)
        self.labels = {ins[6:].rstrip(":"): k for k, (_, ins) in enumerate(code) if ins.startswith("LABEL ")}
        self.locals = set()
        self.types = {}                                        # name -> (base type, dims)
        self.result_type = "int"
        for _, ins in code:
            if ins.startswith("DECLARE "):
                m = DECL_RE.match(ins)
                if m:
                    dims = tuple(int(d) if d else None for d in re.findall(r"\[(\d*)\]", m.group(3)))
                    self.locals.add(m.group(2)); self.types[m.group(2)] = (m.group(1), dims)
                else:
                    self.locals.add(ins.split()[-1])
            elif ins.startswith("GET_PARAM "):
                self.locals.add(ins[10:].split(",")[0])
            elif ins.startswith("RESULT_TYPE "):
                self.result_type = ins.split()[1]
            for tok in re.findall(r"\bt\d+\b", "" if ins.startswith(("WRITE_STR ", "WRITE_FMT ", "READ ")) else ins):
                self.locals.add(tok)
            if ins.startswith(("WRITE_FMT ", "READ ")):
                for tok in re.findall(r"\bt\d+\b", ins.split(", ", 1)[-1]):
                    self.locals.add(tok)


def split_functions(ir):
    """Top-level code and each function's body, keeping original instruction indexes."""
    top, funcs, current = [], {}, None
    for i, ins in enumerate(ir):
        if ins.startswith("FUNC "):
            current = ins[5:].rstrip(":"); funcs[current] = []
        elif ins.startswith("END_FUNC"):
            current = None
        else:
            (funcs[current] if current else top).append((i, ins))
    glob = Function("<globals>", top)
    glob.locals = {t for t in glob.locals if TEMP_RE.match(t)}      # declared names at top level are globals
    return glob, {n: Function(n, c) for n, c in funcs.items()}


SAMPLE_INPUT = "5 3 8 2 7 1 9 4 6 10 " * 20      # used by automatic checks when no input is given


class Reader:
    """Input for scanf, with C's rules: numbers and %s skip spaces and new lines, %c does not
    (unless the format has a space before it: " %c"). `stdin` is text, None (sample input), or a
    callable that returns the next piece of text (e.g. a line) or None at the end."""
    def __init__(self, stdin):
        self.more = stdin if callable(stdin) else None
        self.buf = "" if callable(stdin) else (SAMPLE_INPUT if stdin is None else stdin)
        self.pos = 0

    def peek(self):
        while self.pos >= len(self.buf):
            if self.more is None:
                return None
            chunk = self.more()
            if chunk is None:
                self.more = None
                return None
            self.buf, self.pos = self.buf[self.pos:] + chunk, 0
        return self.buf[self.pos]

    def skip_space(self):
        while (c := self.peek()) is not None and c.isspace():
            self.pos += 1

    def word(self):
        self.skip_space()
        out = []
        while (c := self.peek()) is not None and not c.isspace():
            out.append(c); self.pos += 1
        return "".join(out) if out else None

    def char(self):
        c = self.peek()
        if c is not None:
            self.pos += 1
        return c

    def number(self, floating):
        """Like scanf %d / %f: skip spaces, read the longest number at the front, leave the rest."""
        self.skip_space()
        if self.peek() is None:
            return None
        allowed = "0123456789+-.eE" if floating else "0123456789+-"
        run = []
        k = self.pos
        while (c := self.peek_at(k)) is not None and c in allowed:
            run.append(c); k += 1
        pattern = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?" if floating else r"[+-]?\d+")
        for n in range(len(run), 0, -1):
            if pattern.fullmatch("".join(run[:n])):
                self.pos += n
                text = "".join(run[:n])
                return float(text) if floating else int(text)
        bad = self.word()
        raise IRRuntimeError(f"scanf expected {'a number' if floating else 'a whole number'} but the input was {bad!r}", "input")

    def peek_at(self, k):
        while k >= len(self.buf):
            if self.more is None:
                return None
            chunk = self.more()
            if chunk is None:
                self.more = None
                return None
            self.buf += chunk
        return self.buf[k]


# ---------------------------------------------------------------- library functions
def _label(ref, name):
    return name if not ref.name or ref.name == name or ref.name.startswith('"') else f"{name} (the array '{ref.name}')"


def _cstr(ref, fname, name, limit=None):
    """The characters of a string, up to its '\\0'. Raises when there is no '\\0' inside the array."""
    fname = fname if "(" in fname else fname + "()"
    end = ref.off + ref.size()
    out = []
    for p in range(ref.off, end):
        if limit is not None and len(out) >= limit:
            return out
        c = ref.buf[p]
        if c is None:
            raise IRRuntimeError(f"{fname} read {_label(ref, name)}[{p - ref.off}], which has no value yet: "
                                 f"the text was never finished with a '\\0'", "uninit")
        if c == 0:
            return out
        out.append(c)
    raise IRRuntimeError(f"{fname} went past the end of {_label(ref, name)} ({ref.size()} chars) without finding the "
                         f"'\\0' that ends a string", "no_terminator")


def _need_room(ref, need, fname, name, what):
    if need > ref.size():
        raise IRRuntimeError(f"{fname}() needs {need} places ({what}) but {_label(ref, name)} only has {ref.size()}: "
                             f"it writes past the end of the array (buffer overflow)", "buffer_overflow")


def _write(ref, codes):
    for k, c in enumerate(codes):
        ref.buf[ref.off + k] = c


def _cmp(a, b, n=None):
    for k in range(max(len(a), len(b)) + 1):
        if n is not None and k >= n:
            return 0
        x = a[k] & 0xFF if k < len(a) else 0
        y = b[k] & 0xFF if k < len(b) else 0
        if x != y or x == 0:
            return x - y
    return 0


def _ctype(test):
    return lambda args, names, write: int(0 <= args[0] < 128 and test(chr(args[0])))


def _math(fn, name):
    def run(args, names, write):
        try:
            return fn(*[float(a) for a in args])
        except ValueError:
            raise IRRuntimeError(f"{name}() of {', '.join(map(str, args))} has no real-number answer", "math")
        except OverflowError:
            return math.inf
    return run


def _strncpy(args, names, write):
    d, s, n = args
    _need_room(d, n, "strncpy", names[0], f"you asked for {n} characters")
    src = _cstr(s, "strncpy", names[1], limit=n)
    _write(d, (src + [0] * n)[:n])                 # no '\0' is added when the source is n or longer


def _strcat(args, names, write):
    d, s = args[:2]
    old = _cstr(d, "strcat", names[0])
    add = _cstr(s, "strcat", names[1], limit=args[2] if len(args) > 2 else None)
    _need_room(d, len(old) + len(add) + 1, "strcat" if len(args) == 2 else "strncat", names[0],
               f"{len(old)} + {len(add)} characters + '\\0'")
    _write(ArrayRef(d.buf, d.off + len(old), (d.size() - len(old),), d.base, d.name), add + [0])


def _strcpy(args, names, write):
    d, s = args
    src = _cstr(s, "strcpy", names[1])
    _need_room(d, len(src) + 1, "strcpy", names[0], f"{len(src)} characters + '\\0'")
    _write(d, src + [0])


def _puts(args, names, write):
    write("".join(chr(c & 0xFF) for c in _cstr(args[0], "puts", names[0])) + "\n")


BUILTINS = {
    "strlen": lambda a, n, w: len(_cstr(a[0], "strlen", n[0])),
    "strcpy": _strcpy, "strncpy": _strncpy, "strcat": _strcat, "strncat": _strcat,
    "strcmp": lambda a, n, w: _cmp(_cstr(a[0], "strcmp", n[0]), _cstr(a[1], "strcmp", n[1])),
    "strncmp": lambda a, n, w: _cmp(_cstr(a[0], "strncmp", n[0], a[2]), _cstr(a[1], "strncmp", n[1], a[2]), a[2]),
    "toupper": lambda a, n, w: a[0] - 32 if 97 <= a[0] <= 122 else a[0],
    "tolower": lambda a, n, w: a[0] + 32 if 65 <= a[0] <= 90 else a[0],
    "isupper": _ctype(lambda c: "A" <= c <= "Z"), "islower": _ctype(lambda c: "a" <= c <= "z"),
    "isalpha": _ctype(str.isalpha), "isdigit": _ctype(str.isdigit), "isalnum": _ctype(str.isalnum),
    "isspace": _ctype(lambda c: c in " \t\n\r\v\f"),
    "abs": lambda a, n, w: wrap32(abs(a[0])),
    "sqrt": _math(math.sqrt, "sqrt"), "pow": _math(math.pow, "pow"), "fabs": _math(math.fabs, "fabs"),
    "floor": _math(math.floor, "floor"), "ceil": _math(math.ceil, "ceil"),
    "puts": _puts, "putchar": lambda a, n, w: w(chr(a[0] & 0xFF)),
}
BUILTIN_RESULT = {"sqrt": "double", "pow": "double", "fabs": "double", "floor": "double", "ceil": "double"}
WRITES_FIRST_ARG = ("strcpy", "strncpy", "strcat", "strncat")


def c_format(spec, v, write_str=None):
    """One printf conversion, the way C prints it."""
    m = re.match(r"%([-+ 0#]*)(\d*)(?:\.(\d*))?(hh|h|ll|l|L)?([a-zA-Z])$", spec)
    flags, width, prec, length, conv = m.group(1), m.group(2), m.group(3), m.group(4) or "", m.group(5)
    py = "%" + flags + width + ("." + prec if prec is not None else "")
    if conv == "s":
        return (py + "s") % v
    if conv == "c":
        return (py + "s") % chr(int(v) & 0xFF)
    if conv in "fFeEgG":
        return (py + conv) % float(v)
    v = int(v)
    if length == "h":
        v = wrap_bits(v, 16)
    elif length == "hh":
        v = wrap_bits(v, 8)
    bits = 64 if length in ("l", "ll") else 32
    if conv in "di":
        return (py + "d") % v
    if conv == "u":
        return (py + "d") % (v & ((1 << bits) - 1))
    if conv == "o":
        return (py.replace("#", "") + "o") % (v & ((1 << bits) - 1))
    return (py + conv) % (v & ((1 << bits) - 1))


# ---------------------------------------------------------------- the machine
def run_program(ir, line_map=None, trace=False, max_steps=1_000_000, max_trace=20_000, max_depth=1_000,
                stdin=None, on_output=None):
    """Execute the program. Returns a dict:
       return_value, output, steps, error, error_kind, error_line, overflows, warnings, variables,
       trace (list of events) when trace=True. An event is
       {i, line, ins, func, depth, changed: [name, value, 'l'|'g'] | None, out, call?, ret?, dest?}.
       For arrays, `changed` holds the whole array as text, e.g. ["a", "{1, 2, ?}", "l"]."""
    top, funcs = split_functions(ir)
    globals_env, output, events, overflows, warnings = {}, [], [], [], []
    notes = []                                    # evidence for mistakes that do not stop the program
    chunks = []                                   # everything written to stdout, as text
    reader = Reader(stdin)
    inputs_used = []

    def write(text):
        chunks.append(text)
        if on_output:
            on_output(text)
    st = {"steps": 0}
    frames = []

    def line_of(idx):
        return line_map[idx] if line_map and idx is not None and idx < len(line_map) else None

    def type_of(fr, name):
        if name in fr["fn"].locals:
            return fr["fn"].types.get(name, ("int", ()))
        return top.types.get(name, ("int", ()))

    def env_of(fr, name):
        return fr["env"] if name in fr["fn"].locals else globals_env

    def element(fr, text, want_value):
        """a[t1][t2]: the element's (list, position) or, with fewer indexes, a row (ArrayRef)."""
        name = text[:text.index("[")]
        idx = [val(fr, x) for x in INDEX_RE.findall(text)]
        ref = val(fr, name)
        if not isinstance(ref, ArrayRef):
            raise IRRuntimeError(f"'{name}' is not an array", "error")
        pos, size = ref.off, ref.size()
        for k, i in enumerate(idx):
            d = ref.dims[k]
            size //= d
            if isinstance(i, float) or not 0 <= i < d:
                where = name + "".join(f"[{x}]" for x in idx)
                dim = f"dimension {k + 1} of {_label(ref, name)}" if len(ref.dims) > 1 else _label(ref, name)
                raise IRRuntimeError(f"array index out of bounds: {where} uses index {i}, but {dim} has {d} elements "
                                     f"(valid indexes are 0 to {d - 1})", "out_of_bounds")
            pos += i * size
        if len(idx) < len(ref.dims):
            return ArrayRef(ref.buf, pos, ref.dims[len(idx):], ref.base, ref.name)
        if want_value:
            v = ref.buf[pos]
            if v is None:
                raise IRRuntimeError(f"{name}" + "".join(f"[{x}]" for x in idx) + " is read before it has been given a value", "uninit")
            return v
        return ref, pos

    def val(fr, x):
        if NUM_RE.match(x):
            return int(x)
        if x[:1] == '"':
            return _literal(x)
        if "[" in x:
            return element(fr, x, True)
        if FLOAT_RE.match(x):
            return float(x)
        env = env_of(fr, x)
        if x not in env:
            raise IRRuntimeError(f"variable '{x}' is read before it has a value", "uninit")
        return env[x]

    def assign(fr, name, value):
        if name in fr["fn"].locals:
            fr["env"][name] = value
            return "l"
        globals_env[name] = value
        return "g"

    def push(fn, args, dest, call_idx, names=None):
        if len(frames) >= max_depth:
            raise IRRuntimeError(f"stack overflow: more than {max_depth} nested calls to {fn.name}() (is the base case missing?)", "stack_overflow")
        frames.append({"fn": fn, "pc": 0, "env": {}, "args": args, "pending": [], "pnames": [], "dest": dest, "call_idx": call_idx})

    def emit(fr, idx, ins, changed=None, out=None, text=None, **extra):
        if trace and len(events) < max_trace:
            ev = {"i": idx, "line": line_of(idx), "ins": ins, "func": fr["fn"].name, "depth": len(frames),
                  "changed": list(changed) if changed else None, "out": out, "text": text}
            ev.update(extra)
            events.append(ev)

    def shown(fr, name):
        """(name, display value, scope) for the debugger after `name` changed."""
        v = env_of(fr, name).get(name)
        return (name, show_value(v, type_of(fr, name)[0]), "l" if name in fr["fn"].locals else "g")

    def note_overflow(idx, message, raw, v):
        if len(overflows) < 5:
            overflows.append({"line": line_of(idx), "ins": None, "exact": raw, "stored": v, "message": message})

    def note(idx, kind, message):
        if sum(1 for n in notes if n["kind"] == kind) < 5:
            notes.append({"kind": kind, "line": line_of(idx), "message": message})

    def store_scalar(fr, name, v):
        base, dims = type_of(fr, name)
        if not dims and base != "int":
            v = convert(v, base)
        return assign(fr, name, v)

    def run_frames(stop_depth):
        """Run until the frame stack shrinks back to stop_depth. Returns the last return value."""
        last = None
        while len(frames) > stop_depth:
            fr = frames[-1]
            code = fr["fn"].code
            if fr["pc"] >= len(code):                         # fell off the end of the function
                last = _return(fr, None, code[-1][0] if code else None, "(end of function)")
                continue
            st["steps"] += 1
            if st["steps"] > max_steps:
                raise IRRuntimeError(f"stopped after {max_steps:,} steps: the program never finishes (infinite loop?)", "step_limit")
            idx, ins = code[fr["pc"]]
            fr["pc"] += 1
            st["idx"] = idx
            if ins.startswith("DECLARE "):
                m = DECL_RE.match(ins)
                if m and m.group(3) and "[]" not in m.group(3):
                    name, base = m.group(2), m.group(1)
                    dims = tuple(int(d) for d in re.findall(r"\[(\d+)\]", m.group(3)))
                    n = 1
                    for d in dims:
                        n *= d
                    is_global = fr["fn"].name == "<globals>"
                    ref = ArrayRef([0.0 if base in FLOATING else 0] * n if is_global else [None] * n, 0, dims, base, name)
                    assign(fr, name, ref)
                    emit(fr, idx, ins, shown(fr, name))
                else:
                    emit(fr, idx, ins)
            elif ins.startswith(("LABEL ", "RESULT_TYPE ")):
                emit(fr, idx, ins)
            elif ins.startswith("FALLTHROUGH "):
                a, b = ins.split()[1:3]
                note(idx, "fallthrough", f"the case on line {a} had no break, so the program went on into the case on line {b}")
                emit(fr, idx, ins)
            elif ins.startswith("CONST "):
                t, n = ins[6:].split(", ")
                base = type_of(fr, t)[0]
                v = convert(float(n) if not NUM_RE.match(n) else int(n), base) if base != "int" or not NUM_RE.match(n) else wrap32(int(n))
                emit(fr, idx, ins, (t, show_value(v, base), assign(fr, t, v)))
            elif ins.startswith("STORE "):
                name, src = ins[6:].split(", ")
                v = val(fr, src)
                store_scalar(fr, name, v)
                emit(fr, idx, ins, shown(fr, name))
            elif ins.startswith("ARRAY_INIT "):
                name, values = ins[11:].split(", ", 1)
                ref = val(fr, name)
                vals = json.loads(values)
                for k in range(ref.size()):
                    ref.buf[ref.off + k] = convert(vals[k], ref.base) if k < len(vals) else (0.0 if ref.base in FLOATING else 0)
                emit(fr, idx, ins, shown(fr, name))
            elif ins.startswith("GET_PARAM "):
                name, k = ins[10:].split(", ")
                if int(k) >= len(fr["args"]):
                    raise IRRuntimeError(f"{fr['fn'].name}() was called with too few arguments", "error")
                v = fr["args"][int(k)]
                if not isinstance(v, ArrayRef):
                    store_scalar(fr, name, v)
                else:
                    assign(fr, name, v)
                emit(fr, idx, ins, shown(fr, name))
            elif ins.startswith("PARAM "):
                op = ins[6:].strip()
                fr["pending"].append(val(fr, op))
                fr["pnames"].append(op[:op.index("[")] if "[" in op and op[:1] != '"' else op)
                emit(fr, idx, ins)
            elif ins.startswith("PRINT "):
                v = val(fr, ins[6:].strip())
                output.append(v); write(f"{v}\n")
                emit(fr, idx, ins, out=v, text=f"{v}\n")
            elif ins.startswith("WRITE_INT "):
                v = val(fr, ins[10:].strip())
                output.append(v); write(str(v))
                emit(fr, idx, ins, out=v, text=str(v))
            elif ins.startswith("WRITE_FMT "):
                spec, op = split_operands(ins[10:])
                spec = json.loads(spec)
                v = val(fr, op)
                if spec.endswith("s"):
                    name = op[:op.index("[")] if "[" in op and op[:1] != '"' else op
                    v = "".join(chr(c & 0xFF) for c in _cstr(v, "printf(\"%s\")", name))
                txt = c_format(spec, v)
                write(txt)
                emit(fr, idx, ins, text=txt)
            elif ins.startswith("READ_INT "):
                name = ins[9:].strip()
                v = reader.number(False)
                if v is None:
                    raise IRRuntimeError("the program asked for a number (scanf) but there is no more input", "input")
                v = wrap32(v)
                inputs_used.append(v)
                emit(fr, idx, ins, (name, v, assign(fr, name, v)), read=v)
            elif ins.startswith("READ "):
                spec, target = split_operands(ins[5:])
                spec = json.loads(spec)
                conv = spec.strip()
                if conv == "%s":
                    ref = val(fr, target)
                    word = reader.word()
                    if word is None:
                        raise IRRuntimeError("the program asked for a word (scanf %s) but there is no more input", "input")
                    name = target[:target.index("[")] if "[" in target else target
                    _need_room(ref, len(word) + 1, 'scanf("%s")', name, f"the word {word!r} has {len(word)} characters + '\\0'")
                    _write(ref, [ord(c) if ord(c) < 128 else 63 for c in word] + [0])
                    inputs_used.append(word)
                    emit(fr, idx, ins, shown(fr, name), read=word)
                    continue
                if conv == "%c":
                    if spec.startswith(" "):
                        reader.skip_space()
                    c = reader.char()
                    if c is None:
                        raise IRRuntimeError("the program asked for a character (scanf %c) but there is no more input", "input")
                    if c.isspace():
                        note(idx, "char_whitespace", "scanf(\"%c\") read " + ("the Enter key (a new line)" if c == "\n" else "a space")
                             + " left over from the previous input, not the letter that was typed")
                    v = ord(c) if ord(c) < 128 else 63
                else:
                    v = reader.number(conv[-1] in "feg")
                    if v is None:
                        raise IRRuntimeError("the program asked for a number (scanf) but there is no more input", "input")
                inputs_used.append(v)
                if "[" in target:
                    ref, pos = element(fr, target, False)
                    ref.buf[pos] = convert(v, ref.base)
                    name = target[:target.index("[")]
                else:
                    name = target
                    store_scalar(fr, name, convert(v, type_of(fr, name)[0]))
                emit(fr, idx, ins, shown(fr, name), read=v)
            elif ins.startswith("WRITE_STR "):
                txt = json.loads(ins[10:])
                write(txt)
                emit(fr, idx, ins, text=txt)
            elif ins.startswith("IF_FALSE "):
                cond, _, label = ins[9:].split(" ")
                if val(fr, cond) == 0:
                    fr["pc"] = fr["fn"].labels[label]
                emit(fr, idx, ins)
            elif ins.startswith("GOTO "):
                fr["pc"] = fr["fn"].labels[ins[5:]]
                emit(fr, idx, ins)
            elif ins.startswith("RETURN"):
                rest = ins[6:].strip()
                value = val(fr, rest) if rest else None
                if value is not None and fr["fn"].result_type != "int":
                    value = convert(value, fr["fn"].result_type)
                last = _return(fr, value, idx, ins)
            elif CALL_RE.match(ins):
                dest, name, n = CALL_RE.match(ins).groups()
                n = int(n)
                args = fr["pending"][len(fr["pending"]) - n:] if n else []
                names = fr["pnames"][len(fr["pnames"]) - n:] if n else []
                del fr["pending"][len(fr["pending"]) - n:]
                del fr["pnames"][len(fr["pnames"]) - n:]
                if name not in funcs and name in BUILTINS:
                    result = BUILTINS[name](args, names, write)
                    changed = None
                    if name in WRITES_FIRST_ARG and names and not names[0].startswith('"'):
                        changed = shown(fr, names[0])
                    if dest:
                        result = 0 if result is None else result
                        base = type_of(fr, dest)[0]
                        result = convert(result, base) if base != "int" or isinstance(result, float) else result
                        changed = (dest, show_value(result, base), assign(fr, dest, result))
                    emit(fr, idx, ins, changed, call=name, args=[show_value(a) for a in args], builtin=True)
                    continue
                if name not in funcs:
                    raise IRRuntimeError(f"function '{name}' is not defined", "error")
                emit(fr, idx, ins, call=name, args=[show_value(a) for a in args])
                push(funcs[name], args, dest, idx)
            elif ins.startswith("t") and CAST_RE.match(ins):
                t, typ, src = CAST_RE.match(ins).groups()
                v = val(fr, src)
                nv = convert(v, typ)
                if typ in BITS and not isinstance(v, float) and nv != v:
                    note_overflow(idx, f"converting {v} to {typ} gives {nv}: a {typ} only holds "
                                       f"{-(1 << (BITS[typ] - 1))} to {(1 << (BITS[typ] - 1)) - 1}", v, nv)
                elif typ in BITS and isinstance(v, float) and abs(v) >= 2 ** (BITS[typ] - 1):
                    note_overflow(idx, f"the decimal number {v} is too big for a {typ}; it became {nv}", v, nv)
                elif typ in BITS and isinstance(v, float) and v != int(v):
                    note(idx, "truncation", f"the decimal value {v:.6g} was stored as the whole number {nv}")
                emit(fr, idx, ins, (t, show_value(nv, typ), assign(fr, t, nv)))
            elif ADDR_RE.match(ins):
                t, kind, rest = ADDR_RE.match(ins).groups()
                a, b = [val(fr, x) for x in split_operands(rest)]
                same = isinstance(a, ArrayRef) and isinstance(b, ArrayRef) and a.buf is b.buf and a.off == b.off
                v = int(same) if kind == "EQ" else int(not same)
                if not same:
                    try:
                        ta, tb = _cstr(a, "==", "a"), _cstr(b, "==", "b")
                    except IRRuntimeError:
                        ta, tb = None, None
                    if ta is not None and ta == tb:
                        text = "".join(chr(x & 0xFF) for x in ta)
                        note(idx, "address_compare", f"both strings contained \"{text}\", but {'==' if kind == 'EQ' else '!='} "
                                                     f"gave {v} because they are two different arrays in memory")
                emit(fr, idx, ins, (t, v, assign(fr, t, v)))
            elif LOAD_RE.match(ins):
                t, _, _ = LOAD_RE.match(ins).groups()
                v = element(fr, ins.split(" = ", 1)[1], True)
                base = type_of(fr, t)[0]
                if base != "int":
                    v = convert(v, base)
                emit(fr, idx, ins, (t, show_value(v, base), assign(fr, t, v)))
            elif STORE_AT_RE.match(ins):
                target, src = ins.split(" = ", 1)
                ref, pos = element(fr, target, False)
                ref.buf[pos] = convert(val(fr, src), ref.base)
                emit(fr, idx, ins, shown(fr, target[:target.index("[")]))
            else:
                m = BINOP_RE.match(ins)
                if not m or m.group(3) not in OPS:
                    raise IRRuntimeError(f"unknown instruction: {ins}", "error")
                a, b = val(fr, m.group(2)), val(fr, m.group(4))
                op, dest = m.group(3), m.group(1)
                base = type_of(fr, dest)[0]
                if op in COMPARISONS:
                    v = OPS[op](a, b)
                    if op in ("==", "!=") and (isinstance(a, float) or isinstance(b, float)) and a != b \
                            and abs(a - b) <= 1e-6 * max(1.0, abs(a), abs(b)):
                        note(idx, "float_compare", f"{a!r} {op} {b!r} gave {v}: the two values differ only after the 7th digit")
                elif base in FLOATING or isinstance(a, float) or isinstance(b, float):
                    if op == "%":
                        raise IRRuntimeError("% needs whole numbers", "error")
                    raw = _f_div(float(a), float(b)) if op == "/" else OPS[op](float(a), float(b))
                    v = convert(raw, base if base in FLOATING else "double")
                else:
                    raw = OPS[op](a, b)
                    v = wrap_bits(raw, BITS.get(base, 32))
                    if op == "/" and b and a % b:
                        note(idx, "int_division", f"{a} / {b} gave {v}: whole-number division threw away the remainder "
                                                  f"(the exact answer is {a / b:.4g})")
                    if raw != v and len(overflows) < 5:
                        size = "64-bit long" if base == "long" else "32-bit int"
                        overflows.append({"line": line_of(idx), "ins": ins, "exact": raw, "stored": v,
                                          "message": f"{a} {op} {b} = {raw} does not fit in a {size}; it became {v}"})
                emit(fr, idx, ins, (dest, show_value(v, base), assign(fr, dest, v)))
        return last

    def _return(fr, value, idx, ins):
        frames.pop()
        dest = fr["dest"]
        if value is None and dest and fr["fn"].name != "<globals>":
            warnings.append({"line": line_of(fr["call_idx"]),
                             "message": f"{fr['fn'].name}() finished without returning a value, but its result is used"})
            value = 0
        changed = None
        if dest and frames:
            caller = frames[-1]
            base = type_of(caller, dest)[0]
            if value is not None and base != "int":
                value = convert(value, base)
            changed = (dest, show_value(value, base), assign(caller, dest, value))
        if trace and len(events) < max_trace:
            events.append({"i": idx, "line": line_of(idx), "ins": ins, "func": fr["fn"].name, "depth": len(frames) + 1,
                           "changed": None, "out": None, "ret": show_value(value), "dest": list(changed) if changed else None})
        return value

    result = {"return_value": None, "output": output, "steps": 0, "error": None, "error_kind": None,
              "error_line": None, "overflows": overflows, "warnings": warnings, "notes": notes}
    try:
        if "main" not in funcs:
            raise IRRuntimeError("the program has no main function", "error")
        push(top, [], None, None)
        run_frames(0)
        if funcs["main"].code and any(i.startswith("GET_PARAM") for _, i in funcs["main"].code):
            raise IRRuntimeError("main takes parameters, so it cannot be run directly", "error")
        push(funcs["main"], [], None, None)
        rv = run_frames(0)
        result["return_value"] = int(rv) if isinstance(rv, float) and math.isfinite(rv) else rv
    except IRRuntimeError as exc:
        result["error"], result["error_kind"] = str(exc), exc.kind
        result["error_line"] = line_of(st.get("idx"))
        result["error_function"] = frames[-1]["fn"].name if frames else None
    except RecursionError:
        result["error"], result["error_kind"] = "the program is too deeply nested to run", "stack_overflow"
    result["steps"] = st["steps"]
    result["stdout"] = "".join(chunks)
    result["inputs_used"] = inputs_used
    result["used_sample_input"] = stdin is None and bool(inputs_used)
    result["variables"] = {k: show_value(v) for k, v in globals_env.items() if not TEMP_RE.match(k)}
    if frames:
        result["variables"].update({k: show_value(v) for k, v in frames[-1]["env"].items() if not TEMP_RE.match(k)})
    if trace:
        result["trace"] = events
        result["trace_truncated"] = len(events) >= max_trace
    return result


def run_main(ir, max_steps=1_000_000, stdin=None):
    """Backward-compatible helper: return main's value or raise IRRuntimeError."""
    r = run_program(ir, max_steps=max_steps, stdin=stdin)
    if r["error"]:
        raise IRRuntimeError(r["error"], r["error_kind"])
    return r["return_value"]


def observed(ir, max_steps=1_000_000, stdin=None):
    """What a program visibly does, even when it fails: (return value, printed text, error kind).
    A program that crashes is still comparable: an optimisation must crash the same way and
    print the same text first. The error's line is left out because a valid rewrite may move it."""
    r = run_program(ir, max_steps=max_steps, stdin=stdin)
    return (None if r["error"] else r["return_value"]), r["stdout"], r["error_kind"] if r["error"] else None


def describe_observed(b):
    ret, out, err = b
    shown = out if len(out) <= 120 else out[:120] + "..."
    printed = f"prints {shown!r}" if out else "prints nothing"
    if err:
        return f"{printed} and then stops with a runtime error ({err})"
    return f"returns {ret} and {printed}"


def behaviour(ir, max_steps=1_000_000, stdin=None):
    """(return value, everything printed as text) - what an optimisation/refactoring must preserve.
    Programs that use scanf get the same input on both sides (sample input unless one is given)."""
    r = run_program(ir, max_steps=max_steps, stdin=stdin)
    if r["error"]:
        raise IRRuntimeError(r["error"], r["error_kind"])
    return r["return_value"], r["stdout"]
