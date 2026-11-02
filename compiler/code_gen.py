"""Three-address-like intermediate code generator for supported AST nodes.

Instruction set:
  DECLARE int x          x is a variable of the current function (or a global at top level)
  DECLARE double t4      a temporary that holds a double (int temporaries are not declared)
  DECLARE int a[5]       an array (DECLARE int m[3][4]; parameter arrays: DECLARE int a[])
  CONST t, 5   STORE x, t   t3 = a + b   PRINT x
  t5 = (double) t3       convert a value to another type (C's casts and automatic conversions)
  t6 = a[t1]   a[t1] = t6     read / write one array element (m[t1][t2] for 2-D arrays)
  ARRAY_INIT a, [1, 2, 3]   initial values of an array, in memory order (the rest become 0)
  t7 = ADDR_EQ s, t         compare the ADDRESSES of two arrays (what == does on arrays in C)
  LABEL L1:   GOTO L1   IF_FALSE t GOTO L1
  FUNC f:  RESULT_TYPE double  GET_PARAM a, 0  ...  RETURN t  END_FUNC f
                         RESULT_TYPE only appears when f returns something other than int / void
  PARAM t (push an argument; an array name, a row such as names[t1], or "text" passes an array)
  t5 = CALL f, 2   CALL f, 0 (result unused)   library functions (strlen, strcpy, sqrt ...) use CALL too
  WRITE_STR "text"   WRITE_INT t     printf pieces: text as a JSON string, an int without newline
  WRITE_FMT "%.2f", t                any other printf conversion (%f %c %s %ld %5d ...)
  READ_INT x                         scanf("%d", &x): read the next integer from the input into x
  FALLTHROUGH a b                    no-op marking that the switch case on line a runs on into the case on line b
                                     (only reached when it really falls through; the VM records it)
  READ "%lf", x   READ "%s", name    other scanf conversions; the target may be an element: a[t1]

`generate()` returns the instruction list (unchanged API). After it runs, `line_map[i]` holds
the source line that produced instruction i, which the IDE debugger uses to highlight code.
"""
import json
from compiler.parser import *
from compiler import typesys as T
from compiler.semantic import const_value


class CodeGenerator:
    def __init__(self):
        self.instructions = []
        self.line_map = []
        self.temp_count = 0
        self.label_count = 0
        self.cur_line = 1
        self.scopes = [{}]
        self.funcs = {}
        self.ret_type = 'int'
        self.flow = []               # (break label, continue label or None) for the enclosing loops / switches

    def new_temp(self, typ='int'):
        self.temp_count += 1
        t = f"t{self.temp_count}"
        if typ in ('long', 'float', 'double', 'char', 'short'):
            self.emit(f"DECLARE {typ} {t}")
        return t

    def new_label(self):
        self.label_count += 1
        return f"L{self.label_count}"

    def emit(self, s):
        self.instructions.append(s)
        self.line_map.append(self.cur_line)

    def generate(self, node):
        self.instructions, self.line_map = [], []
        if isinstance(node, ProgramNode):
            for s in node.statements:
                if isinstance(s, FunctionNode):
                    self.funcs[s.name] = (s.return_type, [self._param_type(p) for p in s.params if p[0] != 'void' or len(p) > 4])
        self._gen(node)
        return self.instructions

    # ------------------------------------------------------------ symbols
    def _param_type(self, p):
        if len(p) > 4 and p[4]:
            return T.make(p[0], (None,) + tuple(const_value(d) or 1 for d in p[4][1:]))
        return p[0]

    def lookup(self, name):
        for s in reversed(self.scopes):
            if name in s:
                return s[name]
        return ('int', False)

    def type_of(self, name):
        return self.lookup(name)[0]

    def _decl_type(self, n):
        """Full type of a declaration, with sizes filled in from the initialiser when left empty."""
        if not n.dims:
            return n.var_type
        dims = [None if d is None else (const_value(d) or 1) for d in n.dims]
        if dims[0] is None:
            if isinstance(n.expr, StringNode):
                dims[0] = len(T.decode(n.expr.value)[0]) + 1
            elif isinstance(n.expr, InitListNode):
                if len(dims) > 1 and all(not isinstance(x, (InitListNode, StringNode)) for x in n.expr.items):
                    inner = T.count(dims[1:]) or 1
                    dims[0] = -(-len(n.expr.items) // inner)
                else:
                    dims[0] = len(n.expr.items)
            dims[0] = dims[0] or 1
        return T.make(n.var_type, dims)

    def _type_of(self, n):
        """Type of an expression without generating code (used by sizeof)."""
        if isinstance(n, NumNode): return T.int_type_of(n.value)
        if isinstance(n, FloatNode): return 'float' if n.text[-1:] in 'fF' else 'double'
        if isinstance(n, CharNode): return 'char'
        if isinstance(n, StringNode): return T.make('char', (len(T.decode(n.value)[0]) + 1,))
        if isinstance(n, VarNode): return self.type_of(n.name)
        if isinstance(n, IndexNode):
            t = self.type_of(n.name)
            for _ in n.indices:
                t = T.element(t) if T.is_array(t) else t
            return t
        if isinstance(n, CastNode): return n.to_type
        if isinstance(n, SizeofNode): return 'int'
        if isinstance(n, CallNode):
            sig = self.funcs.get(n.name) or T.BUILTINS.get(n.name)
            return sig[0] if sig else 'int'
        if isinstance(n, BinOpNode):
            if n.op in ('<', '>', '<=', '>=', '==', '!=', '&&', '||'):
                return 'int'
            a, b = self._type_of(n.left), self._type_of(n.right)
            return T.common(a, b) if T.is_scalar(a) and T.is_scalar(b) else 'int'
        return 'int'

    # ------------------------------------------------------------ statements
    def _at(self, n):
        if n is not None and getattr(n, "line", None):
            self.cur_line = n.line

    def _gen(self, n):
        if n is None:
            return None
        if isinstance(n, ProgramNode):
            for s in n.statements:
                self._gen(s)
            return
        if isinstance(n, BlockNode):
            self.scopes.append({})
            for s in n.statements:
                self._gen(s)
            self.scopes.pop()
            return
        self._at(n)
        if isinstance(n, FunctionNode):
            self.emit(f"FUNC {n.name}:")
            if n.return_type not in ('int', 'void'):
                self.emit(f"RESULT_TYPE {n.return_type}")
            self.ret_type = n.return_type
            self.scopes.append({})
            for i, p in enumerate(p for p in n.params if p[0] != 'void' or len(p) > 4):
                ptype, name = self._param_type(p), p[1]
                self.scopes[-1][name] = (ptype, True)
                if ptype != 'int':
                    self.emit(f"DECLARE {T.parse(ptype)[0]} {name}{T.make('', T.parse(ptype)[1])}")
                self.emit(f"GET_PARAM {name}, {i}")
            self._gen(n.body)
            self.scopes.pop()
            self._at(n)
            self.emit(f"END_FUNC {n.name}")
            self.ret_type = 'int'
        elif isinstance(n, VarDeclNode):
            typ = self._decl_type(n)
            base, dims = T.parse(typ)
            self.scopes[-1][n.name] = (typ, False)
            self.emit(f"DECLARE {base} {n.name}{T.make('', dims)}")
            if dims and n.expr is not None:
                self._array_init(n.name, base, dims, n.expr)
            elif n.expr is not None:
                self.emit(f"STORE {n.name}, {self._value(n.expr, base)}")
            elif not dims and len(self.scopes) == 1:
                # C sets global variables to 0 before main starts (locals start with garbage)
                self.emit(f"STORE {n.name}, {self._value(FloatNode(value=0.0, text='0.0') if base in T.FLOATING else NumNode(value=0), base)}")
        elif isinstance(n, AssignNode):
            if n.indices:
                target = self._place(n.name, n.indices)
                elem = self._type_of(IndexNode(name=n.name, indices=n.indices))
                self.emit(f"{target} = {self._value(n.expr, elem)}")
            else:
                self.emit(f"STORE {n.name}, {self._value(n.expr, self.type_of(n.name))}")
        elif isinstance(n, PrintNode):
            self.emit(f"PRINT {self._expr(n.expr)}")
        elif isinstance(n, ScanfNode):
            specs, _ = parse_scanf_format(n.format)
            spaces = T.scanf_space_before(n.format)
            for k, target in enumerate(n.names):
                name = target[0]
                indices = target[3] if len(target) > 3 else []
                spec = specs[k] if k < len(specs) else "%d"
                if spec == "%c" and k < len(spaces) and spaces[k]:
                    spec = " %c"                  # a space before %c skips spaces and new lines
                place = self._place(name, indices) if indices else name
                if spec in ("%d", "%i") and not indices and self.type_of(name) == 'int':
                    self.emit(f"READ_INT {name}")
                else:
                    self.emit(f"READ {json.dumps(spec)}, {place}")
        elif isinstance(n, PrintfNode):
            values = [self._expr(a) for a in n.args]          # evaluate every value first, like C
            parts, _ = parse_format(n.format)
            k = 0
            for part in parts:
                if part[0] == 'text':
                    self.emit(f"WRITE_STR {json.dumps(part[1])}")
                elif k < len(values):
                    if part[0] == 'int':
                        self.emit(f"WRITE_INT {values[k]}")
                    else:
                        self.emit(f"WRITE_FMT {json.dumps(part[1])}, {values[k]}")
                    k += 1
        elif isinstance(n, IfNode):
            otherwise = self.new_label(); end = self.new_label()
            self.emit(f"IF_FALSE {self._expr(n.condition)} GOTO {otherwise}")
            self._gen(n.then_branch)
            if n.else_branch is not None:
                self._at(n); self.emit(f"GOTO {end}")
                self.emit(f"LABEL {otherwise}:")
                self._gen(n.else_branch)
                self._at(n); self.emit(f"LABEL {end}:")
            else:
                self._at(n); self.emit(f"LABEL {otherwise}:")
        elif isinstance(n, WhileNode):
            start = self.new_label(); end = self.new_label()
            self.emit(f"LABEL {start}:")
            self.emit(f"IF_FALSE {self._expr(n.condition)} GOTO {end}")
            self._body(n.body, end, start)
            self._at(n); self.emit(f"GOTO {start}")
            self.emit(f"LABEL {end}:")
        elif isinstance(n, ForNode):
            self.scopes.append({})                    # for (int i = ...) : i belongs to the loop
            self._gen(n.init)
            start = self.new_label(); end = self.new_label(); step = self.new_label()
            self._at(n); self.emit(f"LABEL {start}:")
            if n.condition:
                self.emit(f"IF_FALSE {self._expr(n.condition)} GOTO {end}")
            self._body(n.body, end, step)
            self._at(n); self.emit(f"LABEL {step}:")
            self._gen(n.update)
            self._at(n); self.emit(f"GOTO {start}")
            self.emit(f"LABEL {end}:")
            self.scopes.pop()
        elif isinstance(n, DoWhileNode):
            start = self.new_label(); test = self.new_label(); end = self.new_label()
            self.emit(f"LABEL {start}:")
            self._body(n.body, end, test)
            self._at(n); self.emit(f"LABEL {test}:")
            self.emit(f"IF_FALSE {self._expr(n.condition)} GOTO {end}")
            self.emit(f"GOTO {start}")
            self.emit(f"LABEL {end}:")
        elif isinstance(n, BreakNode):
            if self.flow:
                self.emit(f"GOTO {self.flow[-1][0]}")
        elif isinstance(n, ContinueNode):
            target = next((c for _, c in reversed(self.flow) if c), None)
            if target:
                self.emit(f"GOTO {target}")
        elif isinstance(n, SwitchNode):
            self._switch(n)
        elif isinstance(n, ReturnNode):
            if n.expr is None:
                self.emit("RETURN")
            elif self.ret_type in T.SCALARS:
                self.emit(f"RETURN {self._value(n.expr, self.ret_type)}")
            else:
                self.emit(f"RETURN {self._expr(n.expr)}")
        elif isinstance(n, ExprStmtNode):
            if isinstance(n.expr, CallNode):
                self._call(n.expr, discard=True)
            else:
                self._expr(n.expr)

    def _body(self, body, brk, cont):
        self.flow.append((brk, cont))
        try:
            self._gen(body)
        finally:
            self.flow.pop()

    def _switch(self, n):
        """switch (e) { case 1: ... }  ->  compare e with each case value in turn, jump to the first match
        (or to default, or past the end); the case bodies follow in order, so a missing break falls through."""
        from compiler.semantic import const_value
        value, typ = self._typed(n.expr)
        labels = [self.new_label() for _ in n.cases]
        end = self.new_label()
        default = None
        for c, lab in zip(n.cases, labels):
            if c.value is None:
                default = lab
                continue
            self._at(c)
            k = self.new_temp(); self.emit(f"CONST {k}, {const_value(c.value) or 0}")
            hit = self.new_temp(); self.emit(f"{hit} = {value} == {k}")
            skip = self.new_label()
            self.emit(f"IF_FALSE {hit} GOTO {skip}")
            self.emit(f"GOTO {lab}")
            self.emit(f"LABEL {skip}:")
        self._at(n); self.emit(f"GOTO {default or end}")
        self.scopes.append({})
        self.flow.append((end, None))
        try:
            prev = None
            for c, lab in zip(n.cases, labels):
                if prev is not None and prev.statements:
                    # only reached by falling out of the previous case (jumps go to the label below)
                    self._at(prev); self.emit(f"FALLTHROUGH {prev.line} {c.line}")
                self._at(c); self.emit(f"LABEL {lab}:")
                prev = c
                for st in c.statements:
                    self._gen(st)
        finally:
            self.flow.pop()
            self.scopes.pop()
        self._at(n); self.emit(f"LABEL {end}:")

    def _array_init(self, name, base, dims, init):
        """ARRAY_INIT with every constant value; non-constant values are stored one by one after it."""
        total = T.count(dims)
        flat = [None] * total                     # node for each element, in memory order

        def fill(node, start, dims_left):
            size = T.count(dims_left)
            if isinstance(node, StringNode):
                text = T.decode(node.value)[0]
                for k, ch in enumerate((text + "\0")[:size]):
                    flat[start + k] = CharNode(value=T.wrap(ord(ch), 'char'))
                return
            items = node.items
            if len(dims_left) > 1 and all(not isinstance(x, (InitListNode, StringNode)) for x in items):
                for k, x in enumerate(items[:size]):
                    flat[start + k] = x
                return
            inner = T.count(dims_left[1:]) if len(dims_left) > 1 else 1
            for k, x in enumerate(items[:dims_left[0]]):
                if len(dims_left) > 1:
                    fill(x, start + k * inner, dims_left[1:])
                else:
                    flat[start + k] = x

        fill(init, 0, dims)
        values, later = [], []
        for k, node in enumerate(flat):
            v = None if node is None else const_value(node)
            if node is not None and v is None and isinstance(node, FloatNode):
                v = node.value
            if node is not None and v is None and isinstance(node, BinOpNode) and is_unary_minus(node) and isinstance(node.right, FloatNode):
                v = -node.right.value
            if node is not None and v is None:
                later.append((k, node)); v = 0
            if v is None:
                v = 0
            if base in T.FLOATING:
                v = float(v)
            else:
                v = T.wrap(int(v), base)
            values.append(v)
        while values and values[-1] in (0, 0.0) and not later:
            values.pop()                          # the rest is filled with zeros anyway
        self.emit(f"ARRAY_INIT {name}, {json.dumps(values)}")
        for k, node in later:
            idx = []
            rem = k
            for d in reversed(dims):
                idx.append(rem % d); rem //= d
            ops = []
            for i in reversed(idx):
                t = self.new_temp(); self.emit(f"CONST {t}, {i}"); ops.append(t)
            self.emit(f"{name}{''.join(f'[{o}]' for o in ops)} = {self._value(node, base)}")

    # ------------------------------------------------------------ expressions
    def _place(self, name, indices):
        """name[i][j] with each index evaluated into an operand."""
        ops = [self._value(i, 'int') if self._type_of(i) == 'long' else self._expr(i) for i in indices]
        return f"{name}{''.join(f'[{o}]' for o in ops)}"

    def _expr(self, n):
        return self._typed(n)[0]

    def _value(self, n, to_type):
        """Evaluate n and convert it to to_type (an implicit C conversion)."""
        op, t = self._typed(n)
        return self._convert(op, t, to_type)

    def _convert(self, op, from_t, to_t):
        if not T.is_scalar(to_t) or not T.is_scalar(from_t) or from_t == to_t:
            return op
        if to_t == 'int' and from_t in ('char', 'short'):
            return op
        last = self.instructions[-1] if self.instructions else ""
        if last.startswith(f"CONST {op}, ") and to_t in T.INTEGER and from_t in T.INTEGER:
            raw = last.split(", ", 1)[1]
            if T.wrap(int(raw), to_t) != int(raw):
                last = ""                         # 300 -> char changes the value: keep the conversion visible
        if last.startswith(f"CONST {op}, ") and op.startswith("t"):
            # a literal: write the converted constant directly instead of a conversion instruction
            raw = last.split(", ", 1)[1]
            v = float(raw) if any(c in raw for c in ".eEn") else int(raw)
            self.instructions.pop(); self.line_map.pop()
            if self.instructions and self.instructions[-1] == f"DECLARE {from_t} {op}":
                self.instructions.pop(); self.line_map.pop()
            v = float(v) if to_t in T.FLOATING else T.wrap(int(v), to_t)
            t = self.new_temp(to_t)
            self.emit(f"CONST {t}, {v!r}")
            return t
        t = self.new_temp(to_t)
        self.emit(f"{t} = ({to_t}) {op}")
        return t

    def _typed(self, n):
        """Generate code for an expression. Returns (operand, type)."""
        if isinstance(n, NumNode):
            typ = T.int_type_of(n.value)
            t = self.new_temp(typ); self.emit(f"CONST {t}, {n.value}"); return t, typ
        if isinstance(n, FloatNode):
            typ = 'float' if n.text[-1:] in 'fF' else 'double'
            t = self.new_temp(typ); self.emit(f"CONST {t}, {float(n.value)!r}"); return t, typ
        if isinstance(n, CharNode):
            t = self.new_temp(); self.emit(f"CONST {t}, {n.value}"); return t, 'char'
        if isinstance(n, StringNode):
            text = T.decode(n.value)[0]
            return json.dumps(text), T.make('char', (len(text) + 1,))
        if isinstance(n, VarNode):
            return n.name, self.type_of(n.name)
        if isinstance(n, IndexNode):
            arr = self.type_of(n.name)
            rank = len(T.parse(arr)[1])
            place = self._place(n.name, n.indices)
            elem = self._type_of(n)
            if len(n.indices) < rank:
                return place, elem                     # a whole row, e.g. names[i]: passed as an array
            t = self.new_temp(elem if elem in ('long', 'float', 'double') else 'int')
            self.emit(f"{t} = {place}")
            return t, elem
        if isinstance(n, CastNode):
            op, t = self._typed(n.expr)
            if n.to_type == 'void':
                return op, 'void'
            return self._convert(op, t, n.to_type), n.to_type
        if isinstance(n, SizeofNode):
            if n.of_type:
                size = T.sizeof(n.of_type)
            elif isinstance(n.expr, (VarNode, IndexNode)):
                typ, is_param = self.lookup(n.expr.name)
                size = T.sizeof(self._type_of(n.expr), is_param=is_param and isinstance(n.expr, VarNode))
            else:
                size = T.sizeof(self._type_of(n.expr))
            t = self.new_temp(); self.emit(f"CONST {t}, {size}"); return t, 'int'
        if isinstance(n, CallNode):
            return self._call(n)
        if isinstance(n, BinOpNode) and n.op in ("&&", "||"):
            return self._short_circuit(n), 'int'
        if isinstance(n, BinOpNode) and is_unary_minus(n):
            op, typ = self._typed(n.right)
            typ = T.promote(typ) if T.is_scalar(typ) else 'int'
            zero = self.new_temp(typ); self.emit(f"CONST {zero}, {0.0 if typ in T.FLOATING else 0}")
            t = self.new_temp(typ)
            self.emit(f"{t} = {zero} - {op}")
            return t, typ
        if isinstance(n, BinOpNode):
            a, ta = self._typed(n.left)
            b, tb = self._typed(n.right)
            if T.is_array(ta) or T.is_array(tb):
                t = self.new_temp()
                self.emit(f"{t} = ADDR_{'EQ' if n.op == '==' else 'NE'} {a}, {b}")
                return t, 'int'
            ct = T.common(ta, tb) if T.is_scalar(ta) and T.is_scalar(tb) else 'int'
            a = self._convert(a, ta, ct); b = self._convert(b, tb, ct)
            comparison = n.op in ('<', '>', '<=', '>=', '==', '!=')
            t = self.new_temp('int' if comparison else ct)
            self.emit(f"{t} = {a} {n.op} {b}")
            return t, 'int' if comparison else ct
        return '<?> ', 'int'

    def _call(self, n, discard=False):
        """Evaluate all arguments first, then push them, then call (keeps nested calls simple)."""
        sig = self.funcs.get(n.name) or T.BUILTINS.get(n.name) or ('int', [])
        ret, params = sig
        args = []
        for k, a in enumerate(n.args):
            p = params[k] if k < len(params) else None
            if p is None or T.is_array(p) or p == 'char[]':
                args.append(self._expr(a))
            else:
                args.append(self._value(a, p))
        for a in args:
            self.emit(f"PARAM {a}")
        if discard:
            self.emit(f"CALL {n.name}, {len(args)}")
            return None
        t = self.new_temp(ret if ret in ('long', 'float', 'double') else 'int')
        self.emit(f"{t} = CALL {n.name}, {len(args)}")
        return t, ret if ret != 'void' else 'int'

    def _short_circuit(self, n):
        """a && b / a || b like C: the right side only runs when it can change the answer.
        The result is always 0 or 1."""
        t = self.new_temp()
        other, end = self.new_label(), self.new_label()
        a = self._expr(n.left)
        if n.op == "&&":
            self.emit(f"IF_FALSE {a} GOTO {other}")          # left false -> whole thing false
            b = self._expr(n.right)
            zero = self.new_temp(); self.emit(f"CONST {zero}, 0")
            self.emit(f"{t} = {b} != {zero}")
            self.emit(f"GOTO {end}")
            self.emit(f"LABEL {other}:")
            self.emit(f"CONST {t}, 0")
        else:
            self.emit(f"IF_FALSE {a} GOTO {other}")          # left false -> answer is the right side
            self.emit(f"CONST {t}, 1")                       # left true -> whole thing true
            self.emit(f"GOTO {end}")
            self.emit(f"LABEL {other}:")
            b = self._expr(n.right)
            zero = self.new_temp(); self.emit(f"CONST {zero}, 0")
            self.emit(f"{t} = {b} != {zero}")
        self.emit(f"LABEL {end}:")
        return t
