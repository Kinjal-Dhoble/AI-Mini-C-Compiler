"""Quiet compilation + the compiler "facts" that ground every LLM prompt.

The LLM is only as good as what we show it. Instead of sending raw source alone, we
send line-numbered source, every diagnostic the compiler produced, the IR, and a few
facts the compiler can prove (constant expressions, identity operations, unreachable
statements). The model is told to prefer these facts over its own guesses.

`check_source` is also the verifier: any code the LLM proposes is run through it.
"""
from compiler.parser import (BinOpNode, NumNode, VarNode, BlockNode, FunctionNode, CallNode,
                             IfNode, WhileNode, ForNode, ReturnNode, VarDeclNode, AssignNode,
                             ExprStmtNode, ProgramNode, PrintNode, PrintfNode, DoWhileNode, SwitchNode)
from compiler.pipeline import analyze, ERROR_CATEGORIES


def check_source(source):
    """Compile without printing (wraps compiler.pipeline.analyze). `ok` means it compiled."""
    r = analyze(source)
    r["ok"] = r["success"]
    return r


def token_signature(tokens):
    """Code tokens only (comments/whitespace are already dropped by the lexer)."""
    return [(t.kind, t.value) for t in tokens if t.kind != "EOF"]


def numbered(source):
    return "\n".join(f"{i:>3} | {line}" for i, line in enumerate(source.splitlines(), 1))


def _fmt(d):
    loc = f"line {d['line']}, column {d.get('column', '?')}: " if "line" in d else ""
    extra = f"  (rule-based hint: {d['suggestion']})" if d.get("suggestion") else ""
    return f"- [{d.get('category', 'Error')}] {loc}{d.get('message', '')}{extra}"


def _expr_text(n):
    from compiler.printer import expr as pexpr
    from compiler.parser import IndexNode, CastNode, SizeofNode, FloatNode, CharNode, StringNode
    if isinstance(n, (IndexNode, CastNode, SizeofNode, FloatNode, CharNode, StringNode)):
        return pexpr(n)
    if isinstance(n, NumNode):
        return str(n.value)
    if isinstance(n, VarNode):
        return n.name
    if isinstance(n, BinOpNode):
        return f"({_expr_text(n.left)} {n.op} {_expr_text(n.right)})"
    if isinstance(n, CallNode):
        return f"{n.name}({', '.join(_expr_text(a) for a in n.args)})"
    return "?"


def compiler_facts(ast):
    """Optimisation facts the compiler can prove from the AST."""
    facts = []

    def expr(n):
        if not isinstance(n, BinOpNode):
            return
        if isinstance(n.left, NumNode) and isinstance(n.right, NumNode):
            facts.append(f"line {n.line}: constant expression {_expr_text(n)} can be folded at compile time")
        else:
            l, r = n.left, n.right
            if n.op in "+-" and isinstance(r, NumNode) and r.value == 0:
                facts.append(f"line {n.line}: {_expr_text(n)} adds/subtracts 0 (identity operation)")
            if n.op in "*/" and isinstance(r, NumNode) and r.value == 1:
                facts.append(f"line {n.line}: {_expr_text(n)} multiplies/divides by 1 (identity operation)")
            if n.op == "*" and (isinstance(l, NumNode) and l.value == 0 or isinstance(r, NumNode) and r.value == 0):
                facts.append(f"line {n.line}: {_expr_text(n)} multiplies by 0, result is always 0")
            if n.op == "*" and isinstance(r, NumNode) and r.value == 2:
                facts.append(f"line {n.line}: {_expr_text(n)} could be written as an addition (x + x)")
            expr(l); expr(r)

    def stmts(nodes):
        seen_return = False
        for s in nodes:
            if seen_return:
                facts.append(f"line {s.line}: statement after `return` in the same block is unreachable")
                break
            stmt(s)
            seen_return = isinstance(s, ReturnNode)

    def stmt(n):
        if n is None:
            return
        if isinstance(n, (ProgramNode, BlockNode)):
            stmts(n.statements)
        elif isinstance(n, FunctionNode):
            stmt(n.body)
        elif isinstance(n, (VarDeclNode, AssignNode, ReturnNode, ExprStmtNode, PrintNode)):
            expr(n.expr)
        elif isinstance(n, PrintfNode):
            for a in n.args:
                expr(a)
        elif isinstance(n, IfNode):
            if isinstance(n.condition, NumNode):
                facts.append(f"line {n.line}: `if` condition is the constant {n.condition.value}; one branch is dead code")
            expr(n.condition); stmt(n.then_branch); stmt(n.else_branch)
        elif isinstance(n, WhileNode):
            if isinstance(n.condition, NumNode) and n.condition.value == 0:
                facts.append(f"line {n.line}: `while (0)` body never runs")
            expr(n.condition); stmt(n.body)
        elif isinstance(n, ForNode):
            stmt(n.init); expr(n.condition); stmt(n.body); stmt(n.update)
        elif isinstance(n, DoWhileNode):
            if isinstance(n.condition, NumNode) and n.condition.value == 0:
                facts.append(f"line {n.line}: `do ... while (0)` body runs exactly once")
            stmt(n.body); expr(n.condition)
        elif isinstance(n, SwitchNode):
            expr(n.expr)
            for c in n.cases:
                stmts(c.statements)

    stmt(ast)
    return facts


def structure_facts(check):
    """Functions, calls, loops and complexity, as the compiler computed them."""
    out = []
    cg = check.get("call_graph") or {}
    for f in cg.get("functions", []):
        calls = cg.get("calls", {}).get(f, [])
        callers = sorted({a for a, b, _ in cg.get("edges", []) if b == f})
        out.append(f"- function {f}() at line {cg['line'][f]}: calls {', '.join(c + '()' for c in calls) or 'nothing'}; "
                   f"called by {', '.join(c + '()' for c in callers) or 'nobody'}"
                   + ("; RECURSIVE" if f in cg.get("recursive", []) else "")
                   + ("; NEVER CALLED from main" if f in cg.get("unreachable", []) else ""))
    by_line = {L["line"]: L for L in check.get("ast_loops", [])}
    for L in check.get("loops", []):
        a = by_line.get(L["header_line"], {})
        bound = {"constant": f"runs about {a.get('trips')} times" if a.get("trips") is not None else f"bounded by the constant {a.get('bound')}",
                 "variable": f"bounded by {a.get('bound')}" + (" (a parameter)" if a.get("bound_is_param") else ""),
                 "expression": f"bounded by an expression of {a.get('bound')}",
                 "infinite": "condition is always true", "never": "never runs"}.get(a.get("bound_kind"), "bound unknown")
        out.append(f"- loop at line {L['header_line']} in {L['function']}() (lines {L['lines'][0]}-{L['lines'][-1]}): "
                   f"nesting depth {L['depth']}, loop variable {a.get('var') or '?'}, {bound}, "
                   f"variable grows {'by a constant step (linear)' if a.get('growth') == 'linear' else 'by multiplying/dividing (logarithmic)' if a.get('growth') == 'log' else 'in an unknown way'}; "
                   f"CFG back edge {' , '.join('->'.join(e) for e in L['back_edges'])}")
    for f, c in (check.get("complexity") or {}).items():
        out.append(f"- estimated time complexity of {f}(): {c['big_o']}" + (" (plus recursion)" if c.get("recursive") else "")
                   + (f", from the loops at lines {', '.join(map(str, c['loop_lines']))}" if c.get("loop_lines") else ""))
    return out


def build_context(source, check=None, include_ir=True):
    """Render everything the compiler knows as one Markdown block for the user message."""
    check = check or check_source(source)
    out = ["## Source (line numbers are NOT part of the code)", "```c", numbered(source), "```",
           f"\n## Compilation result: {'SUCCESS' if check['ok'] else 'FAILED at ' + check['stage']}"]
    if check["errors"]:
        out += ["\n## Errors"] + [_fmt(d) for d in check["errors"]]
    if check["warnings"]:
        out += ["\n## Warnings"] + [_fmt(d) for d in check["warnings"]]
    if check["static"]:
        out += ["\n## Static-analysis findings"] + [_fmt(d) for d in check["static"]]
    if check["ast"] is not None and check["ok"]:
        facts = compiler_facts(check["ast"])
        out += ["\n## Compiler-proven facts"] + ([f"- {f}" for f in facts] or ["- (none)"])
        out += ["\n## Program structure (call graph, loops, complexity)"] + (structure_facts(check) or ["- (no functions or loops)"])
    if include_ir and check["ir"]:
        out += ["\n## Intermediate code (three-address)", "```", *check["ir"], "```"]
    return "\n".join(out)


def lenient_signature(source):
    """Code tokens of ANY source, even one the real lexer rejects.

    Uses the compiler's own token regex, but keeps unknown characters as tokens instead of
    raising. Comments and whitespace are dropped. This lets us check that documentation of
    broken code only added comments.
    """
    from compiler.lexer import TOKEN_RE, KEYWORDS
    sig = []
    for m in TOKEN_RE.finditer(source):
        kind, value = m.lastgroup, m.group()
        if kind in ("SPACE", "NEWLINE", "COMMENT"):
            continue
        if kind == "IDENTIFIER" and value in KEYWORDS:
            kind = "KEYWORD"
        sig.append((kind, value))
    return sig
