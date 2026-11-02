"""Recursive-descent parser and AST for a deliberately small C-like language.

Error recovery (so ALL syntax errors are reported, not just the first):
  * phrase-level recovery - when the fix is obvious the parser pretends the missing token was
    there and carries on without losing the statement:
        missing ';' at the end of a line, before '}' or at end of file
        missing ')' just before '{' or ';'
  * panic-mode recovery   - otherwise it skips tokens up to a synchronising point: past the next
    ';', up to a '}', past a whole '{ ... }' block, or to a keyword that starts a new line.

Every recorded error says what was expected, what was found, and how the parser recovered.
If only phrase-level recovery was needed the AST is still complete (ParseErrors.complete), so
semantic analysis can run and report further errors in the same compile.

Data: types char short int long float double (+ void functions); arrays  int a[5];  int m[3][4];
int a[] = {1, 2, 3};  char s[20] = "hi";  indexing a[i], m[i][j]; casts (double) x; sizeof(int) /
sizeof(a); literals 3.14, 2.5f, 'a', '\\n', "text".
VarDeclNode.dims and FunctionNode params hold array sizes (expression nodes, or None for []).
FunctionNode.params items are (type, name, line, column) or, for an array, (type, name, line, column, dims).
ScanfNode.names items are (name, line, column) for &x, or (name, line, column, indices, has_ampersand).
"""
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- AST
@dataclass
class Node: line: int = 1; column: int = 1; end_line: int = 0; end_column: int = 0
@dataclass
class ProgramNode(Node): statements: list = field(default_factory=list)
@dataclass
class VarDeclNode(Node): var_type: str = 'int'; name: str = ''; expr: object = None; dims: list = field(default_factory=list); const: bool = False
@dataclass
class AssignNode(Node): name: str = ''; expr: object = None; indices: list = field(default_factory=list); op: str = '='
"""op records how it was written: '=', '+=', '-=', '*=', '/=', '%=', '++' or '--'. expr is always the full new
value (x += 2 is stored as x = x + 2, i++ as i = i + 1), so every later stage only has to handle '='."""
@dataclass
class BinOpNode(Node): left: object = None; op: str = ''; right: object = None
@dataclass
class NumNode(Node): value: int = 0
@dataclass
class FloatNode(Node): value: float = 0.0; text: str = '0.0'
@dataclass
class CharNode(Node): value: int = 0; text: str = "'\\0'"
@dataclass
class StringNode(Node): value: str = ''
@dataclass
class IndexNode(Node): name: str = ''; indices: list = field(default_factory=list)
@dataclass
class CastNode(Node): to_type: str = 'int'; expr: object = None
@dataclass
class SizeofNode(Node): of_type: str = ''; expr: object = None
@dataclass
class InitListNode(Node): items: list = field(default_factory=list)
@dataclass
class VarNode(Node): name: str = ''
@dataclass
class CallNode(Node): name: str = ''; args: list = field(default_factory=list)
@dataclass
class BlockNode(Node): statements: list = field(default_factory=list)
@dataclass
class IfNode(Node): condition: object = None; then_branch: object = None; else_branch: object = None
@dataclass
class WhileNode(Node): condition: object = None; body: object = None
@dataclass
class ForNode(Node): init: object = None; condition: object = None; update: object = None; body: object = None
@dataclass
class ReturnNode(Node): expr: object = None
@dataclass
class ExprStmtNode(Node): expr: object = None
@dataclass
class PrintNode(Node): expr: object = None
@dataclass
class PrintfNode(Node): format: str = ''; args: list = field(default_factory=list)
@dataclass
class ScanfNode(Node): format: str = ''; names: list = field(default_factory=list)
@dataclass
class BreakNode(Node): pass
@dataclass
class ContinueNode(Node): pass
@dataclass
class DoWhileNode(Node): body: object = None; condition: object = None
@dataclass
class CaseNode(Node): value: object = None; statements: list = field(default_factory=list)
"""One 'case V:' (value is an expression) or 'default:' (value is None) and the statements under it."""
@dataclass
class SwitchNode(Node): expr: object = None; cases: list = field(default_factory=list)
@dataclass
class FunctionNode(Node): return_type: str = 'int'; name: str = ''; params: list = field(default_factory=list); body: object = None


from compiler.typesys import parse_format, parse_scanf_format, TYPE_KEYWORDS  # noqa: E402  (re-exported)


ASSIGN_OPS = ('=', '+=', '-=', '*=', '/=', '%=')
STEP_OPS = ('++', '--')


def make_assign(name, indices, op, rhs, line, column):
    """Build the AssignNode for name[indices] <op> rhs, with expr holding the complete new value."""
    import copy
    if op == '=':
        expr = rhs
    else:
        target = (IndexNode(line=line, column=column, name=name, indices=copy.deepcopy(indices)) if indices
                  else VarNode(line=line, column=column, name=name))
        if op in STEP_OPS:
            rhs = NumNode(line=line, column=column, value=1)
        expr = BinOpNode(line=line, column=column, left=target, op=op[0], right=rhs)
    return AssignNode(line=line, column=column, name=name, expr=expr, indices=indices, op=op)


def is_logical_not(n):
    """!x is parsed as (x == 0) with the 0 at the '!' position."""
    return (isinstance(n, BinOpNode) and n.op == '==' and isinstance(n.right, NumNode) and n.right.value == 0
            and n.right.line == n.line and n.right.column == n.column)


def is_unary_minus(n):
    """Unary minus is parsed as (0 - x); the 0 sits at the operator's own position."""
    return (isinstance(n, BinOpNode) and n.op == '-' and isinstance(n.left, NumNode) and n.left.value == 0
            and n.left.line == n.line and n.left.column == n.column)


# ------------------------------------------------------------------------- errors
class ParseError(SyntaxError):
    def __init__(self, message, token, expected=None):
        super().__init__(message)
        self.line, self.column, self.found, self.expected = token.line, token.column, token.value, expected


class ParseErrors(SyntaxError):
    """All syntax errors in a file. str() is the first error (keeps old behaviour)."""
    def __init__(self, errors, ast, complete):
        super().__init__(errors[0]["message"])
        self.errors, self.ast, self.complete = errors, ast, complete


SYNC_KEYWORDS = {'if', 'while', 'for', 'return', 'print', 'do', 'switch', 'case', 'default', 'break', 'continue', 'const'} | set(TYPE_KEYWORDS)
MAX_ERRORS = 25
MAX_NESTING = 200            # deeper programs get a clear error instead of crashing the parser


def _hint(found, expected, open_braces, mismatched=()):
    """A beginner-oriented hint for common syntax mistakes."""
    if expected == "'&'":
        return "scanf stores into a variable, so it needs its address: write &x, e.g. scanf(\"%d\", &x);"
    if found == '&':
        return "'&' is only used inside scanf, e.g. scanf(\"%d\", &x);. For logical AND write &&."
    if found in ('++', '--'):
        return (f"{found} works as a statement of its own (i{found}; or in a for header), not inside an expression. "
                f"Write the {found} on its own line, e.g. a[i] = x; i{found};")
    if found in ('+=', '-=', '*=', '/=', '%='):
        return f"{found} is a statement of its own (x {found} 2;), not part of an expression."
    if found == '=' and expected in ("')'", "';'", "an expression"):
        return "Did you mean == ? A single = stores a value; == compares two values."
    if expected == "'case'":
        return "Inside a switch every statement belongs to a case: start with case 1: (or default:) and end each case with break;"
    if expected == "a type such as 'int'" and found not in TYPE_KEYWORDS:
        return "const goes before the type: const int SIZE = 10;"
    if found == ':' or expected == "':'":
        return "A case label ends with a colon: case 1: ... break;"
    if found == 'EOF' and expected == "'}'" and mismatched:
        line, close = mismatched[-1]
        return (f"The '{{' on line {line} is probably missing its '}}': the '}}' on line {close} lines up with an "
                f"outer block, so every block after it is off by one. Add a '}}' to close line {line}'s block.")
    if found == 'EOF' and expected == "'}'" and open_braces:
        return f"The '{{' on line {open_braces[-1]} is never closed: add a matching '}}'."
    if found == 'else':
        return "This 'else' has no matching 'if' just before it, or the 'if' statement above it is broken."
    if expected == "';'":
        return "Statements end with ';'. The real mistake is often at the end of the previous line."
    if expected == "')'":
        return "Every '(' needs a matching ')'."
    if found in ('unsigned', 'signed', 'struct', 'bool', 'string', 'enum', 'typedef', 'goto', 'union', 'static', 'extern'):
        return f"'{found}' is not part of Mini C. Types are char, short, int, long, float and double; text is a char array, e.g. char name[20];"
    if found == '[' and expected == "';'":
        return "Array sizes go after the name: int a[5]; (not int[5] a;)."
    if expected == "an array size":
        return "Write the number of elements, e.g. int a[10]; For a list of values you may leave it empty: int a[] = {1, 2, 3};"
    if expected == "an expression":
        return "A value is missing here: an operator or '=' needs something on both sides."
    if expected and expected.startswith("a format string"):
        return 'Write printf("%d\\n", x); with the text in double quotes first, then the values.'

    return None


def _show(value):
    return "end of file" if value == "EOF" else repr(value)


class Parser:
    PRECEDENCE = {'||': 1, '&&': 2, '==': 3, '!=': 3, '<': 4, '>': 4, '<=': 4, '>=': 4,
                  '+': 5, '-': 5, '*': 6, '/': 6, '%': 6}

    def __init__(self, tokens):
        self.tokens, self.pos = tokens, 0
        self.errors, self.panicked = [], False
        self.open_braces = []
        self.mismatched = []                     # (line of '{', line of its '}') whose indentation disagree
        self.indent = {}
        self.depth = 0                           # statement / expression nesting, to stop runaway recursion
        for tok in tokens:
            self.indent.setdefault(tok.line, tok.column)

    # ------------------------------------------------------------- helpers
    def peek(self, offset=0): return self.tokens[min(self.pos + offset, len(self.tokens) - 1)]
    def prev(self): return self.tokens[self.pos - 1] if self.pos else self.tokens[0]

    def match(self, value=None, kind=None):
        t = self.peek()
        return (value is None or t.value == value) and (kind is None or t.kind == kind)

    def _expected_text(self, value, kind):
        return repr(value) if value is not None else {'IDENTIFIER': 'a name', 'KEYWORD': 'a type'}.get(kind, kind or 'valid token')

    def consume(self, value=None, kind=None):
        t = self.peek()
        if self.match(value, kind):
            self.pos += 1
            return t
        expected = self._expected_text(value, kind)
        msg = f"Expected {repr(value) if value is not None else kind or 'valid token'}, got {t.value!r} at line {t.line}, column {t.column}"
        # phrase-level recovery: insert an obviously missing token and keep going
        p = self.prev()
        insert = ((value == ';' and (t.line > p.line or t.value in ('}', 'EOF')))
                  or (value == ')' and t.value in ('{', ';')))
        if insert and self.pos > 0:
            at_line, at_col = p.line, p.column + len(p.value)
            self.errors.append({
                "category": "Syntax Error", "message": msg, "line": t.line, "column": t.column,
                "expected": expected, "found": t.value, "fix_line": at_line, "fix_column": at_col,
                "recovery": f"Assumed a missing {value!r} after {_show(p.value)} on line {at_line} and continued.",
                "recovery_kind": "insert", "hint": _hint(t.value, expected, self.open_braces, self.mismatched)})
            return p
        raise ParseError(msg, t, expected)

    def _record(self, err, start):
        """Panic-mode recovery after a ParseError raised inside statement starting at `start`."""
        err_index = self.pos
        resume = self._synchronize(start)
        skipped = [t.value for t in self.tokens[err_index:self.pos] if t.kind != 'EOF']
        nxt = self.peek()
        where = "the end of the file" if nxt.kind == 'EOF' else f"line {nxt.line} ({_show(nxt.value)})"
        text = (f"Skipped {' '.join(skipped[:8])}{' ...' if len(skipped) > 8 else ''} and resumed at {where}."
                if skipped else f"Resumed at {where}.")
        if self.errors and self.errors[-1]["line"] == err.line and self.errors[-1]["column"] == err.column:
            return  # same spot as the previous error: a cascade, not a new mistake
        self.errors.append({"category": "Syntax Error", "message": str(err), "line": err.line, "column": err.column,
                            "expected": err.expected, "found": err.found, "recovery": text, "recovery_kind": resume,
                            "hint": _hint(err.found, err.expected, self.open_braces, self.mismatched)})
        self.panicked = True

    def _synchronize(self, start):
        while not self.match(kind='EOF'):
            t = self.peek()
            if t.value == ';':
                self.pos += 1
                return "panic"
            if t.value == '}':
                if self.pos == start:          # stray '}' with nothing else: drop it
                    self.pos += 1
                return "panic"
            if t.value == '{':                 # skip a whole block: it belongs to the broken statement
                depth = 0
                while not self.match(kind='EOF'):
                    v = self.peek().value
                    self.pos += 1
                    if v == '{': depth += 1
                    elif v == '}':
                        depth -= 1
                        if depth == 0: break
                return "panic"
            if (t.kind == 'KEYWORD' and t.value in SYNC_KEYWORDS and self.pos > start
                    and t.line > self.prev().line):
                return "panic"
            self.pos += 1
        return "panic"

    # ---------------------------------------------------------- statements
    def parse(self):
        stmts = self._statement_list(top=True)
        ast = ProgramNode(line=1, column=1, statements=stmts)
        if self.errors:
            raise ParseErrors(self.errors, ast, complete=not self.panicked)
        return ast

    def _statement_list(self, top, stop=('}',)):
        out = []
        while not self.match(kind='EOF') and (top or self.peek().value not in stop):
            start = self.pos
            try:
                node = self.statement(allow_list=True)
                out.extend(node if isinstance(node, list) else [node])
            except ParseError as e:
                self._record(e, start)
                if len(self.errors) >= MAX_ERRORS:
                    self.pos = len(self.tokens) - 1
                    break
        return out

    def statement(self, allow_list=False):
        first = self.peek()
        if self.depth > MAX_NESTING:
            raise ParseError(f"The program is nested too deeply at line {first.line} (more than {MAX_NESTING} levels)", first, "less nesting")
        self.depth += 1
        try:
            node = self._statement()
        finally:
            self.depth -= 1
        last = self.prev()
        for n in (node if isinstance(node, list) else [node]):
            n.end_line, n.end_column = last.line, last.column + len(last.value) - 1
            if not n.line:
                n.line, n.column = first.line, first.column
        if isinstance(node, list) and not allow_list:
            node = BlockNode(line=first.line, column=first.column, statements=node, end_line=last.line,
                             end_column=last.column + len(last.value) - 1)
        return node

    def _is_type(self, offset=0):
        t = self.peek(offset)
        return t.kind == 'KEYWORD' and t.value in TYPE_KEYWORDS

    def _dims(self, allow_empty_first):
        """[N][M] after a name. Returns a list of expression nodes (None for an empty first [])."""
        dims = []
        while self.match(value='['):
            b = self.consume('[')
            if self.match(value=']'):
                if dims or not allow_empty_first:
                    t = self.peek()
                    raise ParseError(f"Expected an array size, got ']' at line {t.line}, column {t.column}", t, "an array size")
                dims.append(None)
            else:
                dims.append(self.expression())
            self.consume(']')
        return dims

    def _initializer(self):
        """An expression, a string, or { items } (nested for 2-D arrays)."""
        if not self.match(value='{'):
            return self.expression()
        t = self.consume('{')
        items = []
        while not self.match(value='}'):
            items.append(self._initializer())
            if not self.match(value=','):
                break
            self.consume(',')
        self.consume('}')
        return InitListNode(line=t.line, column=t.column, items=items)

    def _indices(self):
        out = []
        while self.match(value='['):
            self.consume('['); out.append(self.expression()); self.consume(']')
        return out

    def _assign_ahead(self):
        """Does an assignment start here?  x = / x += / x++ / a[i][j] -= / ++x / --a[i]"""
        if self.peek().value in STEP_OPS and self.peek(1).kind == 'IDENTIFIER':
            return True
        if self.peek().kind != 'IDENTIFIER':
            return False
        k, depth = 1, 0
        while True:
            v = self.peek(k).value
            if v == '[':
                depth += 1
            elif v == ']':
                depth -= 1
            elif depth == 0:
                return v in ASSIGN_OPS or v in STEP_OPS
            elif self.peek(k).kind == 'EOF':
                return False
            k += 1

    def _assignment(self):
        """name = expr, name op= expr, name++, ++name (and the same on name[i][j]).
        Used by statements and for-loop headers."""
        t = self.peek()
        if t.value in STEP_OPS:                       # ++x / --a[i]
            op = self.consume().value
            n = self.consume(kind='IDENTIFIER')
            return make_assign(n.value, self._indices(), op, None, t.line, t.column)
        n = self.consume(kind='IDENTIFIER')
        idx = self._indices()
        op = self.peek().value
        if op in STEP_OPS:
            self.consume()
            return make_assign(n.value, idx, op, None, n.line, n.column)
        if op not in ASSIGN_OPS:
            bad = self.peek()
            raise ParseError(f"Expected '=', got {bad.value!r} at line {bad.line}, column {bad.column}", bad, "'='")
        self.consume()
        return make_assign(n.value, idx, op, self.expression(), n.line, n.column)

    def _declaration(self, t, typ, const):
        """The rest of  type name[dims] = init, name2 = ...;  (one or more variables)."""
        decls = []
        while True:
            name = self.consume(kind='IDENTIFIER')
            dims = self._dims(allow_empty_first=True)
            expr = None
            if self.match(value='='):
                self.consume('=')
                expr = self._initializer()
            at = t if not decls else name
            decls.append(VarDeclNode(line=at.line, column=at.column, var_type=typ, name=name.value, expr=expr, dims=dims, const=const))
            if not self.match(value=','):
                break
            self.consume(',')
        self.consume(';')
        return decls[0] if len(decls) == 1 else decls

    def _statement(self):
        t = self.peek()
        if self.match(value='{'):
            self.consume('{')
            self.open_braces.append(t.line)
            ss = self._statement_list(top=False)
            self.consume('}')
            self.open_braces.pop()
            close = self.prev()
            if close.value == '}' and close.column != self.indent.get(t.line, close.column) and close.line != t.line:
                self.mismatched.append((t.line, close.line))
            return BlockNode(line=t.line, column=t.column, statements=ss)
        if self.match(value='const'):
            self.consume('const')
            if not self._is_type() or self.peek().value == 'void':
                b = self.peek()
                raise ParseError(f"Expected a type after 'const', got {b.value!r} at line {b.line}, column {b.column}", b, "a type such as 'int'")
            typ = self.consume().value
            return self._declaration(t, typ, const=True)
        if self._is_type():
            typ = self.consume().value
            name = self.peek()
            if self.peek(1).value != '(':
                return self._declaration(t, typ, const=False)
            name = self.consume(kind='IDENTIFIER')
            if self.match(value='('):
                self.consume('(')
                params = []
                if not self.match(value=')'):
                    while True:
                        ptype = self.peek()
                        if not self._is_type():
                            raise ParseError(f"Unsupported parameter type {ptype.value!r} at line {ptype.line}, column {ptype.column}", ptype, "a type such as 'int'")
                        self.consume()
                        if ptype.value == 'void' and self.match(value=')'):
                            break
                        pname = self.consume(kind='IDENTIFIER')
                        pdims = self._dims(allow_empty_first=True)
                        params.append((ptype.value, pname.value, pname.line, pname.column) +
                                      ((pdims,) if pdims else ()))
                        if not self.match(value=','):
                            break
                        self.consume(',')
                self.consume(')')
                if not self.match(value='{'):
                    b = self.peek()
                    raise ParseError(f"Function body must be a block at line {b.line}, column {b.column}", b, "'{'")
                body = self.statement()
                return FunctionNode(line=t.line, column=t.column, return_type=typ, name=name.value, params=params, body=body)
        if self.match(value='if'):
            self.consume('if'); self.consume('('); cond = self.expression(); self.consume(')')
            yes = self.statement(); no = None
            if self.match(value='else'):
                self.consume('else'); no = self.statement()
            return IfNode(line=t.line, column=t.column, condition=cond, then_branch=yes, else_branch=no)
        if self.match(value='while'):
            self.consume('while'); self.consume('('); cond = self.expression(); self.consume(')')
            return WhileNode(line=t.line, column=t.column, condition=cond, body=self.statement())
        if self.match(value='do'):
            self.consume('do')
            body = self.statement()
            self.consume('while'); self.consume('('); cond = self.expression(); self.consume(')'); self.consume(';')
            return DoWhileNode(line=t.line, column=t.column, body=body, condition=cond)
        if self.match(value='break') or self.match(value='continue'):
            word = self.consume().value
            self.consume(';')
            return (BreakNode if word == 'break' else ContinueNode)(line=t.line, column=t.column)
        if self.match(value='switch'):
            self.consume('switch'); self.consume('('); e = self.expression(); self.consume(')')
            self.consume('{')
            self.open_braces.append(t.line)
            cases = []
            while not self.match(value='}') and not self.match(kind='EOF'):
                c = self.peek()
                if self.match(value='case') or self.match(value='default'):
                    self.consume()
                    value = self.expression() if c.value == 'case' else None
                    self.consume(':')
                    cases.append(CaseNode(line=c.line, column=c.column, value=value))
                    continue
                if not cases:
                    raise ParseError(f"Expected 'case' or 'default', got {c.value!r} at line {c.line}, column {c.column}", c, "'case'")
                cases[-1].statements.extend(self._statement_list(top=False, stop=('}', 'case', 'default')))
            self.consume('}')
            self.open_braces.pop()
            return SwitchNode(line=t.line, column=t.column, expr=e, cases=cases)
        if self.match(value='for'):
            self.consume('for'); self.consume('(')
            init = cond = update = None
            if not self.match(value=';'):
                if self._is_type() or self.match(value='const'):
                    is_const = self.match(value='const')
                    if is_const:
                        self.consume('const')
                    q = self.consume(); n = self.consume(kind='IDENTIFIER'); e = None
                    if self.match(value='='):
                        self.consume('='); e = self.expression()
                    init = VarDeclNode(line=q.line, column=q.column, var_type=q.value, name=n.value, expr=e, const=is_const)
                else:
                    init = self._assignment()
            self.consume(';')
            if not self.match(value=';'):
                cond = self.expression()
            self.consume(';')
            if not self.match(value=')'):
                update = self._assignment()
            self.consume(')')
            return ForNode(line=t.line, column=t.column, init=init, condition=cond, update=update, body=self.statement())
        if self.match(value='return'):
            self.consume('return')
            e = None if self.match(value=';') or self.match(value='}') else self.expression()
            self.consume(';')
            return ReturnNode(line=t.line, column=t.column, expr=e)
        if self.match(value='print'):
            self.consume('print'); self.consume('('); e = self.expression(); self.consume(')'); self.consume(';')
            return PrintNode(line=t.line, column=t.column, expr=e)
        if self.match(kind='IDENTIFIER') and t.value == 'printf' and self.peek(1).value == '(':
            self.consume(); self.consume('(')
            fmt = self.peek()
            if fmt.kind != 'STRING':
                raise ParseError(f"printf needs a format string in double quotes first, got {fmt.value!r} at line {fmt.line}, column {fmt.column}",
                                 fmt, 'a format string such as "%d\\n"')
            self.consume()
            args = []
            while self.match(value=','):
                self.consume(','); args.append(self.expression())
            self.consume(')'); self.consume(';')
            return PrintfNode(line=t.line, column=t.column, format=fmt.value[1:-1], args=args)
        if self.match(kind='IDENTIFIER') and t.value == 'scanf' and self.peek(1).value == '(':
            self.consume(); self.consume('(')
            fmt = self.peek()
            if fmt.kind != 'STRING':
                raise ParseError(f"scanf needs a format string in double quotes first, got {fmt.value!r} at line {fmt.line}, column {fmt.column}",
                                 fmt, 'a format string such as "%d"')
            self.consume()
            names = []
            while self.match(value=','):
                self.consume(',')
                amp = self.peek()
                has_amp = amp.value == '&'
                if has_amp:
                    self.consume('&')
                elif amp.kind != 'IDENTIFIER':
                    raise ParseError(f"Expected '&', got {amp.value!r} at line {amp.line}, column {amp.column}", amp, "'&'")
                n = self.consume(kind='IDENTIFIER')
                idx = self._indices()
                names.append((n.value, n.line, n.column) if has_amp and not idx else (n.value, n.line, n.column, idx, has_amp))
            self.consume(')'); self.consume(';')
            return ScanfNode(line=t.line, column=t.column, format=fmt.value[1:-1], names=names)
        if self.match(value=';'):
            self.consume(';')
            return BlockNode(line=t.line, column=t.column, statements=[])
        if self._assign_ahead():
            a = self._assignment()
            self.consume(';')
            return a
        e = self.expression()
        self.consume(';')
        return ExprStmtNode(line=t.line, column=t.column, expr=e)

    # ---------------------------------------------------------- expressions
    def expression(self): return self._binary(1)

    def _binary(self, minp):
        if self.depth > MAX_NESTING:
            t = self.peek()
            raise ParseError(f"The expression is nested too deeply at line {t.line} (more than {MAX_NESTING} levels)", t, "a simpler expression")
        self.depth += 1
        try:
            return self._binary_inner(minp)
        finally:
            self.depth -= 1

    def _binary_inner(self, minp):
        left = self.unary()
        while self.peek().value in self.PRECEDENCE and self.PRECEDENCE[self.peek().value] >= minp:
            op = self.consume(); p = self.PRECEDENCE[op.value]; right = self._binary(p + 1)
            left = BinOpNode(line=op.line, column=op.column, left=left, op=op.value, right=right)
        return left

    def unary(self):
        if self.depth > MAX_NESTING:
            t = self.peek()
            raise ParseError(f"The expression is nested too deeply at line {t.line} (more than {MAX_NESTING} levels)", t, "a simpler expression")
        self.depth += 1
        try:
            return self._unary_inner()
        finally:
            self.depth -= 1

    def _unary_inner(self):
        if self.match(value='!'):
            t = self.consume('!')
            operand = self.unary()
            return BinOpNode(line=t.line, column=t.column, left=operand, op='==', right=NumNode(line=t.line, column=t.column, value=0))
        if self.match(value='-'):
            t = self.consume('-')
            return BinOpNode(line=t.line, column=t.column, left=NumNode(line=t.line, column=t.column, value=0), op='-', right=self.unary())
        t = self.peek()
        if self.match(value='(') and self._is_type(1):              # cast: (double) x
            self.consume('('); typ = self.consume().value; self.consume(')')
            return CastNode(line=t.line, column=t.column, to_type=typ, expr=self.unary())
        if self.match(value='sizeof'):
            self.consume(); self.consume('(')
            if self._is_type():
                typ = self.consume().value; self.consume(')')
                return SizeofNode(line=t.line, column=t.column, of_type=typ)
            e = self.expression(); self.consume(')')
            return SizeofNode(line=t.line, column=t.column, expr=e)
        if self.match(kind='NUMBER'):
            self.consume()
            return NumNode(line=t.line, column=t.column, value=int(t.value))
        if self.match(kind='FLOAT'):
            self.consume()
            return FloatNode(line=t.line, column=t.column, value=float(t.value.rstrip('fF')), text=t.value)
        if self.match(kind='CHAR'):
            from compiler.typesys import char_value
            self.consume()
            return CharNode(line=t.line, column=t.column, value=char_value(t.value)[0] or 0, text=t.value)
        if self.match(kind='STRING'):
            self.consume()
            return StringNode(line=t.line, column=t.column, value=t.value[1:-1])
        if self.match(kind='IDENTIFIER'):
            self.consume()
            if self.match(value='('):                       # function call
                self.consume('(')
                args = []
                if not self.match(value=')'):
                    args.append(self.expression())
                    while self.match(value=','):
                        self.consume(','); args.append(self.expression())
                self.consume(')')
                return CallNode(line=t.line, column=t.column, name=t.value, args=args)
            node = (IndexNode(line=t.line, column=t.column, name=t.value, indices=self._indices()) if self.match(value='[')
                    else VarNode(line=t.line, column=t.column, name=t.value))
            if self.peek().value in STEP_OPS or self.peek().value in ASSIGN_OPS[1:]:
                bad = self.peek()
                raise ParseError(f"Unexpected {bad.value!r} inside an expression at line {bad.line}, column {bad.column}", bad, "an operator such as + or ;")
            return node
        if self.match(value='('):
            self.consume('('); e = self.expression(); self.consume(')')
            return e
        if t.value in ('++', '--'):
            raise ParseError(f"Unexpected token {t.value!r} in expression at line {t.line}, column {t.column}", t, "an expression")
        raise ParseError(f"Unexpected token {t.value!r} in expression at line {t.line}, column {t.column}", t, "an expression")
