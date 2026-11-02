"""Data types shared by every stage: semantic analysis, IR generation, the VM and the C back-end.

Types are plain strings so they are easy to print and to send to the IDE / the AI:
    scalars   'char' 'short' 'int' 'long' 'float' 'double'   (+ 'void' for functions)
    arrays    'int[5]'  'char[20]'  'int[3][4]'  'int[]' (array parameter: size comes from the caller)
    string    a string literal "hi" has the type 'char[3]' (two characters + the '\\0' at the end)

C rules that Mini C follows:
  * char and short are promoted to int before arithmetic (so 'a' + 1 is an int);
  * mixed arithmetic uses the "bigger" type: int < long < float < double;
  * assigning between number types converts silently (3.9 stored in an int becomes 3);
  * arrays cannot be assigned with '=' and are passed to functions by reference.
"""
import re

SCALARS = ("char", "short", "int", "long", "float", "double")
TYPE_KEYWORDS = SCALARS + ("void",)
INTEGER = ("char", "short", "int", "long")
FLOATING = ("float", "double")
SIZE = {"char": 1, "short": 2, "int": 4, "long": 8, "float": 4, "double": 8, "pointer": 8}
BITS = {"char": 8, "short": 16, "int": 32, "long": 64}
C_NAME = {"char": "char", "short": "short", "int": "int", "long": "long long", "float": "float", "double": "double"}
RANK = {"char": 0, "short": 0, "int": 1, "long": 2, "float": 3, "double": 4}

_ARRAY_RE = re.compile(r"^(\w+)((?:\[\d*\])+)$")


def parse(t):
    """'int[3][4]' -> ('int', (3, 4)); 'int[]' -> ('int', (None,)); 'int' -> ('int', ())."""
    m = _ARRAY_RE.match(t or "")
    if not m:
        return t, ()
    dims = tuple(int(d) if d else None for d in re.findall(r"\[(\d*)\]", m.group(2)))
    return m.group(1), dims


def make(base, dims=()):
    return base + "".join(f"[{'' if d is None else d}]" for d in dims)


def is_array(t):
    return bool(parse(t)[1])


def is_scalar(t):
    return t in SCALARS


def is_integer(t):
    return t in INTEGER


def is_floating(t):
    return t in FLOATING


def is_string(t):
    """A one-dimensional char array (or a string literal): something strlen/printf("%s") can use."""
    base, dims = parse(t)
    return base == "char" and len(dims) == 1


def promote(t):
    """Integer promotion: char and short become int in arithmetic."""
    return "int" if t in ("char", "short") else t


def common(a, b):
    """The type both operands are converted to (C's usual arithmetic conversions)."""
    a, b = promote(a), promote(b)
    return a if RANK[a] >= RANK[b] else b


def element(t):
    """Type of a[i]: drops the first dimension. 'int[3][4]' -> 'int[4]', 'int[5]' -> 'int'."""
    base, dims = parse(t)
    return make(base, dims[1:])


def sizeof(t, is_param=False):
    """Bytes, like C's sizeof. An array parameter is really a pointer, so sizeof gives 8."""
    base, dims = parse(t)
    if dims and (is_param or dims[0] is None):
        return SIZE["pointer"]
    n = SIZE.get(base, 4)
    for d in dims:
        n *= d or 1
    return n


def count(dims):
    """Number of elements in an array with these dimensions."""
    n = 1
    for d in dims:
        n *= d or 0
    return n


def describe(t):
    """Beginner wording: 'int[5]' -> 'an array of 5 ints', 'char[6]' -> 'a string (char array of 6)'."""
    base, dims = parse(t)
    if not dims:
        return t
    if base == "char" and len(dims) == 1:
        return "a string (char array" + (f" of {dims[0]})" if dims[0] else ")")
    if len(dims) == 1:
        return f"an array of {dims[0]} {base}s" if dims[0] else f"an array of {base}s"
    return f"a {'x'.join(str(d or '?') for d in dims)} table of {base}s"


def wrap(v, t):
    """Store an integer in a type with fewer bits, the way C does (two's complement wrap-around)."""
    bits = BITS.get(t)
    if bits is None:
        return v
    mask = (1 << bits) - 1
    v &= mask
    return v - (1 << bits) if v >> (bits - 1) else v


def int_type_of(value):
    """Type of an integer literal: int if it fits, otherwise long."""
    return "int" if -2**31 <= value < 2**31 else "long"


# ---------------------------------------------------------------- library functions
# name -> (return type, parameter types). 'char[]' means a string: a char array or a "literal".
# 'void' results can only be used as statements (strcpy etc. return a pointer in real C).
BUILTINS = {
    "strlen": ("int", ["char[]"]),
    "strcpy": ("void", ["char[]", "char[]"]),
    "strncpy": ("void", ["char[]", "char[]", "int"]),
    "strcat": ("void", ["char[]", "char[]"]),
    "strncat": ("void", ["char[]", "char[]", "int"]),
    "strcmp": ("int", ["char[]", "char[]"]),
    "strncmp": ("int", ["char[]", "char[]", "int"]),
    "toupper": ("int", ["int"]), "tolower": ("int", ["int"]),
    "isupper": ("int", ["int"]), "islower": ("int", ["int"]), "isalpha": ("int", ["int"]),
    "isdigit": ("int", ["int"]), "isalnum": ("int", ["int"]), "isspace": ("int", ["int"]),
    "abs": ("int", ["int"]),
    "sqrt": ("double", ["double"]), "pow": ("double", ["double", "double"]), "fabs": ("double", ["double"]),
    "floor": ("double", ["double"]), "ceil": ("double", ["double"]),
    "puts": ("void", ["char[]"]), "putchar": ("void", ["int"]),
}
WRITES_FIRST_ARG = {"strcpy", "strncpy", "strcat", "strncat"}
HEADER = {**{k: "string.h" for k in ("strlen", "strcpy", "strncpy", "strcat", "strncat", "strcmp", "strncmp")},
          **{k: "ctype.h" for k in ("toupper", "tolower", "isupper", "islower", "isalpha", "isdigit", "isalnum", "isspace")},
          **{k: "math.h" for k in ("sqrt", "pow", "fabs", "floor", "ceil")},
          "abs": "stdlib.h", "puts": "stdio.h", "putchar": "stdio.h"}


# ---------------------------------------------------------------- string literals
ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "\\": "\\", '"': '"', "'": "'", "a": "\a"}


def decode(raw):
    """Text between the quotes -> (characters, errors). Handles \\n \\t \\r \\0 \\\\ \\" \\'."""
    out, errors, i = [], [], 0
    while i < len(raw):
        c = raw[i]
        if c == "\\":
            nxt = raw[i + 1] if i + 1 < len(raw) else ""
            if nxt in ESCAPES:
                out.append(ESCAPES[nxt])
            else:
                errors.append(f"unsupported escape '\\{nxt}' (use \\n, \\t, \\0, \\\\, \\\" or \\')")
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out), errors


def char_value(raw):
    """Value of a character literal written as 'a' or '\\n' (raw includes the quotes)."""
    text, errors = decode(raw[1:-1])
    if errors:
        return None, errors[0]
    if len(text) != 1:
        return None, ("a character literal holds exactly one character; use double quotes for text"
                      if text else "empty character literal ''")
    return ord(text) if ord(text) < 128 else ord(text) - 256 if ord(text) < 256 else ord(text), None


# ---------------------------------------------------------------- printf / scanf formats
SPEC_RE = re.compile(r"%([-+ 0#]*)(\d*|\*)(?:\.(\d*|\*))?(hh|h|ll|l|L)?([a-zA-Z%]?)")
INT_CONV, FLOAT_CONV = "diuxXoc", "fFeEgG"


def parse_format(raw):
    """Split a printf format (as written between the quotes) into parts:
         ('text', s)                     plain text (escapes decoded)
         ('int',)                        exactly %d or %i  (the original Mini C form)
         ('spec', spec, conv, length)    any other conversion, e.g. ('spec', '%5.2f', 'f', '')
       Returns (parts, errors)."""
    parts, errors, buf, i = [], [], [], 0
    while i < len(raw):
        c = raw[i]
        if c == "\\":
            nxt = raw[i + 1] if i + 1 < len(raw) else ""
            if nxt in ESCAPES:
                buf.append(ESCAPES[nxt]); i += 2; continue
            errors.append(f"unsupported escape '\\{nxt}' (use \\n for a new line or \\t for a tab)"); i += 2; continue
        if c == "%":
            m = SPEC_RE.match(raw, i)
            spec, flags, width, prec, length, conv = m.group(0), m.group(1), m.group(2), m.group(3), m.group(4) or "", m.group(5)
            i += max(len(spec), 1)
            if spec == "%%":
                buf.append("%"); continue
            if "*" in (width or "") or "*" in (prec or ""):
                errors.append(f"'{spec}': a * width is not supported; write the number, e.g. %5d"); continue
            if conv == "" or conv not in INT_CONV + FLOAT_CONV + "s":
                kind = {"p": "pointers", "n": "pointers"}.get(conv, "")
                errors.append(f"'{spec}' is not supported" + (f": Mini C has no {kind}" if kind else "")
                              + ". Use %d (int), %ld (long), %f (float/double), %c (char) or %s (string)"); continue
            if length == "L" or (length and conv in "s") or (length in ("hh", "h") and conv in FLOAT_CONV + "c"):
                errors.append(f"'{spec}' is not supported: remove the '{length}'"); continue
            if buf:
                parts.append(("text", "".join(buf))); buf = []
            if spec in ("%d", "%i"):
                parts.append(("int",))
            else:
                parts.append(("spec", spec, conv, length))
            continue
        buf.append(c); i += 1
    if buf:
        parts.append(("text", "".join(buf)))
    return parts, errors


def printf_arg_ok(conv, length, t):
    """Is a value of type t right for this printf conversion? Returns (ok, advice)."""
    if conv == "s":
        return is_string(t), "%s prints a string: give it a char array or \"text\""
    if is_array(t):
        return False, f"%{length}{conv} prints one value, but this is {describe(t)}"
    if conv in FLOAT_CONV:
        return is_floating(t), "%f prints a float or double; for whole numbers use %d"
    if conv == "c":
        return t in ("char", "short", "int"), "%c prints one character (a char or an int code)"
    if length in ("l", "ll"):
        return t == "long", "%ld is for long values; use %d for int and char"
    if t == "long":
        return False, "a long needs %ld (with an l)"
    if is_floating(t):
        return False, "%d prints whole numbers; for a float or double use %f"
    return t in ("char", "short", "int"), "%d prints a whole number"


SCANF_SPECS = {"%d": "int", "%i": "int", "%ld": "long", "%hd": "short", "%f": "float", "%lf": "double",
               "%e": "float", "%le": "double", "%g": "float", "%lg": "double", "%c": "char", "%s": "string"}


def parse_scanf_format(raw):
    """scanf formats may contain conversions and spaces / \\n / \\t only.
    Returns (list of conversions such as ['%d', '%s'], errors)."""
    specs, errors, i = [], [], 0
    while i < len(raw):
        if raw[i] == "%":
            m = re.match(r"%(\d*)(hh|h|ll|l|L)?([a-zA-Z%]?)", raw[i:])
            spec = "%" + (m.group(2) or "") + m.group(3)
            if m.group(1):
                errors.append(f"'{m.group(0)}': widths in scanf are not supported (Mini C checks the array size for you)")
            elif spec.replace("ll", "l") in SCANF_SPECS:
                specs.append(spec.replace("ll", "l"))
            else:
                errors.append(f"'{m.group(0)}' is not supported in scanf. Use %d, %ld, %f, %lf, %c or %s")
            i += len(m.group(0))
        elif raw.startswith(("\\n", "\\t"), i):
            i += 2
        elif raw[i] == " ":
            i += 1
        else:
            errors.append(f"scanf formats can only contain %d / %f / %c / %s ... and spaces, not {raw[i]!r}. "
                          "Print a prompt with printf before the scanf")
            i = len(raw)
    return specs, errors


def scanf_space_before(raw):
    """For each conversion in a scanf format: is there a space (or \\n, \\t) before it?
    That matters for %c only: " %c" skips spaces and new lines, "%c" reads them."""
    flags, gap, i = [], False, 0
    while i < len(raw):
        if raw[i] == "%":
            m = re.match(r"%\d*(?:hh|h|ll|l|L)?[a-zA-Z%]?", raw[i:])
            flags.append(gap)
            gap = False
            i += len(m.group(0))
        elif raw.startswith(("\\n", "\\t"), i):
            gap = True; i += 2
        elif raw[i] == " ":
            gap = True; i += 1
        else:
            i += 1
    return flags
