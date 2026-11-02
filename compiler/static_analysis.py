"""Rule-based static analysis on the AST (E7): code smells and suspicious code.

  long function       more than 20 statements in one function
  deep nesting        if/while/for nested 3 or more levels deep
  magic number        an unexplained literal (not 0, 1, 2 or -1) inside an expression or condition
  unreachable code    statements after a return in the same block
  unused function     a function that main never (directly or indirectly) calls
  many parameters     more than 4 parameters
  constant condition  if/while whose condition is a plain number (always true or always false)

Logic errors (category 'Warning'): see logic_checks() below
  empty_body          if (x); / while (x); / for (...);  - the ';' is the whole body
  switch_fallthrough  a case runs into the next one because it has no break
  self_comparison     x == x, x < x ...: always the same answer
  impossible_condition x > 5 && x < 3 can never be true; x < 5 || x > 3 is always true
  counter_changed     the for-loop counter is also changed inside the loop body
  constant_overflow   a calculation of constants that does not fit in an int (50000 * 50000)
  endless_loop        while (1) / for (;;) with no break or return inside
  unreachable         statements after return, break or continue in the same block
Findings use category 'Code Smell' (style) or 'Warning' (likely mistake).
"""
from compiler.parser import *

ALLOWED_LITERALS = {0, 1, 2}


def _count(n):
    if n is None: return 0
    if isinstance(n, (BlockNode, ProgramNode)): return sum(_count(s) for s in n.statements)
    c = 1
    for k in ("then_branch", "else_branch", "body"):
        c += _count(getattr(n, k, None)) if k in getattr(n, "__dataclass_fields__", {}) else 0
    return c


class StaticAnalyzer:
    def analyze(self, program, call_graph=None):
        out = []
        seen_magic = set()

        def finding(cat, n, msg, **extra):
            d = {'category': cat, 'message': msg, 'line': n.line, 'column': n.column}
            d.update(extra)
            out.append(d)

        def magic(e, fn):
            if e is None: return
            if isinstance(e, NumNode):
                if e.value not in ALLOWED_LITERALS and (fn, e.value) not in seen_magic:
                    seen_magic.add((fn, e.value))
                    finding('Code Smell', e, f"Magic number {e.value}: give it a name, e.g. int limit = {e.value};",
                            rule="magic_number", value=e.value)
            elif isinstance(e, BinOpNode):
                if is_unary_minus(e) and isinstance(e.right, NumNode) and e.right.value == 1:
                    return
                magic(e.left, fn); magic(e.right, fn)
            elif isinstance(e, CallNode):
                for a in e.args: magic(a, fn)
            elif isinstance(e, CastNode):
                magic(e.expr, fn)

        def stmts(lst, fn, depth):
            dead = False
            for s in lst:
                if dead:
                    finding('Warning', s, f"Unreachable code: this statement comes after a {dead}, so it never runs",
                            rule="unreachable")
                    break
                walk(s, fn, depth)
                dead = {ReturnNode: "return", BreakNode: "break", ContinueNode: "continue"}.get(type(s), False)

        def walk(n, fn, depth):
            if n is None: return
            if isinstance(n, FunctionNode):
                if _count(n.body) > 20:
                    finding('Code Smell', n, f"Function '{n.name}' has more than 20 statements; split it into smaller functions",
                            rule="long_function")
                if len(n.params) > 4:
                    finding('Code Smell', n, f"Function '{n.name}' has {len(n.params)} parameters; more than 4 is hard to follow",
                            rule="many_parameters")
                walk(n.body, n.name, 0)
            elif isinstance(n, (ProgramNode, BlockNode)):
                stmts(n.statements, fn, depth)
            elif isinstance(n, IfNode):
                if isinstance(n.condition, NumNode):
                    finding('Warning', n, "Condition is always " + ("true: the else part never runs" if n.condition.value else "false: this block never runs"),
                            rule="constant_condition")
                if depth >= 3:
                    finding('Code Smell', n, 'Deeply nested conditional; consider simplifying it', rule="deep_nesting")
                magic(n.condition, fn)
                walk(n.then_branch, fn, depth + 1); walk(n.else_branch, fn, depth + 1)
            elif isinstance(n, SwitchNode):
                if depth >= 3:
                    finding('Code Smell', n, 'Deeply nested switch; consider moving it into a function', rule="deep_nesting")
                for c in n.cases:
                    stmts(c.statements, fn, depth + 1)
            elif isinstance(n, (WhileNode, ForNode, DoWhileNode)):
                if isinstance(n.condition, NumNode) and n.condition.value == 0 and not isinstance(n, DoWhileNode):
                    finding('Warning', n, "Loop condition is always false: the loop body never runs", rule="constant_condition")
                if depth >= 3:
                    finding('Code Smell', n, 'Deeply nested loop; consider moving the inner part into a function', rule="deep_nesting")
                magic(n.condition, fn)
                if isinstance(n, ForNode) and n.update is not None:
                    magic(n.update.expr, fn)
                walk(n.body, fn, depth + 1)
            elif isinstance(n, VarDeclNode):
                # `int limit = 50;` is how you NAME a number; only flag literals inside a larger expression
                if n.expr is not None and not isinstance(n.expr, (NumNode, InitListNode)) and not n.dims and fn != "<globals>":
                    magic(n.expr, fn)
            elif isinstance(n, PrintfNode):
                for a in n.args:
                    magic(a, fn)
            elif isinstance(n, (AssignNode, ReturnNode, PrintNode, ExprStmtNode)):
                if n.expr is not None and not (isinstance(n, ReturnNode) and isinstance(n.expr, NumNode)):
                    magic(n.expr, fn)

        walk(program, "<globals>", 0)
        out.extend(logic_checks(program))
        if call_graph:
            for name in call_graph.get("unreachable", []):
                line = call_graph.get("line", {}).get(name, 1)
                out.append({'category': 'Warning', 'message': f"Function '{name}' is never called from main",
                            'line': line, 'column': 1, 'rule': "unused_function"})
        return out


# =========================================================================== logic errors
def _children(n):
    for k in getattr(n, "__dataclass_fields__", {}):
        v = getattr(n, k)
        if isinstance(v, list):
            for x in v:
                if hasattr(x, "__dataclass_fields__"):
                    yield x
        elif hasattr(v, "__dataclass_fields__"):
            yield v


def _walk(n):
    yield n
    for c in _children(n):
        yield from _walk(c)


def _exits(n, loops=True):
    """Statements inside n that leave the enclosing loop: break (not one of an inner loop or switch) or return."""
    if isinstance(n, ReturnNode) or isinstance(n, BreakNode):
        return True
    if isinstance(n, (WhileNode, ForNode, DoWhileNode)):
        return any(isinstance(x, ReturnNode) for x in _walk(n.body))
    if isinstance(n, SwitchNode):                      # a break inside a switch only leaves the switch
        return any(isinstance(x, ReturnNode) for c in n.cases for st in c.statements for x in _walk(st))
    return any(_exits(c) for c in _children(n))


def _ends_flow(stmts):
    """Does this list of statements always finish with break, continue or return?"""
    if not stmts:
        return False
    last = stmts[-1]
    if isinstance(last, (BreakNode, ContinueNode, ReturnNode)):
        return True
    if isinstance(last, BlockNode):
        return _ends_flow(last.statements)
    if isinstance(last, IfNode) and last.else_branch is not None:
        return _ends_flow([last.then_branch]) and _ends_flow([last.else_branch])
    return False


def _has_call(e):
    return any(isinstance(x, CallNode) for x in _walk(e))


def _stray_semicolon(body):
    """A body that is only ';' (an empty statement on the same spot), not an intentional { }."""
    return (isinstance(body, BlockNode) and not body.statements and body.end_line == body.line
            and body.end_column == body.column)


def _interval(c):
    """x op K as (name, set-of-values description) for range reasoning: returns (name, lo, hi, not_equal)."""
    from compiler.semantic import const_value
    if not isinstance(c, BinOpNode) or c.op not in ("<", "<=", ">", ">=", "==", "!="):
        return None
    var, k, op = None, None, c.op
    if isinstance(c.left, VarNode) and const_value(c.right) is not None:
        var, k = c.left.name, const_value(c.right)
    elif isinstance(c.right, VarNode) and const_value(c.left) is not None:
        var, k = c.right.name, const_value(c.left)
        op = {"<": ">", "<=": ">=", ">": "<", ">=": "<=", "==": "==", "!=": "!="}[op]
    if var is None:
        return None
    inf = float("inf")
    return var, {"<": (-inf, k - 1), "<=": (-inf, k), ">": (k + 1, inf), ">=": (k, inf),
                 "==": (k, k), "!=": None}[op], (k if op == "!=" else None)


def logic_checks(program):
    from compiler.printer import expr as show
    from compiler.semantic import const_value
    out = []

    def warn(n, rule, msg):
        out.append({"category": "Warning", "rule": rule, "message": msg, "line": n.line, "column": n.column})

    for n in _walk(program):
        # if (x);  while (x);  for (...);
        if isinstance(n, IfNode) and _stray_semicolon(n.then_branch):
            warn(n, "empty_body", f"The ';' right after if ({show(n.condition)}) is the whole if-statement, so the "
                                  f"code below it runs every time. Remove that ';'.")
        if isinstance(n, (WhileNode, ForNode)) and _stray_semicolon(n.body):
            kind = "while" if isinstance(n, WhileNode) else "for"
            warn(n, "empty_body", f"The ';' right after the {kind} (...) is the whole loop body: the loop repeats "
                                  f"nothing, and the block below it runs only once, afterwards. Remove that ';'.")
        # switch fall-through
        if isinstance(n, SwitchNode):
            for a, b in zip(n.cases, n.cases[1:]):
                if a.statements and not _ends_flow(a.statements):
                    label = "default" if a.value is None else f"case {show(a.value)}"
                    nxt = "default" if b.value is None else f"case {show(b.value)}"
                    warn(a, "switch_fallthrough", f"{label} has no break, so after it runs the program carries on into "
                                                  f"{nxt} as well. Add break; at the end of {label} (if falling through is "
                                                  f"intended, say so in a comment).")
        # comparisons
        if isinstance(n, BinOpNode) and n.op in ("==", "!=", "<", ">", "<=", ">=") and not is_logical_not(n):
            if show(n.left) == show(n.right) and not _has_call(n.left) and not isinstance(n.left, (NumNode, FloatNode, CharNode)):
                answer = "true" if n.op in ("==", "<=", ">=") else "false"
                warn(n, "self_comparison", f"{show(n)} compares a value with itself, so it is always {answer}. "
                                           f"One side is probably meant to be a different variable.")
        if isinstance(n, BinOpNode) and n.op in ("&&", "||"):
            a, b = _interval(n.left), _interval(n.right)
            if a and b and a[0] == b[0]:
                name = a[0]
                if n.op == "&&":
                    if a[1] and b[1] and max(a[1][0], b[1][0]) > min(a[1][1], b[1][1]):
                        warn(n, "impossible_condition", f"{show(n)} can never be true: no value of {name} is in both ranges. "
                                                        f"Did you mean || (or the other comparison the other way round)?")
                else:
                    if a[1] and b[1] and (a[1][1] + 1 >= b[1][0] and b[1][1] + 1 >= a[1][0]) and \
                            min(a[1][0], b[1][0]) == float("-inf") and max(a[1][1], b[1][1]) == float("inf"):
                        warn(n, "impossible_condition", f"{show(n)} is always true: every value of {name} is in one of the ranges. "
                                                        f"Did you mean &&?")
        # the for counter changed inside the body
        if isinstance(n, ForNode) and isinstance(n.update, AssignNode) and not n.update.indices:
            v = n.update.name
            for x in _walk(n.body):
                if isinstance(x, AssignNode) and x.name == v and not x.indices:
                    warn(x, "counter_changed", f"The loop counter {v} is changed here, inside the loop body, and again by the "
                                               f"for header ({show(n.update.expr) if n.update.op == '=' else v + n.update.op}). "
                                               f"The loop will skip values or may never end; use a separate variable.")
                    break
        # constants that overflow an int
        if isinstance(n, BinOpNode) and n.op in "+-*" and getattr(n, "ctype", "int") == "int":
            v = const_value(n)
            if v is not None and not -2**31 <= v <= 2**31 - 1 and all(
                    const_value(c) is not None and -2**31 <= const_value(c) <= 2**31 - 1 for c in (n.left, n.right)):
                warn(n, "constant_overflow", f"{show(n)} = {v} does not fit in an int (largest {2**31 - 1:,}), so the result "
                                             f"wraps around to a wrong value. Do the sum in long: long big = (long) 50000 * 50000;")
        # while (1) / for (;;) without a way out
        if isinstance(n, (WhileNode, ForNode, DoWhileNode)):
            cond = n.condition
            forever = cond is None or (isinstance(cond, NumNode) and cond.value != 0)
            if forever and not _exits(n.body):
                warn(n, "endless_loop", "This loop never ends: its condition is always true and there is no break or "
                                        "return inside it. Add a condition, or a break when the work is done.")
    return out
