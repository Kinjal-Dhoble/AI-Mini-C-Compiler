"""Scope-aware semantic checks and type checking for Mini C.

Checks: declarations and scopes, duplicate names, undeclared variables/functions, call
argument counts and types, using a void result as a value, return statements that do not
match the function's return type, possibly-uninitialised reads, and the data rules:
  * types char short int long float double; numbers convert into each other like in C;
  * arrays: constant sizes, initialiser lists that fit, indexes that are whole numbers, no
    assigning a whole array with '=', arrays passed only to array parameters;
  * strings: char arrays and "text"; library functions strlen, strcpy, strcmp, ... check that
    they get strings; printf / scanf conversions (%d %ld %f %lf %c %s) must match the values.
Every expression node gets a `ctype` attribute (its type as a string, see compiler.typesys),
which the bug detector uses.
"""
from compiler.parser import *
from compiler import typesys as T

MAX_ARRAY_ELEMENTS = 1_000_000     # bigger arrays are rejected instead of exhausting memory

SUGGESTIONS = [
    ("undeclared variable", "Declare the variable before using it and check the spelling and scope."),
    ("undeclared function", "Define the function above or below main, or check the spelling of its name."),
    ("expects", "Call the function with exactly as many arguments as it has parameters."),
    ("duplicate declaration", "Use a different variable name or remove the duplicate declaration in this scope."),
    ("duplicate function", "Rename one of the functions; every function needs a unique name."),
    ("whole array", "Arrays cannot be copied with '='. Copy element by element in a loop, or use strcpy(dest, src) for strings."),
    ("array size", "Write the number of elements as a whole number, e.g. int marks[10];"),
    ("not an array", "Only variables declared with [ ] can be indexed, e.g. int a[5]; a[2] = 7;"),
    ("index", "An array index must be a whole number (int or char) between 0 and size - 1."),
    ("initialis", "List the values in braces, e.g. int a[3] = {1, 2, 3}; or char s[6] = \"hello\";"),
    ("too long", "Make the array bigger: a string needs one extra place for the '\\0' at its end."),
    ("single quotes", "Use 'a' (single quotes) for one character and \"abc\" (double quotes) for text."),
    ("addresses", "Compare strings with strcmp(a, b) == 0, not with ==."),
    ("needs a string", "Pass a char array (char name[20]) or a string in double quotes."),
    ("void", "A void function does not produce a value: call it on its own line, or make it return a value."),
    ("cannot return a value", "Remove the value after 'return', or change the function's return type."),
    ("cannot initialize", "Make the types on both sides compatible."),
    ("cannot assign", "Make the types on both sides compatible."),
    ("incompatible operand types", "Both sides of the operator must be numbers."),
    ("% only", "Use % with whole numbers only (int, long, char)."),
    ("top level", "Move this function outside of any other function."),
    ("division by constant zero", "Change the divisor so it cannot be zero."),
    ("scanf", 'Match each conversion to its variable: %d int, %ld long, %f float, %lf double, %c char, %s string (no &). Example: scanf("%d %s", &age, name);'),
    ("printf", 'Match each placeholder to its value: %d int, %ld long, %f float/double, %c char, %s string. Example: printf("%s is %d\\n", name, age);'),
    ("print()", 'print(x) is for whole numbers. Use printf("%f\\n", x) for decimals and printf("%s\\n", s) for text.'),
    ("argument", "Check the types of the values you pass: arrays go to array parameters ([]), numbers to number parameters."),
]


def _a(t):
    return ("an " if t[:1] in "aeiou" else "a ") + t


def const_value(n):
    """Value of a constant whole-number expression (5, 2 * 10, sizeof(int)), else None."""
    if isinstance(n, NumNode):
        return n.value
    if isinstance(n, CharNode):
        return n.value
    if isinstance(n, SizeofNode) and n.of_type:
        return T.sizeof(n.of_type)
    if isinstance(n, BinOpNode) and n.op in "+-*/%":
        a, b = const_value(n.left), const_value(n.right)
        if a is None or b is None or (n.op in "/%" and b == 0):
            return None
        if n.op == "+":
            return a + b
        if n.op == "-":
            return a - b
        if n.op == "*":
            return a * b
        q = abs(a) // abs(b) * (1 if (a < 0) == (b < 0) else -1)    # C division rounds toward zero
        return q if n.op == "/" else a - q * b
    return None


EXPR_NODES = (NumNode, FloatNode, CharNode, StringNode, VarNode, IndexNode, CastNode, SizeofNode,
              CallNode, BinOpNode, InitListNode)


class SemanticAnalyzer:
    def __init__(self):
        self.diagnostics = []; self.scopes = [{}]; self.functions = {}
        self.reads = set(); self.writes = set(); self.current = None
        self.flow = []          # enclosing 'loop' / 'switch' statements, for break and continue

    def error(self, node, msg, category='Semantic Error'):
        lower = msg.lower()
        suggestion = next((s for k, s in SUGGESTIONS if k in lower),
                          "Review the statement at this location and correct the reported problem.")
        self.diagnostics.append({'category': category, 'message': msg, 'line': node.line,
                                 'column': node.column, 'suggestion': suggestion})

    def lookup(self, name):
        for s in reversed(self.scopes):
            if name in s:
                return s[name]
        return None

    # ------------------------------------------------------------ declarations
    def _dims(self, node, dims, where):
        """Check array sizes; returns a tuple of ints (None for an empty first size)."""
        out = []
        for k, d in enumerate(dims):
            if d is None:
                if k > 0:
                    self.error(node, f"Only the first array size of '{where}' may be left empty: write every other array size")
                out.append(None)
                continue
            self.visit(d)
            v = const_value(d)
            if v is None:
                self.error(node, f"The array size of '{where}' must be a constant number (Mini C has no variable-length arrays), e.g. int {where}[10];")
                out.append(1)
            elif v <= 0:
                self.error(node, f"The array size of '{where}' must be at least 1, not {v}")
                out.append(1)
            else:
                out.append(v)
        total = 1
        for v in out:
            total *= v or 1
        if total > MAX_ARRAY_ELEMENTS:
            self.error(node, f"The array '{where}' has {total:,} elements, more than the {MAX_ARRAY_ELEMENTS:,} Mini C allows "
                             f"(a real C program would usually crash with a stack overflow here)")
            out = [1] * len(out)
        return tuple(out)

    def _param_type(self, p):
        if len(p) > 4 and p[4]:
            return T.make(p[0], (None,) + tuple(const_value(d) or 1 for d in p[4][1:]))
        return p[0]

    def analyze(self, program):
        # Register every function signature first so functions can call each other in any order.
        for n in program.statements:
            if isinstance(n, FunctionNode):
                if n.name in self.functions:
                    self.error(n, f"Duplicate function '{n.name}'")
                self.functions[n.name] = (n.return_type, [self._param_type(p) for p in n.params])
        for n in program.statements:
            self.visit(n)
        self._unread(self.scopes[0])
        return self.diagnostics

    def _unread(self, scope):
        for name, meta in scope.items():
            if name not in self.reads:
                self.diagnostics.append({'category': 'Warning', 'message': f"Variable '{name}' is never read",
                                         'line': meta['line'], 'column': meta['column']})

    def _string_init(self, n, base, dims, s):
        text, errs = T.decode(s.value)
        for e in errs:
            self.error(s, f"String: {e}", 'Lexical Error')
        need = len(text) + 1
        if base != "char" or len(dims) != 1:
            self.error(n, f"Cannot initialize {T.describe(T.make(base, dims))} '{n.name}' with the string \"{s.value}\"", 'Type Error')
            return dims
        if dims[0] is None:
            return (need,)
        if len(text) > dims[0]:
            self.error(n, f"The string \"{s.value}\" ({len(text)} characters + '\\0') is too long for {n.name}[{dims[0]}]", 'Type Error')
        elif len(text) == dims[0]:
            self.error(n, f"char {n.name}[{dims[0]}] = \"{s.value}\" leaves no room for the '\\0' that ends the string: "
                          f"strlen/printf(\"%s\") will read past the end. Use {n.name}[{need}]", 'Warning')
        return dims

    def _init_list(self, n, base, dims, lst, where):
        """Check an { ... } initialiser against the remaining dimensions. Returns the first size."""
        if len(dims) > 1 and all(not isinstance(x, (InitListNode, StringNode)) for x in lst.items):
            total = T.count((dims[0] or 1,) + dims[1:]) if dims[0] else None
            inner = T.count(dims[1:])
            for x in lst.items:
                self._scalar_init(n, base, x)
            first = dims[0] or -(-len(lst.items) // inner)
            if total is not None and len(lst.items) > total:
                self.error(lst, f"Too many initialisers for {where}: it holds {total} values, {len(lst.items)} were given", 'Type Error')
            return first
        if dims[0] is not None and len(lst.items) > dims[0]:
            self.error(lst, f"Too many initialisers for {where}: it holds {dims[0]} values, {len(lst.items)} were given", 'Type Error')
        for x in lst.items:
            if len(dims) == 1:
                if isinstance(x, (InitListNode, StringNode)):
                    self.error(x, f"Each value in the initialiser of {where} must be one {base}", 'Type Error')
                else:
                    self._scalar_init(n, base, x)
            elif isinstance(x, InitListNode):
                self._init_list(n, base, dims[1:], x, where)
            elif isinstance(x, StringNode) and base == "char" and len(dims) == 2:
                self._string_init(VarDeclNode(line=x.line, column=x.column, name=f"{n.name}[...]"), base, dims[1:], x)
            else:
                self.error(x, f"Each row of {where} needs its own {{ ... }} in the initialiser", 'Type Error')
        return dims[0] or len(lst.items)

    def _scalar_init(self, n, base, x):
        t = self.visit(x)
        if t == 'void':
            self.error(x, f"Cannot initialize {base} variable '{n.name}' with void", 'Type Error')
        elif T.is_array(t):
            self.error(x, f"Cannot initialize an element of '{n.name}' with {T.describe(t)}", 'Type Error')

    def _declare(self, n):
        base = n.var_type
        if base == 'void':
            self.error(n, f"Variable '{n.name}' cannot have type void", 'Type Error')
        dims = self._dims(n, n.dims, n.name) if n.dims else ()
        if dims:
            e = n.expr
            if isinstance(e, StringNode):
                dims = self._string_init(n, base, dims, e)
                e.ctype = T.make("char", (len(T.decode(e.value)[0]) + 1,))
            elif isinstance(e, InitListNode):
                first = self._init_list(n, base, dims, e, f"{n.name}")
                dims = (first,) + dims[1:]
            elif e is not None:
                self.visit(e)
                self.error(n, f"An array is initialised with {{ ... }} (or \"text\" for a char array), e.g. {base} {n.name}[3] = {{1, 2, 3}};", 'Type Error')
            elif dims[0] is None:
                self.error(n, f"The array size of '{n.name}' is missing: write {base} {n.name}[10]; or give the values: {base} {n.name}[] = {{1, 2, 3}};")
                dims = (1,) + dims[1:]
        typ = T.make(base, dims)
        if n.name in self.scopes[-1]:
            self.error(n, f"Duplicate declaration of '{n.name}'")
        else:
            self.scopes[-1][n.name] = {'type': typ, 'line': n.line, 'column': n.column,
                                       'initialized': n.expr is not None or bool(dims), 'param': False,
                                       'const': getattr(n, 'const', False)}
            if getattr(n, 'const', False) and n.expr is None:
                self.error(n, f"const variable '{n.name}' must be given its value when it is declared, e.g. const {base} {n.name} = 10;")
        n.ctype = typ
        if not dims and n.expr is not None:
            if isinstance(n.expr, InitListNode):
                self.error(n, f"'{n.name}' is a single {base}, so it cannot take a {{ ... }} list. Did you mean {base} {n.name}[] = {{...}};?", 'Type Error')
                return
            if isinstance(n.expr, StringNode) and base == 'char':
                self.visit(n.expr)
                self.error(n, f"Cannot initialize char variable '{n.name}' with a string: use single quotes for one character ('a'), or declare a char array: char {n.name}[20] = \"...\";", 'Type Error')
                return
            typ2 = self.visit(n.expr); self.writes.add(n.name)
            if typ2 == 'void':
                self.error(n, f"Cannot initialize {base} variable '{n.name}' with void", 'Type Error')
            elif T.is_array(typ2):
                self.error(n, f"Cannot initialize {base} variable '{n.name}' with {T.describe(typ2)}", 'Type Error')

    # ------------------------------------------------------------ lvalues
    def _index_type(self, node, name, indices, meta):
        t = meta['type']
        if not T.is_array(t):
            self.error(node, f"'{name}' is {t}, not an array, so {name}[...] cannot be used", 'Type Error')
            for i in indices:
                self.visit(i)
            return 'unknown'
        rank = len(T.parse(t)[1])
        if len(indices) > rank:
            self.error(node, f"'{name}' has {rank} dimension{'s' if rank > 1 else ''}, but {len(indices)} indexes were given", 'Type Error')
        for i in indices:
            it = self.visit(i)
            if it != 'unknown' and not T.is_integer(it):
                self.error(i, f"An array index must be a whole number, but this index is {T.describe(it) if T.is_array(it) else it}", 'Type Error')
        for _ in indices[:rank]:
            t = T.element(t)
        return t

    def visit(self, n):
        t = self._visit(n)
        if isinstance(n, EXPR_NODES):
            n.ctype = t
        return t

    def _visit(self, n):
        if n is None:
            return 'void'
        if isinstance(n, ProgramNode):
            for x in n.statements:
                self.visit(x)
        elif isinstance(n, BlockNode):
            self.scopes.append({})
            for x in n.statements:
                self.visit(x)
            self._unread(self.scopes.pop())
        elif isinstance(n, FunctionNode):
            if self.current is not None:
                self.error(n, f"Function '{n.name}' must be defined at the top level, not inside '{self.current.name}'")
                return 'void'
            self.current = n
            self.flow = []
            self.scopes.append({})
            for p in n.params:
                typ, name, line, col = p[:4]
                if typ == 'void' and len(p) <= 4:
                    continue
                ptype = typ
                if len(p) > 4 and p[4]:
                    dims = self._dims(n, p[4], name)
                    ptype = T.make(typ, (None,) + dims[1:])
                elif typ == 'void':
                    self.error(n, f"Parameter '{name}' cannot have type void", 'Type Error')
                self.scopes[-1][name] = {'type': ptype, 'line': line, 'column': col, 'initialized': True, 'param': True}
                self.writes.add(name)
            self.visit(n.body)
            self._unread(self.scopes.pop())
            self.current = None
        elif isinstance(n, VarDeclNode):
            self._declare(n)
        elif isinstance(n, AssignNode):
            meta = self.lookup(n.name)
            if not meta:
                self.error(n, f"Undeclared variable '{n.name}'"
                           + (" (it is a function name)" if n.name in self.functions else ""))
                for i in n.indices:
                    self.visit(i)
                self.visit(n.expr)
                return 'void'
            if meta.get('const'):
                how = {"++": "increase", "--": "decrease"}.get(getattr(n, 'op', '='), "change")
                self.error(n, f"Cannot {how} '{n.name}': it is declared const, so its value is fixed")
            target = self._index_type(n, n.name, n.indices, meta) if n.indices else meta['type']
            if n.indices:
                self.reads.add(n.name)
            if isinstance(n.expr, StringNode) and target in T.INTEGER:
                self.visit(n.expr)
                self.error(n, f"Cannot assign a string to {'an element of ' + n.name if n.indices else n.name}: use single quotes for one character, e.g. 'a'", 'Type Error')
                return 'void'
            typ = self.visit(n.expr); self.writes.add(n.name)
            if not n.indices:
                meta['initialized'] = True
            if T.is_array(target):
                what = f"{n.name}" + "".join("[...]" for _ in n.indices)
                extra = f" For text use strcpy({n.name}, ...);" if T.is_string(target) else ""
                self.error(n, f"Cannot assign to the whole array '{what}' with '=': copy the elements one by one in a loop.{extra}", 'Type Error')
            elif typ == 'void':
                self.error(n, f"Cannot assign void to {target} variable '{n.name}'", 'Type Error')
            elif T.is_array(typ):
                self.error(n, f"Cannot assign {T.describe(typ)} to {target} variable '{n.name}'", 'Type Error')
        elif isinstance(n, VarNode):
            meta = self.lookup(n.name); self.reads.add(n.name)
            if not meta:
                extra = f" (did you mean to call {n.name}(...)?)" if n.name in self.functions else ""
                self.error(n, f"Undeclared variable '{n.name}'{extra}")
                return 'unknown'
            if not meta['initialized']:
                self.error(n, f"Variable '{n.name}' may be used before initialization", 'Warning')
            n.is_param = meta.get('param', False)
            return meta['type']
        elif isinstance(n, IndexNode):
            meta = self.lookup(n.name); self.reads.add(n.name)
            if not meta:
                self.error(n, f"Undeclared variable '{n.name}'")
                for i in n.indices:
                    self.visit(i)
                return 'unknown'
            return self._index_type(n, n.name, n.indices, meta)
        elif isinstance(n, NumNode):
            return T.int_type_of(n.value)
        elif isinstance(n, FloatNode):
            return 'float' if n.text[-1:] in 'fF' else 'double'
        elif isinstance(n, CharNode):
            return 'char'
        elif isinstance(n, StringNode):
            text, errs = T.decode(n.value)
            for e in errs:
                self.error(n, f"String: {e}", 'Lexical Error')
            return T.make('char', (len(text) + 1,))
        elif isinstance(n, CastNode):
            t = self.visit(n.expr)
            if T.is_array(t):
                self.error(n, f"Cannot convert {T.describe(t)} to {n.to_type}: cast one element instead", 'Type Error')
            elif t == 'void' and n.to_type != 'void':
                self.error(n, f"Cannot convert the result of a void function to {n.to_type}", 'Type Error')
            return n.to_type
        elif isinstance(n, SizeofNode):
            if n.expr is not None:
                if isinstance(n.expr, (VarNode, IndexNode)) and self.lookup(n.expr.name):
                    meta = self.lookup(n.expr.name)
                    t = meta['type'] if isinstance(n.expr, VarNode) else self._index_type(n.expr, n.expr.name, n.expr.indices, meta)
                    n.expr.ctype = t
                    n.size = T.sizeof(t, is_param=meta.get('param') and isinstance(n.expr, VarNode))
                    n.expr.is_param = meta.get('param', False)
                else:
                    t = self.visit(n.expr)
                    n.size = T.sizeof(t) if t not in ('unknown', 'void') else 4
            else:
                n.size = T.sizeof(n.of_type)
            return 'int'
        elif isinstance(n, InitListNode):
            for x in n.items:
                self.visit(x)
            self.error(n, "A { ... } list can only initialise an array in its declaration", 'Type Error')
            return 'unknown'
        elif isinstance(n, CallNode):
            return self._call(n)
        elif isinstance(n, BinOpNode):
            return self._binop(n)
        elif isinstance(n, IfNode):
            self._condition(n.condition); self.visit(n.then_branch); self.visit(n.else_branch)
        elif isinstance(n, WhileNode):
            self._condition(n.condition); self._loop_body(n.body)
        elif isinstance(n, ForNode):
            self.visit(n.init); self._condition(n.condition); self.visit(n.update); self._loop_body(n.body)
        elif isinstance(n, DoWhileNode):
            self._loop_body(n.body); self._condition(n.condition)
        elif isinstance(n, (BreakNode, ContinueNode)):
            word = "break" if isinstance(n, BreakNode) else "continue"
            if word == "break" and not self.flow:
                self.error(n, "'break' can only be used inside a loop or a switch")
            elif word == "continue" and "loop" not in self.flow:
                self.error(n, "'continue' can only be used inside a loop" + (" (a switch is not a loop)" if self.flow else ""))
        elif isinstance(n, SwitchNode):
            self._switch(n)
        elif isinstance(n, ReturnNode):
            typ = self.visit(n.expr) if n.expr is not None else None
            fn = self.current
            if fn is None:
                self.error(n, "'return' can only be used inside a function")
            elif fn.return_type == 'void' and n.expr is not None:
                self.error(n, f"void function '{fn.name}' cannot return a value", 'Type Error')
            elif fn.return_type != 'void' and n.expr is None:
                self.error(n, f"Function '{fn.name}' should return an {fn.return_type} value here" if fn.return_type == 'int'
                           else f"Function '{fn.name}' should return a {fn.return_type} value here", 'Warning')
            elif typ == 'void':
                self.error(n, f"Cannot return the result of a void function from '{fn.name}'", 'Type Error')
            elif typ and T.is_array(typ):
                self.error(n, f"'{fn.name}' cannot return {T.describe(typ)}: functions return one value. Pass the array as a parameter and fill it instead", 'Type Error')
        elif isinstance(n, PrintNode):
            t = self.visit(n.expr)
            if t == 'void':
                self.error(n, "Cannot print the result of a void function", 'Type Error')
            elif T.is_array(t):
                self.error(n, f"print() shows one whole number, but this is {T.describe(t)}" +
                           (": use printf(\"%s\\n\", ...) for text" if T.is_string(t) else ": print the elements in a loop"), 'Type Error')
            elif t in ('long', 'float', 'double'):
                spec = '%ld' if t == 'long' else '%f'
                self.error(n, f"print() shows int values only, but this is a {t}: use printf(\"{spec}\\n\", ...)", 'Type Error')
        elif isinstance(n, ScanfNode):
            self._scanf(n)
        elif isinstance(n, PrintfNode):
            self._printf(n)
        elif isinstance(n, ExprStmtNode):
            self.visit(n.expr)
        return 'void'

    def _loop_body(self, body):
        self.flow.append("loop")
        try:
            self.visit(body)
        finally:
            self.flow.pop()

    def _switch(self, n):
        t = self.visit(n.expr)
        if t not in T.INTEGER and t != 'unknown':
            self.error(n, f"switch needs a whole number or a char, but this is {_a(T.describe(t)) if T.is_array(t) else _a(t)}"
                       + (": compare text with strcmp in if / else if instead" if T.is_string(t) else ": use if / else if for decimals" if t in T.FLOATING else ""), 'Type Error')
        seen, default = {}, None
        self.flow.append("switch")
        self.scopes.append({})                         # the switch body is one block, as in C
        try:
            for c in n.cases:
                if c.value is None:
                    if default is not None:
                        self.error(c, f"A switch can have only one 'default' (the first is on line {default})")
                    default = default or c.line
                else:
                    self.visit(c.value)
                    v = const_value(c.value)
                    if v is None:
                        self.error(c, "A case label must be a constant whole number or a character, e.g. case 3: or case 'a':")
                    elif v in seen:
                        self.error(c, f"Duplicate case value {v} (it is already used on line {seen[v]})")
                    else:
                        seen[v] = c.line
                for st in c.statements:
                    self.visit(st)
            self._unread(self.scopes.pop())
        finally:
            self.flow.pop()

    # ------------------------------------------------------------ expressions
    def _binop(self, n):
        a = self.visit(n.left); b = self.visit(n.right)
        if 'void' in (a, b):
            self.error(n, f"The result of a void function cannot be used with '{n.op}'", 'Type Error')
            return 'unknown'
        if 'unknown' in (a, b):
            return 'int' if n.op in ('<', '>', '<=', '>=', '==', '!=', '&&', '||') else (a if a != 'unknown' else b)
        if T.is_array(a) or T.is_array(b):
            if n.op in ('==', '!=') and T.is_array(a) and T.is_array(b):
                self.error(n, f"'{n.op}' compares the addresses of two arrays, not their contents, so it is almost always "
                              f"{'false' if n.op == '==' else 'true'}. To compare strings use strcmp(a, b) == 0", 'Warning')
                return 'int'
            if n.op in ('==', '!=') and (isinstance(n.left, StringNode) or isinstance(n.right, StringNode)):
                self.error(n, "This compares one character with a string: use single quotes for one character, e.g. s[i] == 'a'", 'Type Error')
                return 'int'
            arr = a if T.is_array(a) else b
            self.error(n, f"Cannot use '{n.op}' with {T.describe(arr)}: work with one element at a time, e.g. a[i]", 'Type Error')
            return 'unknown'
        if n.op in ('%',) and (T.is_floating(a) or T.is_floating(b)):
            self.error(n, f"% only works with whole numbers, but this uses a {a if T.is_floating(a) else b}", 'Type Error')
        if n.op in ('/', '%') and ((isinstance(n.right, NumNode) and n.right.value == 0) or
                                    (isinstance(n.right, FloatNode) and n.right.value == 0)):
            self.error(n, 'Division by constant zero', 'Warning')
        if n.op in ('<', '>', '<=', '>=', '==', '!=', '&&', '||'):
            return 'int'
        return T.common(a, b)

    def _condition(self, expr):
        if expr is None:
            return
        t = self.visit(expr)
        if t == 'void':
            self.error(expr, "A void function result cannot be used as a condition", 'Type Error')
        elif T.is_array(t):
            self.error(expr, f"{T.describe(t).capitalize()} cannot be used as a condition: test one element, or the length (strlen(s) > 0)", 'Type Error')

    def _call(self, n):
        types = []
        for a in n.args:
            t = self.visit(a)
            types.append(t)
            if t == 'void':
                self.error(a, "A void value cannot be passed as an argument", 'Type Error')
        sig = self.functions.get(n.name)
        if sig is None and n.name in ('printf', 'scanf'):
            self.error(n, f"{n.name}(...) must be used as a statement on its own line, not inside an expression")
            return 'unknown'
        builtin = sig is None and n.name in T.BUILTINS
        if builtin:
            sig = T.BUILTINS[n.name]
        if sig is None:
            self.error(n, f"Undeclared function '{n.name}'")
            return 'unknown'
        ret, params = sig
        if len(params) != len(n.args):
            self.error(n, f"Function '{n.name}' expects {len(params)} argument{'s' if len(params) != 1 else ''}, got {len(n.args)}")
        for k, (p, a, t) in enumerate(zip(params, n.args, types), 1):
            if t in ('unknown', 'void'):
                continue
            if builtin and p == 'char[]':
                if not T.is_string(t):
                    self.error(a, f"{n.name}() needs a string (a char array or \"text\") as argument {k}, but got {T.describe(t)}", 'Type Error')
                elif k == 1 and n.name in T.WRITES_FIRST_ARG and isinstance(a, StringNode):
                    self.error(a, f"{n.name}() writes into its first argument, so it must be a char array, not the text \"{a.value}\"", 'Type Error')
                continue
            if T.is_array(p):
                pb, pd = T.parse(p)
                ab, ad = T.parse(t)
                if not T.is_array(t):
                    self.error(a, f"Argument {k} of {n.name}() must be {T.describe(p)}, but got one {t} value", 'Type Error')
                elif pb != ab or len(pd) != len(ad) or any(x is not None and x != y for x, y in zip(pd[1:], ad[1:])):
                    self.error(a, f"Argument {k} of {n.name}() must be {T.describe(p)}, but got {T.describe(t)}", 'Type Error')
            elif T.is_array(t):
                self.error(a, f"Argument {k} of {n.name}() is {T.describe(t)}, but {n.name}() expects one {p} value", 'Type Error')
        return ret

    # ------------------------------------------------------------ input / output
    def _scanf(self, n):
        specs, errs = parse_scanf_format(n.format)
        for e in errs:
            self.error(n, f"scanf format: {e}", 'Type Error')
        if not errs and len(specs) != len(n.names):
            what = "%d" if specs and all(s in ("%d", "%i") for s in specs) else "conversions"
            self.error(n, f"scanf format has {len(specs)} {what} but {len(n.names)} variable{'s' if len(n.names) != 1 else ''} {'was' if len(n.names) == 1 else 'were'} given")
        for k, target in enumerate(n.names):
            name, line, col = target[:3]
            indices, amp = (target[3], target[4]) if len(target) > 3 else ([], True)
            at = Node(line=line, column=col)
            meta = self.lookup(name)
            if not meta:
                self.diagnostics.append({'category': 'Semantic Error', 'message': f"Undeclared variable '{name}'",
                                         'line': line, 'column': col,
                                         'suggestion': "Declare the variable before reading into it: int x; scanf(\"%d\", &x);"})
                continue
            if meta.get('const'):
                self.diagnostics.append({'category': 'Semantic Error', 'line': line, 'column': col,
                                         'message': f"scanf cannot store into '{name}': it is declared const"})
            t = self._index_type(at, name, indices, meta) if indices else meta['type']
            self.writes.add(name)
            if not indices:
                meta['initialized'] = True
            spec = specs[k] if k < len(specs) and not errs else None
            if spec is None or t == 'unknown':
                continue
            label = name + "".join("[...]" for _ in indices)
            if spec == "%s":
                if not T.is_string(t):
                    self.error(at, f"scanf %s reads a word into a char array, but '{label}' is {T.describe(t)}", 'Type Error')
                elif amp:
                    self.error(at, f"scanf(\"%s\", {label}) needs no &: an array name already gives scanf the place to store the text", 'Warning')
                continue
            want = T.SCANF_SPECS[spec]
            if T.is_array(t):
                self.error(at, f"scanf {spec} reads one value, but '{label}' is {T.describe(t)}: read into one element, e.g. &{name}[i]", 'Type Error')
            elif not amp:
                self.error(at, f"scanf needs the address of '{label}': write &{label}", 'Type Error')
            elif t != want:
                right = {"int": "%d", "long": "%ld", "short": "%hd", "float": "%f", "double": "%lf", "char": "%c"}.get(t, "?")
                self.error(at, f"scanf {spec} reads {_a(want)}, but '{label}' is {_a(t)}: use {right}", 'Type Error')

    def _printf(self, n):
        parts, errs = parse_format(n.format)
        for e in errs:
            self.error(n, f"printf format: {e}", 'Type Error')
        convs = [p for p in parts if p[0] in ('int', 'spec')]
        if not errs and len(convs) != len(n.args):
            if all(p[0] == 'int' for p in convs):
                self.error(n, f"printf format has {len(convs)} %d but {len(n.args)} value{'s' if len(n.args) != 1 else ''} {'was' if len(n.args) == 1 else 'were'} given")
            else:
                shown = " ".join("%d" if p[0] == 'int' else p[1] for p in convs)
                self.error(n, f"printf format has {len(convs)} placeholder{'s' if len(convs) != 1 else ''} ({shown}) but {len(n.args)} value{'s' if len(n.args) != 1 else ''} {'was' if len(n.args) == 1 else 'were'} given")
        for k, a in enumerate(n.args):
            t = self.visit(a)
            if t == 'void':
                self.error(a, "Cannot print the result of a void function", 'Type Error')
                continue
            if errs or k >= len(convs) or t == 'unknown':
                continue
            p = convs[k]
            spec, conv, length = ('%d', 'd', '') if p[0] == 'int' else p[1:]
            ok, advice = T.printf_arg_ok(conv, length, t)
            if not ok:
                self.error(a, f"printf {spec} does not match value {k + 1}, which is {T.describe(t)}: {advice}", 'Type Error')
