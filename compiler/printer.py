"""Pretty-printer: turns AST nodes back into Mini C source (used by the refactoring engine)."""
from compiler.parser import *

PREC = Parser.PRECEDENCE


def _dims(dims):
    return "".join("[]" if d is None else f"[{expr(d)}]" for d in dims)


def init(n):
    """An initialiser: an expression, "text" or { ... }."""
    if isinstance(n, InitListNode):
        return "{" + ", ".join(init(x) for x in n.items) + "}"
    return expr(n)


def expr(n, parent=0, right=False):
    if isinstance(n, NumNode):
        return str(n.value)
    if isinstance(n, FloatNode):
        return n.text
    if isinstance(n, CharNode):
        return n.text
    if isinstance(n, StringNode):
        return f'"{n.value}"'
    if isinstance(n, VarNode):
        return n.name
    if isinstance(n, IndexNode):
        return n.name + "".join(f"[{expr(i)}]" for i in n.indices)
    if isinstance(n, CastNode):
        s = f"({n.to_type}) {expr(n.expr, 7)}"
        return f"({s})" if parent > 7 else s
    if isinstance(n, SizeofNode):
        return f"sizeof({n.of_type or expr(n.expr)})"
    if isinstance(n, InitListNode):
        return init(n)
    if isinstance(n, CallNode):
        return f"{n.name}({', '.join(expr(a) for a in n.args)})"
    if isinstance(n, BinOpNode):
        if is_unary_minus(n):
            inner = expr(n.right, 7)
            return f"-{inner}"
        if is_logical_not(n):
            return f"!{expr(n.left, 7)}"
        p = PREC[n.op]
        left = expr(n.left, p)
        if isinstance(n.left, BinOpNode) and PREC.get(n.left.op) == p and p in (3, 4) and not is_unary_minus(n.left):
            left = f"({left})"                    # (a == b) == 1 reads better with brackets
        s = f"{left} {n.op} {expr(n.right, p, True)}"
        return f"({s})" if p < parent or (right and p == parent) else s
    return "?"


def negate(n):
    """Logical NOT of a condition, written without '!' where a simpler form exists (a < b -> a >= b)."""
    flip = {'<': '>=', '>=': '<', '>': '<=', '<=': '>', '==': '!=', '!=': '=='}
    if isinstance(n, BinOpNode) and n.op in flip and not is_unary_minus(n):
        return BinOpNode(line=n.line, column=n.column, left=n.left, op=flip[n.op], right=n.right)
    if isinstance(n, BinOpNode) and n.op in ('&&', '||'):
        return BinOpNode(line=n.line, column=n.column, left=negate(n.left), op='||' if n.op == '&&' else '&&', right=negate(n.right))
    if isinstance(n, NumNode):
        return NumNode(line=n.line, column=n.column, value=0 if n.value else 1)
    if is_logical_not(n):
        return n.left
    return BinOpNode(line=n.line, column=n.column, left=n, op='==', right=NumNode(value=0))


def is_boolean(n):
    """Does this expression always produce 0 or 1?"""
    return isinstance(n, BinOpNode) and n.op in ('<', '>', '<=', '>=', '==', '!=', '&&', '||') and not is_unary_minus(n)


def stmt(n, ind="    ", level=0):
    """Return a list of source lines for a statement."""
    pad = ind * level
    if isinstance(n, BlockNode):
        return [pad + "{"] + [l for s in n.statements for l in stmt(s, ind, level + 1)] + [pad + "}"]
    if isinstance(n, VarDeclNode):
        return [pad + ("const " if getattr(n, "const", False) else "") + f"{n.var_type} {n.name}{_dims(n.dims)}"
                + (f" = {init(n.expr)}" if n.expr is not None else "") + ";"]
    if isinstance(n, AssignNode):
        return [pad + assignment(n) + ";"]
    if isinstance(n, BreakNode):
        return [pad + "break;"]
    if isinstance(n, ContinueNode):
        return [pad + "continue;"]
    if isinstance(n, DoWhileNode):
        lines = [pad + "do {"] + _body(n.body, ind, level)
        lines[-1] += f" while ({expr(n.condition)});"
        return lines
    if isinstance(n, SwitchNode):
        lines = [pad + f"switch ({expr(n.expr)}) " + "{"]
        for c in n.cases:
            lines.append(ind * (level + 1) + ("default:" if c.value is None else f"case {expr(c.value)}:"))
            lines += [l for st in c.statements for l in stmt(st, ind, level + 2)]
        return lines + [pad + "}"]
    if isinstance(n, PrintNode):
        return [pad + f"print({expr(n.expr)});"]
    if isinstance(n, ScanfNode):
        return [pad + "scanf(" + ", ".join([f'"{n.format}"'] + [_scanf_target(x) for x in n.names]) + ");"]
    if isinstance(n, PrintfNode):
        return [pad + "printf(" + ", ".join([f'"{n.format}"'] + [expr(a) for a in n.args]) + ");"]
    if isinstance(n, ReturnNode):
        return [pad + ("return;" if n.expr is None else f"return {expr(n.expr)};")]
    if isinstance(n, ExprStmtNode):
        return [pad + f"{expr(n.expr)};"]
    if isinstance(n, IfNode):
        lines = [pad + f"if ({expr(n.condition)}) " + "{"] + _body(n.then_branch, ind, level)
        if n.else_branch is not None:
            if isinstance(n.else_branch, IfNode):
                rest = stmt(n.else_branch, ind, level)
                lines[-1] += " else " + rest[0].lstrip()
                lines += rest[1:]
            else:
                lines[-1] += " else {"
                lines += _body(n.else_branch, ind, level)
        return lines
    if isinstance(n, WhileNode):
        return [pad + f"while ({expr(n.condition)}) " + "{"] + _body(n.body, ind, level)
    if isinstance(n, ForNode):
        first = ""
        if isinstance(n.init, VarDeclNode):
            first = f"{n.init.var_type} {n.init.name}" + (f" = {expr(n.init.expr)}" if n.init.expr is not None else "")
        elif isinstance(n.init, AssignNode):
            first = assignment(n.init)
        cond = expr(n.condition) if n.condition is not None else ""
        upd = assignment(n.update) if n.update is not None else ""
        return [pad + f"for ({first}; {cond}; {upd}) " + "{"] + _body(n.body, ind, level)
    if isinstance(n, FunctionNode):
        params = ", ".join(f"{p[0]} {p[1]}" + (_dims(p[4]) if len(p) > 4 else "") for p in n.params)
        return [pad + f"{n.return_type} {n.name}({params}) " + "{"] + _body(n.body, ind, level)
    return [pad + "/* ? */"]


def assignment(n):
    """x = e, written back the way the programmer wrote it: x += e, i++, ++i ..."""
    op = getattr(n, "op", "=")
    if op in ("++", "--"):
        return f"{_target(n)}{op}"
    if op != "=" and isinstance(n.expr, BinOpNode):
        return f"{_target(n)} {op} {expr(n.expr.right)}"
    return f"{_target(n)} = {expr(n.expr)}"


def _target(n):
    return n.name + "".join(f"[{expr(i)}]" for i in getattr(n, "indices", []) or [])


def _scanf_target(x):
    if len(x) <= 3:
        return f"&{x[0]}"
    return ("&" if x[4] else "") + x[0] + "".join(f"[{expr(i)}]" for i in x[3])


def _body(n, ind, level):
    """Lines for a branch/loop body, always braced, ending with the closing brace."""
    inner = n.statements if isinstance(n, BlockNode) else [n]
    return [l for s in inner for l in stmt(s, ind, level + 1)] + [ind * level + "}"]
