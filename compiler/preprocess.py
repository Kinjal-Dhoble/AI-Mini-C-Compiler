"""A small C preprocessor: object-like #define and #undef.

    #define SIZE 10            later uses of SIZE are replaced by 10
    #define LIMIT (SIZE * 2)   macros may use earlier macros
    #undef SIZE

Replacement happens on the source text, outside strings, character literals and comments, so
every front end (the hand-written parser, the PLY yacc parser and the flex/bison checker) sees the
same program. A #define line is replaced by an empty line so line numbers stay the same; when the
value is shorter than the name, spaces keep later columns where they were.

Not supported (reported as errors): function-like macros  #define SQ(x) ((x)*(x))  and conditional
compilation (#if, #ifdef, #ifndef, #else, #endif).
"""
import re

_DEFINE = re.compile(r"^[ \t]*#[ \t]*define[ \t]+([A-Za-z_]\w*)(\()?(.*)$")
_UNDEF = re.compile(r"^[ \t]*#[ \t]*undef[ \t]+([A-Za-z_]\w*)[ \t]*(//.*)?$")
_PIECES = re.compile(r'''"(?:[^"\\\n]|\\.)*"?|'(?:[^'\\\n]|\\.)*'?|//.*|/\*|\*/|[A-Za-z_]\w*|.''')


def _strip_comment(body):
    body = re.sub(r"/\*.*?\*/", " ", body)
    return body.split("//", 1)[0].strip()


def preprocess(source):
    """Returns (text, errors). errors are lexical-error dicts with line and column."""
    if "#" not in source:
        return source, []
    macros, errors, out = {}, [], []
    in_comment = False

    def expand(text, line, depth=0):
        if depth > 20:
            errors.append({"category": "Lexical Error", "line": line, "column": 1, "found": "#define",
                           "message": f"A #define refers to itself (line {line})",
                           "hint": "A macro cannot be defined in terms of itself, directly or through another macro."})
            return text
        changed = False

        def repl(m):
            nonlocal changed
            w = m.group(0)
            if w in macros:
                changed = True
                return macros[w]
            return w
        new = re.sub(r'"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\'|[A-Za-z_]\w*', lambda m: repl(m) if m.group(0)[0] not in "\"'" else m.group(0), text)
        return expand(new, line, depth + 1) if changed else new

    for n, line in enumerate(source.split("\n"), 1):
        if not in_comment:
            d = _DEFINE.match(line)
            if d:
                name, paren, rest = d.group(1), d.group(2), d.group(3)
                if paren:
                    errors.append({"category": "Lexical Error", "line": n, "column": line.index(name) + 1, "found": "#define",
                                   "message": f"Function-like macro {name}(...) at line {n} is not supported",
                                   "hint": "Mini C supports #define NAME value for constants. For anything with parameters, write a function."})
                else:
                    macros[name] = expand(_strip_comment(rest), n) or ""
                out.append("")
                continue
            u = _UNDEF.match(line)
            if u:
                macros.pop(u.group(1), None)
                out.append("")
                continue
        if not macros:
            # still track block comments so a '#define' inside one is not treated as real
            for p in _PIECES.findall(line):
                if p == "/*" and not in_comment:
                    in_comment = True
                elif p == "*/" and in_comment:
                    in_comment = False
            out.append(line)
            continue
        pieces, new = _PIECES.findall(line), []
        for p in pieces:
            if in_comment:
                new.append(p)
                if p == "*/":
                    in_comment = False
                continue
            if p == "/*":
                in_comment = True
                new.append(p)
            elif p.startswith("//") or p[0] in "\"'":
                new.append(p)
            elif p in macros:
                value = expand(macros[p], n)
                new.append(value + " " * max(0, len(p) - len(value)))
            else:
                new.append(p)
        out.append("".join(new))
    return "\n".join(out), errors
