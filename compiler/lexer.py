"""Position-aware lexer for the supported Mini C subset.

Unknown characters do not stop the lexer: each one is recorded and skipped, so every lexical
error in the file is reported at once (raised together as LexErrors at the end).

`#include <...>` lines (habit from normal C) are accepted and skipped with a note, because Mini C
needs no headers: print(x), strlen(s), sqrt(x) ... are built in. `#define NAME value` and `#undef` are
handled first by compiler/preprocess.py; other preprocessor lines (#if, #ifdef ...) are errors.

Token kinds: KEYWORD, IDENTIFIER, NUMBER (whole number), FLOAT (3.14, 2.5f, 1e-3), CHAR ('a', '\\n'),
STRING ("text"), OP and DELIM.
"""
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Token:
    kind: str
    value: str
    line: int
    column: int


KEYWORDS = {'int', 'return', 'if', 'else', 'while', 'for', 'void', 'print',
            'char', 'short', 'long', 'float', 'double', 'sizeof',
            'break', 'continue', 'do', 'switch', 'case', 'default', 'const'}
TOKEN_RE = re.compile(r'''(?P<SPACE>[ \t\r]+)|(?P<NEWLINE>\n)|(?P<COMMENT>//[^\n]*|/\*[\s\S]*?\*/)|(?P<PREPROC>\#[^\n]*)|(?P<STRING>"(?:[^"\\\n]|\\.)*")|(?P<CHAR>'(?:[^'\\\n]|\\.)*')|(?P<FLOAT>(?:\d+\.\d*|\.\d+)(?:[eE][+-]?\d+)?[fF]?|\d+[eE][+-]?\d+[fF]?)|(?P<NUMBER>\d+)|(?P<IDENTIFIER>[A-Za-z_][A-Za-z0-9_]*)|(?P<OP>==|!=|<=|>=|\+\+|--|&&|\|\||[+\-*/%]=|[+\-*/%<>=!&])|(?P<DELIM>[(){};,:\[\]])|(?P<MISMATCH>.)''')

CHAR_HINTS = {
    '"': "This string is not closed: add the missing \" on the same line.",
    "'": "This character is not closed: write one character between single quotes, e.g. 'a' or '\\n'.",
    '#': "Preprocessor lines such as #include are not needed (or supported) in Mini C.",
    '|': "Use || for logical OR.", '.': "Mini C has no structs, so '.' is only used inside numbers such as 3.14.",
    '?': "The ?: operator is not supported; use if/else.",
    '~': "Bitwise operators are not supported in Mini C.", '^': "Bitwise operators are not supported in Mini C; for powers use pow(x, y).",
}


class LexErrors(SyntaxError):
    """All lexical errors in a file. str() is the first error (keeps old behaviour)."""
    def __init__(self, errors, tokens):
        super().__init__(errors[0]["message"])
        self.errors, self.tokens = errors, tokens


class Lexer:
    def __init__(self, code):
        self.code = code
        self.notes = []          # informational messages, e.g. skipped #include lines

    def tokenize(self):
        from compiler.preprocess import preprocess
        text, errors = preprocess(self.code)          # #define / #undef, same line numbers
        self.text = text
        tokens, line, col = [], 1, 1
        for m in TOKEN_RE.finditer(text):
            kind, value = m.lastgroup, m.group()
            tok_line, tok_col = line, col
            if kind == 'NEWLINE':
                line += 1; col = 1; continue
            if kind in ('SPACE', 'COMMENT'):
                if '\n' in value:
                    line += value.count('\n'); col = len(value.split('\n')[-1]) + 1
                else:
                    col += len(value)
                continue
            if kind == 'PREPROC':
                at_line_start = text[:m.start()].rsplit('\n', 1)[-1].strip() == ''
                inc = re.match(r'#\s*include\s*([<"])([^>"]*)[>"]\s*(//.*)?$', value.rstrip())
                if at_line_start and inc:
                    header = inc.group(2)
                    close = '>' if inc.group(1) == '<' else '"'
                    tip = " (the standard header is <stdio.h>)" if header.startswith("stdio") and header != "stdio.h" else ""
                    self.notes.append({"category": "Note", "line": tok_line, "column": tok_col,
                                       "message": f"#include {inc.group(1)}{header}{close} ignored: Mini C needs no header files, printf, strlen, sqrt ... are built in{tip}"})
                    col += len(value)
                    continue
                errors.append({"category": "Lexical Error", "line": tok_line, "column": tok_col, "found": "#",
                               "message": f"Unexpected character '#' at line {tok_line}, column {tok_col}",
                               "hint": "Mini C accepts #include (ignored), #define NAME value and #undef NAME. Conditional lines such as #if and #ifdef are not supported."})
                col += len(value)
                continue
            if kind == 'MISMATCH':
                errors.append({"category": "Lexical Error", "line": tok_line, "column": tok_col, "found": value,
                               "message": f"Unexpected character {value!r} at line {tok_line}, column {tok_col}",
                               "hint": CHAR_HINTS.get(value, "Remove this character; Mini C does not use it.")})
                col += len(value)
                continue
            if kind == 'CHAR':
                from compiler.typesys import char_value
                _, problem = char_value(value)
                if problem:
                    errors.append({"category": "Lexical Error", "line": tok_line, "column": tok_col, "found": value,
                                   "message": f"Bad character literal {value} at line {tok_line}, column {tok_col}: {problem}",
                                   "hint": "A char holds one character in single quotes, e.g. 'a', '\\n' or '\\0'. Text such as \"abc\" uses double quotes."})
                    col += len(value)
                    continue
            if kind == 'IDENTIFIER' and value in KEYWORDS:
                kind = 'KEYWORD'
            tokens.append(Token(kind, value, tok_line, tok_col))
            col += len(value)
        tokens.append(Token('EOF', 'EOF', line, col))
        if errors:
            raise LexErrors(errors, tokens)
        return tokens
