"""Bug detection, part 1: rule-based candidates + confirmation by running the program.

Rules (cheap, may over-report):
  possible_div_zero    dividing by a variable that can be 0 (set to 0, a parameter, the result of -, % or a call)
  infinite_loop        loop condition never changes inside the loop, or the counter moves the wrong way
  uninitialised_read   reading a variable before any value was stored in it
  no_effect            a statement like `x == 5;` or `x + 1;` that computes something and throws it away
  self_assignment      x = x;
  missing_base_case    a recursive call that runs before any if-statement can return
  missing_return       an int function that can reach its end without returning a value
  shadowing            a local variable hides another variable with the same name
  unreachable          code after return / under a condition that can never be true
Arrays, strings and types (compiler/typesys.py; expression types come from semantic analysis):
  array_out_of_bounds  a constant index outside the array, e.g. a[5] when a has 5 elements
  off_by_one           a loop `i <= N` (or `i < N+1`) that indexes an array of N elements with i
  sizeof_bytes         a loop up to sizeof(a), which counts bytes, not elements
  sizeof_pointer       sizeof on an array PARAMETER gives the size of an address (8), not the array
  array_size_argument  a function is told an array is longer than it really is
  uninit_array         a local array that is read but never filled
  array_read_before_write  count[x] = count[x] + 1 on an array that was never set to 0
  no_room_for_terminator   char s[5] = "hello": no place left for the '\0' that ends a string
  missing_terminator   a string built character by character without a '\0' at the end
  strcpy_overflow      strcpy / strcat copying more characters than the array holds
  strncpy_no_terminator    strncpy(d, s, sizeof d) leaves d without a '\0' when s is long
  string_compare_eq    s == t compares addresses; strcmp(s, t) == 0 compares the text
  strcmp_as_bool       if (strcmp(a, b)) is true when the strings are DIFFERENT
  integer_division     double avg = sum / n; divides whole numbers first and loses the decimals
  float_equality      comparing decimals with == (0.1 is stored approximately; a float is never 0.1)
  lost_decimals        storing a decimal value in an int cuts off the decimal part
  char_overflow        a char only holds -128..127
  scanf_char_newline   scanf("%c") after another scanf reads the space / Enter left behind
Then the MiniC VM runs the program (when main takes no input). A runtime error, an overflow or
a never-ending loop at a line confirms the matching candidate, or becomes a new finding. The VM
checks every array access, so out-of-bounds indexes, string overflows and missing '\0's are
caught exactly where they happen (real C would silently continue with garbage).

Part 2 (the LLM) lives in ai_features.detect_bugs: it judges each finding, explains it for a
beginner and proposes a fix, which is verified again by compiling and running.
"""
from compiler.parser import *
from compiler import typesys as T
from compiler.semantic import const_value
from ai_assistant.ir_interpreter import run_program
from compiler.static_analysis import _exits

SEVERITY = {"possible_div_zero": "high", "infinite_loop": "high", "uninitialised_read": "high",
            "no_effect": "medium", "self_assignment": "low", "missing_base_case": "high",
            "missing_return": "medium", "shadowing": "low", "unreachable": "low",
            "overflow": "medium", "runtime_error": "high", "constant_condition": "low",
            "array_out_of_bounds": "high", "off_by_one": "high", "sizeof_bytes": "high", "sizeof_pointer": "high",
            "array_size_argument": "high", "uninit_array": "high", "array_read_before_write": "high",
            "no_room_for_terminator": "high", "missing_terminator": "high", "strcpy_overflow": "high",
            "buffer_overflow": "high", "strncpy_no_terminator": "medium", "string_compare_eq": "high",
            "strcmp_as_bool": "medium", "integer_division": "medium", "float_equality": "medium",
            "lost_decimals": "low", "char_overflow": "medium", "scanf_char_newline": "medium", "math": "high",
            "empty_body": "high", "switch_fallthrough": "medium", "self_comparison": "medium",
            "impossible_condition": "high", "counter_changed": "medium", "constant_overflow": "medium"}

FIX_HINTS = {
    "empty_body": "Delete the ';' straight after the closing ')' so the block below belongs to the if / loop.",
    "switch_fallthrough": "End each case with break; unless you really want it to continue into the next case.",
    "self_comparison": "Compare with the value you meant, e.g. x == y instead of x == x.",
    "impossible_condition": "Check the operators: 'between 3 and 5' is x >= 3 && x <= 5; 'outside' is x < 3 || x > 5.",
    "counter_changed": "Leave the loop counter to the for header; use another variable for the value you change.",
    "constant_overflow": "Calculate in long: long big = (long) 50000 * 50000; and print it with %ld.",
    "possible_div_zero": "Check the divisor first: if (d != 0) { ... } else { ... }",
    "infinite_loop": "Change the loop variable inside the loop so the condition becomes false, e.g. i = i + 1;",
    "uninitialised_read": "Give the variable a value when you declare it, e.g. int sum = 0;",
    "missing_base_case": "Start the function with a stopping case, e.g. if (n <= 1) { return 1; }",
    "missing_return": "Add a return statement at the end of every path.",
    "array_out_of_bounds": "Valid indexes are 0 to size - 1. Use an index inside that range or make the array bigger.",
    "off_by_one": "Use < instead of <= : for (i = 0; i < N; i = i + 1) visits indexes 0 to N - 1.",
    "sizeof_bytes": "Count elements, not bytes: sizeof(a) / sizeof(a[0]).",
    "sizeof_pointer": "Pass the number of elements as an extra parameter: int sum(int a[], int n).",
    "array_size_argument": "Pass the real number of elements (or compute it with sizeof(a) / sizeof(a[0]) where the array is declared).",
    "uninit_array": "Fill the array before reading it, e.g. int a[5] = {0}; or a loop that stores a value in every element.",
    "array_read_before_write": "Start every element at 0 first: int count[10] = {0};",
    "no_room_for_terminator": 'Make the array one longer than the text (char s[6] = "hello";) or leave the size out: char s[] = "hello";',
    "missing_terminator": "After the last character, store the end marker: s[i] = '\\0';",
    "strcpy_overflow": "Make the destination array big enough for the text plus 1 (for the '\\0').",
    "buffer_overflow": "Make the destination array big enough for the text plus 1 (for the '\\0').",
    "strncpy_no_terminator": "Copy one less than the size and finish the string yourself: strncpy(d, s, N - 1); d[N - 1] = '\\0';",
    "string_compare_eq": "Compare the text with strcmp: if (strcmp(a, b) == 0) { ... }",
    "strcmp_as_bool": "Write the comparison out: strcmp(a, b) == 0 means equal, != 0 means different.",
    "integer_division": "Make one side a decimal before dividing: (double) sum / n, or sum / 2.0",
    "float_equality": "Compare with a small tolerance: fabs(a - b) < 0.0001. For float variables write 0.1f.",
    "lost_decimals": "Use a double variable, or round on purpose: (int) (x + 0.5).",
    "char_overflow": "Use an int for numbers; a char only holds -128 to 127.",
    "scanf_char_newline": 'Put a space before %c: scanf(" %c", &ch); skips spaces and new lines first.',
    "overflow": "Use a bigger type (long) or keep the numbers smaller.",
    "no_effect": "Did you mean = (assign) instead of == (compare)?",
    "math": "Check the value first, e.g. if (x >= 0) before sqrt(x).",
}


def _names(e):
    if isinstance(e, VarNode): return {e.name}
    if isinstance(e, IndexNode): return {e.name}.union(*[_names(i) for i in e.indices])
    if isinstance(e, CastNode): return _names(e.expr)
    if isinstance(e, BinOpNode): return _names(e.left) | _names(e.right)
    if isinstance(e, CallNode): return set().union(*map(_names, e.args)) if e.args else set()
    return set()


def _has_call(e):
    if isinstance(e, CallNode): return True
    if isinstance(e, BinOpNode): return _has_call(e.left) or _has_call(e.right)
    if isinstance(e, CastNode): return _has_call(e.expr)
    if isinstance(e, IndexNode): return any(_has_call(i) for i in e.indices)
    return False


def _walk(n, fn):
    """Yield every node below n (pre-order)."""
    if n is None: return
    yield n
    for k in getattr(n, "__dataclass_fields__", {}):
        v = getattr(n, k)
        if isinstance(v, list):
            for x in v:
                if hasattr(x, "__dataclass_fields__"): yield from _walk(x, fn)
        elif hasattr(v, "__dataclass_fields__"):
            yield from _walk(v, fn)


def _assigned_in(n):
    out = {x.name for x in _walk(n, None) if isinstance(x, (AssignNode, VarDeclNode))}
    for x in _walk(n, None):
        if isinstance(x, ScanfNode):
            out |= {name for name, *_ in x.names}
    return out


def _returns(n):
    return any(isinstance(x, ReturnNode) for x in _walk(n, None))


def _always_returns(n):
    if isinstance(n, ReturnNode): return True
    if isinstance(n, BlockNode): return any(_always_returns(s) for s in n.statements)
    if isinstance(n, IfNode): return n.else_branch is not None and _always_returns(n.then_branch) and _always_returns(n.else_branch)
    if isinstance(n, DoWhileNode): return _always_returns(n.body) and not _breaks(n.body)
    if isinstance(n, SwitchNode):
        # every value reaches some case (there is a default), no case breaks out, and falling through ends in a return
        return (any(c.value is None for c in n.cases) and not any(_breaks(s) for c in n.cases for s in c.statements)
                and bool(n.cases) and any(_always_returns(s) for s in n.cases[-1].statements))
    return False


def _breaks(n):
    """True if a break inside n leaves n's enclosing loop or switch (breaks in nested loops/switches do not count)."""
    if isinstance(n, BreakNode): return True
    if isinstance(n, BlockNode): return any(_breaks(s) for s in n.statements)
    if isinstance(n, IfNode): return _breaks(n.then_branch) or (n.else_branch is not None and _breaks(n.else_branch))
    return False


def find_candidates(check):
    """Rule-based suspects. `check` is the dict from context_builder.check_source (must be ok)."""
    ast, out = check["ast"], []

    def add(rule, node, title, message, **extra):
        d = {"rule": rule, "line": node.line, "title": title, "message": message,
             "severity": SEVERITY[rule], "source": "rule", "confirmed": False, "evidence": None, "fix_hint": FIX_HINTS.get(rule)}
        d.update(extra)
        out.append(d)

    globals_ = {s.name for s in ast.statements if isinstance(s, VarDeclNode)}
    for fn in [s for s in ast.statements if isinstance(s, FunctionNode)]:
        params = {p[1] for p in fn.params}
        values = {}
        for x in _walk(fn.body, fn):
            if isinstance(x, (AssignNode, VarDeclNode)) and x.expr is not None:
                values.setdefault(x.name, []).append(x.expr)
        # possible division by zero
        for x in _walk(fn.body, fn):
            if isinstance(x, BinOpNode) and x.op in ("/", "%") and isinstance(x.right, VarNode):
                d = x.right.name
                risky = d in params or any(
                    (isinstance(v, NumNode) and v.value == 0) or _has_call(v) or
                    (isinstance(v, BinOpNode) and v.op in ("-", "%")) for v in values.get(d, []))
                if risky:
                    add("possible_div_zero", x, f"{d} could be 0 when dividing",
                        f"Line {x.line} divides by {d}, and {d} " + ("is a parameter, so callers can pass 0." if d in params else "can become 0 (it is set to 0, or computed with -, % or a call)."),
                        variable=d)
        # loops
        for x in _walk(fn.body, fn):
            if isinstance(x, (WhileNode, ForNode, DoWhileNode)):
                cond = x.condition
                if _exits(x.body):
                    continue                      # a break or return inside is the way out
                if isinstance(cond, NumNode):
                    if cond.value and not _returns(x.body):
                        add("infinite_loop", x, "Loop never ends", f"The loop on line {x.line} has a condition that is always true and no return inside it.",
                            end_line=x.end_line)
                    continue
                if cond is None:
                    continue
                changed = _assigned_in(x.body) | ({x.update.name} if isinstance(x, ForNode) and x.update else set())
                names = _names(cond)
                if names and not (names & changed) and not _returns(x.body) and not _has_call(cond):
                    add("infinite_loop", x, "Loop condition never changes",
                        f"The loop on line {x.line} tests {', '.join(sorted(names))}, but nothing inside the loop changes "
                        f"{'it' if len(names) == 1 else 'them'}. If the condition is true once, it stays true forever.",
                        end_line=x.end_line)
                    continue
                upd = x.update if isinstance(x, ForNode) else next((s for s in reversed(x.body.statements) if isinstance(s, AssignNode)), None) if isinstance(x.body, BlockNode) else None
                if upd is not None and upd.indices:
                    upd = None
                if (isinstance(cond, BinOpNode) and cond.op in ("<", "<=", ">", ">=") and isinstance(cond.left, VarNode)
                        and upd is not None and upd.name == cond.left.name and isinstance(upd.expr, BinOpNode)
                        and isinstance(upd.expr.left, VarNode) and upd.expr.left.name == upd.name and isinstance(upd.expr.right, NumNode)):
                    up = (upd.expr.op == "+") == (upd.expr.right.value > 0)
                    if upd.expr.op in "+-" and ((cond.op in ("<", "<=") and not up) or (cond.op in (">", ">=") and up)):
                        add("infinite_loop", x, "Loop counter moves the wrong way",
                            f"The loop on line {x.line} continues while {cond.left.name} {cond.op} ..., but {upd.name} is "
                            f"{'decreased' if not up else 'increased'} each time, so it moves away from the end.", end_line=x.end_line)
            # no-effect statements and self assignment
            if isinstance(x, ExprStmtNode) and not isinstance(x.expr, CallNode) and not _has_call(x.expr):
                hint = ""
                if isinstance(x.expr, BinOpNode) and x.expr.op == "==" and isinstance(x.expr.left, VarNode):
                    hint = f" Did you mean the assignment {x.expr.left.name} = ...; (one '=')?"
                add("no_effect", x, "Statement has no effect", f"Line {x.line} computes a value and throws it away.{hint}")
            if isinstance(x, AssignNode) and isinstance(x.expr, VarNode) and x.expr.name == x.name and not x.indices:
                add("self_assignment", x, "Variable assigned to itself", f"Line {x.line} sets {x.name} = {x.name}, which does nothing.")
        # recursion without a base case
        if fn.name in check["call_graph"].get("recursive", []):
            for s in fn.body.statements:
                if isinstance(s, IfNode) and _returns(s):
                    break
                if any(isinstance(c, CallNode) and c.name == fn.name for c in _walk(s, fn)):
                    add("missing_base_case", s, "Recursion without a stopping case",
                        f"{fn.name}() calls itself on line {s.line} before any if-statement can return, so it may never stop.")
                    break
        # missing return
        if fn.return_type != "void" and fn.name != "main" and not _always_returns(fn.body):
            add("missing_return", fn, "May finish without returning a value",
                f"{fn.name}() is declared to return {fn.return_type}, but some paths reach the end of the function without a return statement.")
        _data_rules(ast, fn, add)
        # shadowing
        def shadow(n, scopes):
            if isinstance(n, BlockNode):
                scopes = scopes + [set()]
                for s in n.statements:
                    shadow(s, scopes)
                return
            if isinstance(n, VarDeclNode):
                if any(n.name in sc for sc in scopes[:-1]):
                    add("shadowing", n, "Variable hides another one", f"Line {n.line} declares a new {n.name}, hiding the {n.name} declared outside this block. Changes to it will not affect the outer one.")
                scopes[-1].add(n.name)
                return
            for k in ("then_branch", "else_branch", "body"):
                v = getattr(n, k, None)
                if v is not None:
                    shadow(v, scopes)
            if isinstance(n, ForNode) and isinstance(n.init, VarDeclNode):
                shadow(n.init, scopes)
        shadow(fn.body, [set(globals_), set(params)])
    # semantic + static warnings that are likely bugs
    for w in check.get("warnings", []):
        if "before initialization" in w["message"]:
            out.append({"rule": "uninitialised_read", "line": w["line"], "title": "Variable may be used before it has a value",
                        "message": w["message"], "severity": "high", "source": "rule", "confirmed": False, "evidence": None,
                        "fix_hint": FIX_HINTS["uninitialised_read"]})
    logic_titles = {"empty_body": "Stray ';' after if / loop", "switch_fallthrough": "case falls through into the next case",
                    "self_comparison": "Compares a value with itself", "impossible_condition": "Condition can never change its answer",
                    "counter_changed": "Loop counter changed inside the loop", "constant_overflow": "Constant calculation overflows int"}
    for d in check.get("static", []):
        if d.get("rule") in ("unreachable", "constant_condition"):
            out.append({"rule": d["rule"], "line": d["line"], "title": "Code that never runs" if d["rule"] == "unreachable" else "Condition never changes",
                        "message": d["message"], "severity": "low", "source": "rule", "confirmed": False, "evidence": None})
        elif d.get("rule") in logic_titles:
            out.append({"rule": d["rule"], "line": d["line"], "title": logic_titles[d["rule"]], "message": d["message"],
                        "severity": SEVERITY[d["rule"]], "source": "rule", "confirmed": False, "evidence": None,
                        "fix_hint": FIX_HINTS[d["rule"]], "any_line": d["rule"] in ("empty_body", "counter_changed")})
    return out


# ------------------------------------------------------------ arrays, strings and types
STRING_READERS = {"strlen": (0,), "strcpy": (1,), "strncpy": (1,), "strcat": (0, 1), "strncat": (0, 1),
                  "strcmp": (0, 1), "strncmp": (0, 1), "puts": (0,)}


def _ctype(n):
    return getattr(n, "ctype", None) or ""


def _source_order(nodes):
    return sorted(nodes, key=lambda n: (n.line, n.column))


def _data_rules(ast, fn, add):
    """Rules for array, string and number-type mistakes in one function."""
    arrays = {}                                   # name -> info about the array
    scalars = {}                                  # name -> type of plain variables
    for g in ast.statements:
        if isinstance(g, VarDeclNode):
            if g.dims:
                arrays[g.name] = {"type": _ctype(g), "param": False, "local": False, "decl": g}
            else:
                scalars[g.name] = g.var_type
    for prm in fn.params:
        if len(prm) > 4:
            arrays[prm[1]] = {"type": T.make(prm[0], (None,) + tuple(const_value(d) or 1 for d in prm[4][1:])),
                              "param": True, "local": False, "decl": None}
        else:
            scalars[prm[1]] = prm[0]
    nodes = list(_walk(fn.body, fn))
    for x in nodes:
        if isinstance(x, VarDeclNode):
            if x.dims:
                arrays[x.name] = {"type": _ctype(x), "param": False, "local": True, "decl": x}
            else:
                scalars[x.name] = x.var_type
                arrays.pop(x.name, None)

    def dims_of(name):
        return T.parse(arrays[name]["type"])[1] if name in arrays and arrays[name]["type"] else ()

    # names that hold one constant value for the whole function (int n = 5; never changed)
    assigned = {}
    for x in nodes:
        if isinstance(x, (AssignNode, VarDeclNode)) and not getattr(x, "indices", None) and not getattr(x, "dims", None):
            assigned.setdefault(x.name, []).append(x.expr)
        if isinstance(x, ScanfNode):
            for t in x.names:
                assigned.setdefault(t[0], []).append(None)
    for g in ast.statements:
        if isinstance(g, VarDeclNode) and not g.dims:
            assigned.setdefault(g.name, []).append(g.expr)

    def const(e):
        v = const_value(e) if e is not None else None
        if v is None and isinstance(e, VarNode) and len(assigned.get(e.name, [])) == 1 and assigned[e.name][0] is not None:
            v = const_value(assigned[e.name][0])
        return v

    def uses(node):
        """(array name, index position, index expression) for every a[...] in node."""
        out = []
        for y in _walk(node, fn):
            if isinstance(y, (IndexNode, AssignNode)) and getattr(y, "indices", None):
                out += [(y.name, k, e, y) for k, e in enumerate(y.indices)]
            if isinstance(y, ScanfNode):
                for t in y.names:
                    if len(t) > 3:
                        out += [(t[0], k, e, Node(line=t[1], column=t[2])) for k, e in enumerate(t[3])]
        return out

    # 1. constant index outside the array
    for name, k, e, where in uses(fn.body):
        d = dims_of(name)
        c = const_value(e)
        if c is None or k >= len(d) or d[k] is None:
            continue
        if c < 0 or c >= d[k]:
            add("array_out_of_bounds", where, f"Index {c} is outside the array {name}",
                f"Line {where.line} uses {name}[{c}], but {name} has {d[k]} elements, so the valid indexes are 0 to {d[k] - 1}. "
                "Array indexes start at 0, so the last element is size - 1.", any_line=False)

    # 2. loops that run one step too far (or up to sizeof)
    for x in nodes:
        if not isinstance(x, (ForNode, WhileNode)) or not isinstance(x.condition, BinOpNode):
            continue
        c = x.condition
        if isinstance(c.left, VarNode) and c.op in ("<", "<="):
            var, op, bound = c.left.name, c.op, c.right
        elif isinstance(c.right, VarNode) and c.op in (">", ">="):
            var, op, bound = c.right.name, {">": "<", ">=": "<="}[c.op], c.left
        else:
            var = None
        reported = set()
        if var:
            for name, k, e, where in uses(x.body):
                if not (isinstance(e, VarNode) and e.name == var) or name in reported:
                    continue
                d = dims_of(name)
                size = d[k] if k < len(d) else None
                if isinstance(bound, SizeofNode) and isinstance(bound.expr, VarNode) and bound.expr.name in arrays:
                    elem = T.sizeof(T.parse(arrays[bound.expr.name]["type"])[0])
                    if elem > 1:
                        reported.add(name)
                        add("sizeof_bytes", x, "sizeof counts bytes, not elements",
                            f"The loop on line {x.line} runs while {var} {c.op} sizeof({bound.expr.name}). sizeof gives the size in BYTES: "
                            f"each element is {elem} bytes, so the loop goes {elem} times too far and {name}[{var}] runs off the end of the array.",
                            end_line=x.end_line)
                    continue
                b = const(bound)
                if size is None:
                    if op == "<=" and arrays.get(name, {}).get("param"):
                        reported.add(name)
                        add("off_by_one", x, "Loop goes one element too far",
                            f"The loop on line {x.line} runs while {var} <= {_text(bound)}, so the last pass uses {name}[{_text(bound)}]. "
                            f"If {_text(bound)} is the number of elements, the last valid index is {_text(bound)} - 1.", end_line=x.end_line)
                    continue
                if b is None:
                    continue
                last = b if op == "<=" else b - 1
                if last >= size:
                    reported.add(name)
                    shown = name + "[...]" * k + f"[{last}]"
                    holder = f"each row of {name} has {size} elements" if k else f"{name} has {size} elements"
                    add("off_by_one", x, "Loop goes past the end of the array",
                        f"The loop on line {x.line} runs while {var} {c.op} {_text(bound)}, so {var} reaches {last} and line {where.line} uses "
                        f"{shown}. But {holder}, so the last valid index there is {size - 1}"
                        + (" (use < instead of <=)." if op == "<=" and last == size else "."), end_line=x.end_line)
        # counting down from the size: for (i = 5; i >= 0; ...) with a[5]
        if isinstance(x, ForNode) and x.init is not None and c.op in (">=", ">"):
            start = const_value(getattr(x.init, "expr", None))
            v = x.init.name
            for name, k, e, where in uses(x.body):
                d = dims_of(name)
                if isinstance(e, VarNode) and e.name == v and start is not None and k < len(d) and d[k] and start >= d[k] and name not in reported:
                    reported.add(name)
                    add("off_by_one", x, "Loop starts past the end of the array",
                        f"The loop on line {x.line} starts with {v} = {start}, so the first pass uses {name}[{start}], "
                        f"but the last valid index of {name} is {d[k] - 1}. Start at {d[k] - 1}.", end_line=x.end_line)

    # 3. sizeof of an array parameter
    for x in nodes:
        if isinstance(x, SizeofNode) and isinstance(x.expr, VarNode) and arrays.get(x.expr.name, {}).get("param"):
            add("sizeof_pointer", x, "sizeof on an array parameter",
                f"Line {x.line} uses sizeof({x.expr.name}), but {x.expr.name} is a parameter. An array parameter only holds the "
                f"ADDRESS of the caller's array, so sizeof gives 8, not the size of the array.", any_line=True)

    # 4. calls that pass a too-large length
    for x in nodes:
        if not isinstance(x, CallNode):
            continue
        callee = next((f for f in ast.statements if isinstance(f, FunctionNode) and f.name == x.name), None)
        if callee is None:
            continue
        sizes = [dims_of(a.name)[0] for a in x.args if isinstance(a, VarNode) and a.name in arrays and dims_of(a.name) and dims_of(a.name)[0]]
        for a in x.args:
            n = const(a) if not (isinstance(a, VarNode) and a.name in arrays) else None
            if sizes and n is not None and n > max(sizes):
                arr = next(b.name for b in x.args if isinstance(b, VarNode) and b.name in arrays and dims_of(b.name)[0] == max(sizes))
                add("array_size_argument", x, "Function is told the array is longer than it is",
                    f"Line {x.line} calls {x.name}() with {arr} ({max(sizes)} elements) and the number {n}. If {x.name}() uses {n} as the "
                    f"length, it reads {arr}[{max(sizes)}] and beyond, which do not exist.", any_line=True)
                break

    # 5. local arrays that are never filled, or read before being set
    for name, info in arrays.items():
        decl = info["decl"]
        if not info["local"] or decl is None or decl.expr is not None:
            continue
        writes, reads = [], []
        for y in nodes:
            if isinstance(y, AssignNode) and y.name == name and y.indices:
                writes.append(y)
            elif isinstance(y, ScanfNode) and any(t[0] == name for t in y.names):
                writes.append(y)
            elif isinstance(y, CallNode):
                for k, a in enumerate(y.args):
                    if isinstance(a, (VarNode, IndexNode)) and a.name == name:
                        if (y.name in T.WRITES_FIRST_ARG and k == 0) or y.name not in T.BUILTINS:
                            writes.append(y)
                        elif y.name in STRING_READERS:
                            reads.append(y)
            elif isinstance(y, IndexNode) and y.name == name:
                reads.append(y)
            elif isinstance(y, PrintfNode) and any(isinstance(a, VarNode) and a.name == name for a in y.args):
                reads.append(y)
        if reads and not writes:
            first = _source_order(reads)[0]
            add("uninit_array", first, f"Array {name} is read but never filled",
                f"{name} is declared on line {decl.line} without values and nothing stores into it, but line {first.line} reads it. "
                "A local array starts with leftover garbage values.", any_line=True)
            continue
        if writes:
            first = _source_order(writes)[0]
            if isinstance(first, AssignNode) and any(isinstance(z, IndexNode) and z.name == name for z in _walk(first.expr, fn)):
                add("array_read_before_write", first, f"{name}[...] is used before it was set",
                    f"Line {first.line} computes {name}[...] from {name}[...] itself, but {name} (line {decl.line}) never got starting "
                    f"values. Its elements hold garbage, so the result is garbage too.", any_line=True)
        # strings built one character at a time
        base, d = T.parse(info["type"])
        used_as_string = [y for y in nodes if
                          (isinstance(y, CallNode) and y.name in STRING_READERS and any(
                              isinstance(y.args[k], VarNode) and y.args[k].name == name for k in STRING_READERS[y.name] if k < len(y.args)))
                          or (isinstance(y, PrintfNode) and any(isinstance(a, VarNode) and a.name == name for a in y.args))]
        filled_by_function = any(isinstance(w, (ScanfNode, CallNode)) for w in writes)
        if base == "char" and len(d) == 1 and used_as_string and writes and not filled_by_function:
            zero = any(isinstance(w, AssignNode) and isinstance(w.expr, (NumNode, CharNode)) and w.expr.value == 0 for w in writes)
            if not zero:
                where = _source_order(used_as_string)[0]
                add("missing_terminator", where, f"String {name} has no '\\0' at its end",
                    f"{name} is filled one character at a time, but no '\\0' is ever stored after the last character. "
                    f"Line {where.line} uses it as a string, so it keeps reading past the text into garbage.", any_line=True)

    # 6. string initialisers, copies and comparisons
    for name, info in arrays.items():
        decl = info["decl"]
        base, d = T.parse(info["type"])
        if decl is not None and isinstance(decl.expr, StringNode) and d and d[0] and decl.dims and decl.dims[0] is not None:
            text = T.decode(decl.expr.value)[0]
            if len(text) == d[0]:
                add("no_room_for_terminator", decl, f"No room for the '\\0' in {name}",
                    f"char {name}[{d[0]}] = \"{decl.expr.value}\" stores {len(text)} characters in {d[0]} places, so the '\\0' that marks the end "
                    f"of the string does not fit. strlen, printf(\"%s\") and strcpy will read past the end.", any_line=True)

    def literal_len(a):
        if isinstance(a, StringNode):
            return len(T.decode(a.value)[0])
        if isinstance(a, VarNode) and a.name in arrays and arrays[a.name]["decl"] is not None and isinstance(arrays[a.name]["decl"].expr, StringNode):
            if not any(isinstance(y, (AssignNode, ScanfNode)) and getattr(y, "name", None) == a.name for y in nodes):
                return len(T.decode(arrays[a.name]["decl"].expr.value)[0])
        return None

    for x in nodes:
        if isinstance(x, CallNode) and x.name in ("strcpy", "strcat") and len(x.args) == 2 and isinstance(x.args[0], VarNode):
            dst = x.args[0].name
            d = dims_of(dst)
            src_len = literal_len(x.args[1])
            if not d or d[0] is None or src_len is None:
                continue
            have = literal_len(x.args[0]) if x.name == "strcat" else 0
            if have is None:
                continue
            need = have + src_len + 1
            if need > d[0]:
                add("strcpy_overflow", x, f"{x.name} writes past the end of {dst}",
                    f"Line {x.line}: {x.name}() needs {need} places ({have + src_len} characters + '\\0'), but {dst} only has {d[0]}. "
                    "C does not check this: it overwrites whatever comes after the array.", any_line=False)
        if isinstance(x, CallNode) and x.name == "strncpy" and len(x.args) == 3 and isinstance(x.args[0], VarNode):
            d = dims_of(x.args[0].name)
            n = const(x.args[2])
            if d and d[0] and n is not None and n >= d[0] and not any(
                    isinstance(y, AssignNode) and y.name == x.args[0].name and y.indices and isinstance(y.expr, (NumNode, CharNode)) and y.expr.value == 0
                    for y in nodes):
                add("strncpy_no_terminator", x, "strncpy may leave no '\\0'",
                    f"Line {x.line} copies up to {n} characters into {x.args[0].name}[{d[0]}]. When the source is {n} characters or longer, "
                    f"strncpy fills every place and does NOT add a '\\0', so {x.args[0].name} is not a proper string.", any_line=True)
        if isinstance(x, BinOpNode) and x.op in ("==", "!=") and T.is_array(_ctype(x.left)) and T.is_array(_ctype(x.right)):
            add("string_compare_eq", x, "Strings compared with " + x.op,
                f"Line {x.line} uses {x.op} on two strings. For arrays, {x.op} compares their ADDRESSES (where they are in memory), "
                f"not the letters, so it is {'false' if x.op == '==' else 'true'} even when the text is the same.")

    conds = [x.condition for x in nodes if isinstance(x, (IfNode, WhileNode, ForNode)) and x.condition is not None]
    for c in conds:
        call = None
        if isinstance(c, CallNode) and c.name in ("strcmp", "strncmp"):
            call = c
            msg = (f"Line {c.line} uses {c.name}(...) directly as a condition. {c.name} gives 0 when the strings are EQUAL, "
                   "so the condition is true when they are DIFFERENT.")
        elif (isinstance(c, BinOpNode) and c.op in ("==", "!=") and isinstance(c.left, CallNode) and c.left.name in ("strcmp", "strncmp")
              and const_value(c.right) in (1, -1)):
            call = c
            msg = (f"Line {c.line} compares {c.left.name}(...) with {const_value(c.right)}. {c.left.name} only promises a negative number, 0 "
                   "or a positive number (often not exactly 1 or -1), and 0 means EQUAL.")
        if call is not None:
            add("strcmp_as_bool", call, "strcmp result used the wrong way", msg)

    # 7. number types
    ret_type = fn.return_type

    def target_type(n):
        if isinstance(n, VarDeclNode):
            return None if n.dims else n.var_type
        if isinstance(n, AssignNode):
            if n.indices:
                t = arrays.get(n.name, {}).get("type", "")
                return T.parse(t)[0] if t and len(n.indices) == len(T.parse(t)[1]) else None
            return scalars.get(n.name)
        if isinstance(n, ReturnNode):
            return ret_type
        return None

    def int_division(e):
        for y in _walk(e, fn):
            if isinstance(y, CastNode) and T.is_floating(y.to_type) and isinstance(y.expr, BinOpNode) and y.expr.op == "/" \
                    and T.is_integer(_ctype(y.expr.left)) and T.is_integer(_ctype(y.expr.right)):
                return y.expr, True
            if isinstance(y, BinOpNode) and y.op == "/" and T.is_integer(_ctype(y.left)) and T.is_integer(_ctype(y.right)):
                return y, False
        return None, False

    for x in nodes + [s for s in []]:
        if not isinstance(x, (VarDeclNode, AssignNode, ReturnNode)) or x.expr is None or isinstance(x.expr, (InitListNode, StringNode)):
            continue
        tt = target_type(x)
        if not tt:
            continue
        et = _ctype(x.expr)
        what = x.name if not isinstance(x, ReturnNode) else f"the result of {fn.name}()"
        if T.is_floating(tt):
            div, cast_late = int_division(x.expr)
            if div is not None and not (const_value(div.left) is not None and const_value(div.right) is not None
                                         and const_value(div.right) and const_value(div.left) % const_value(div.right) == 0):
                add("integer_division", div, "Whole-number division loses the decimals",
                    f"Line {div.line} divides {_text(div.left)} by {_text(div.right)}. Both are whole numbers, so C does whole-number "
                    f"division FIRST (7 / 2 is 3, not 3.5) and only then stores the result in the {tt} {what}."
                    + (" The cast comes too late: it converts the already-cut result." if cast_late else ""))
        elif tt in T.INTEGER and T.is_floating(et) and not isinstance(x.expr, CastNode):
            add("lost_decimals", x, "Decimal part is cut off",
                f"Line {x.line} stores a {et} value in the {tt} {what}. The part after the decimal point is thrown away "
                "(9.99 becomes 9), without rounding.")
        if tt in ("char", "short"):
            v = const_value(x.expr)
            lo, hi = (-128, 127) if tt == "char" else (-32768, 32767)
            if v is not None and not lo <= v <= hi:
                add("char_overflow", x, f"Value does not fit in a {tt}",
                    f"Line {x.line} stores {v} in the {tt} {what}, but a {tt} only holds {lo} to {hi}. "
                    f"It becomes {T.wrap(v, tt)} instead.")
    for x in nodes:
        if isinstance(x, BinOpNode) and x.op in ("==", "!=") and (T.is_floating(_ctype(x.left)) or T.is_floating(_ctype(x.right))):
            other = x.right if T.is_floating(_ctype(x.left)) else x.left
            if const_value(other) == 0 or (isinstance(other, FloatNode) and other.value == 0):
                continue
            kinds = {_ctype(x.left), _ctype(x.right)}
            extra = (" Here a float (about 7 digits) is compared with a double (about 16 digits), so they are never exactly equal."
                     if kinds == {"float", "double"} else "")
            add("float_equality", x, "Decimals compared with " + x.op,
                f"Line {x.line} compares decimal numbers with {x.op}. Most decimals (like 0.1) cannot be stored exactly, so two values "
                f"that should be equal are often a tiny bit different.{extra}")

    # 8. scanf("%c") after another scanf
    scanfs = _source_order([x for x in nodes if isinstance(x, ScanfNode)])
    for prev, cur in zip(scanfs, scanfs[1:]):
        if cur.format.startswith("%c"):
            add("scanf_char_newline", cur, "scanf(\"%c\") reads the leftover space or Enter",
                f"Line {cur.line} reads one character with \"%c\". The previous scanf (line {prev.line}) stopped right before the space or "
                "Enter the user typed after their input, so %c reads THAT instead of the character they meant.")


def _text(e):
    from compiler.printer import expr
    try:
        return expr(e)
    except Exception:
        return "?"


KIND_RULES = {
    "div_zero": ["possible_div_zero"], "step_limit": ["infinite_loop", "empty_body", "counter_changed"], "stack_overflow": ["missing_base_case"],
    "uninit": ["uninitialised_read", "uninit_array", "array_read_before_write", "missing_terminator"],
    "out_of_bounds": ["array_out_of_bounds", "off_by_one", "sizeof_bytes", "sizeof_pointer", "array_size_argument", "counter_changed", "empty_body"],
    "buffer_overflow": ["strcpy_overflow", "no_room_for_terminator", "missing_terminator", "strncpy_no_terminator"],
    "no_terminator": ["no_room_for_terminator", "missing_terminator", "strncpy_no_terminator", "strcpy_overflow"],
}
KIND_FINDING = {
    "div_zero": ("possible_div_zero", "Division by zero"), "step_limit": ("infinite_loop", "Program never finishes"),
    "uninit": ("uninitialised_read", "Variable used before it has a value"), "stack_overflow": ("missing_base_case", "Recursion never stops"),
    "out_of_bounds": ("array_out_of_bounds", "Array index out of bounds"), "buffer_overflow": ("buffer_overflow", "String does not fit in its array"),
    "no_terminator": ("missing_terminator", "String without '\\0' at the end"), "math": ("math", "Maths function given an impossible value"),
}


def confirm_by_running(check, candidates, max_steps=300_000, stdin=None):
    """Run the program; mark candidates the run proves and add findings the run reveals."""
    ast = check["ast"]
    main = next((s for s in ast.statements if isinstance(s, FunctionNode) and s.name == "main"), None)
    if main is None or main.params:
        return {"ran": False, "reason": "main needs input, so the program cannot be run automatically"}, candidates
    r = run_program(check["ir"], check["line_map"], max_steps=max_steps, stdin=stdin)
    summary = {"ran": True, "used_sample_input": r["used_sample_input"], "inputs": r["inputs_used"][:20], "output": r["output"][:50], "return_value": r["return_value"], "error": r["error"],
               "error_kind": r["error_kind"], "error_line": r["error_line"], "steps": r["steps"]}
    found = list(candidates)

    def near(c, line):
        return c["line"] == line or (c.get("end_line") and c["line"] <= line <= c["end_line"])

    if r["error_kind"]:
        rules = KIND_RULES.get(r["error_kind"], [])
        match = [c for c in found if c["rule"] in rules and (near(c, r["error_line"]) or c.get("any_line") or c["rule"] == "missing_base_case")]
        evidence = f"Running the program: {r['error']} (line {r['error_line']})."
        if match:
            for c in match:
                c["confirmed"], c["evidence"] = True, evidence
        else:
            rule, title = KIND_FINDING.get(r["error_kind"], ("runtime_error", "Runtime error"))
            found.append({"rule": rule, "line": r["error_line"] or 1, "title": title,
                          "message": r["error"], "severity": "high", "source": "execution", "confirmed": True, "evidence": evidence,
                          "fix_hint": FIX_HINTS.get(rule)})
    for ov in r["overflows"][:3]:
        static = [c for c in found if c["rule"] in ("char_overflow", "constant_overflow") and c["line"] == ov["line"]]
        if static:
            for c in static:
                c["confirmed"], c["evidence"] = True, f"Running the program: {ov['message']}."
            continue
        conversion = ov["message"].startswith(("converting", "the decimal"))
        found.append({"rule": "overflow", "line": ov["line"], "title": "Value too big for its type" if conversion else "Integer overflow",
                      "message": ov["message"] + ("." if conversion else ". The result is wrong because int only holds values up to 2,147,483,647."),
                      "severity": "medium", "source": "execution", "confirmed": True,
                      "evidence": f"Running the program: {ov['message']}.", "fix_hint": FIX_HINTS["overflow"]})
    note_rules = {"char_whitespace": "scanf_char_newline", "truncation": "lost_decimals", "address_compare": "string_compare_eq",
                  "float_compare": "float_equality", "int_division": "integer_division", "fallthrough": "switch_fallthrough"}
    for n in r.get("notes", []):
        for c in found:
            if c["rule"] == note_rules.get(n["kind"]) and not c["confirmed"] and near(c, n["line"]):
                c["confirmed"], c["evidence"] = True, f"Running the program: {n['message']} (line {n['line']})."
    for w in r["warnings"][:3]:
        for c in found:
            if c["rule"] == "missing_return" and not c["confirmed"]:
                c["confirmed"], c["evidence"] = True, f"Running the program: {w['message']} (line {w['line']})."
    return summary, found


def detect(check, stdin=None):
    """Candidates + execution evidence, sorted: confirmed first, then by severity and line.
    Programs that read input are run with `stdin` (or sample numbers when none is given)."""
    cands = find_candidates(check)
    run, findings = confirm_by_running(check, cands, stdin=stdin)
    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (not f["confirmed"], order[f["severity"]], f["line"] or 0))
    for k, f in enumerate(findings):
        f["id"] = f"B{k + 1}"
    return {"findings": findings, "run": run}
