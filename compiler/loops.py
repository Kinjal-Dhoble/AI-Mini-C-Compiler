"""Loop analysis on the control-flow graph, plus loop-bound and complexity estimates from the AST.

CFG part (classic compiler theory):
  * dominators         - block D dominates B if every path from the entry to B passes D
  * back edge          - an edge B -> H where H dominates B
  * natural loop       - H plus every block that can reach B without going through H
  * nesting depth      - how many natural loops contain a loop's header
  * unreachable blocks - blocks no path from the function entry can reach

AST part (for algorithmic hints): for each loop, what bounds it (a constant, a variable or
parameter, or unknown) and how the loop variable moves (by +/- c: linear; by * or / c:
logarithmic). Nesting these gives a rough Big-O per function.
"""
from compiler.parser import (ScanfNode, FunctionNode, ProgramNode, BlockNode, IfNode, WhileNode, ForNode, AssignNode,
                             DoWhileNode, SwitchNode,
                             VarDeclNode, BinOpNode, NumNode, VarNode, CallNode, ReturnNode, ExprStmtNode, PrintNode,
                             IndexNode, CastNode)

NOOP = ("LABEL ", "DECLARE ", "GOTO ", "RESULT_TYPE ", "FALLTHROUGH ")


def _dominators(nodes, entry, preds):
    dom = {n: set(nodes) for n in nodes}
    dom[entry] = {entry}
    changed = True
    while changed:
        changed = False
        for n in nodes:
            if n == entry:
                continue
            ps = [dom[p] for p in preds[n] if p in dom]
            new = ({n} | set.intersection(*ps)) if ps else {n}
            if new != dom[n]:
                dom[n], changed = new, True
    return dom


def cfg_loops(cfg, line_map):
    """Natural loops and unreachable blocks per function."""
    blocks = {b["id"]: b for b in cfg["blocks"]}
    loops, unreachable = [], []
    for fname, entry in cfg["entries"].items():
        if entry is None:
            continue
        nodes = [b["id"] for b in cfg["blocks"] if b["function"] == fname]
        succ = {n: [] for n in nodes}; preds = {n: [] for n in nodes}
        for a, b, kind in cfg["edges"]:
            if a in succ and b in succ:
                succ[a].append(b); preds[b].append(a)
        seen, stack = set(), [entry]
        while stack:
            x = stack.pop()
            if x not in seen:
                seen.add(x); stack += succ[x]
        for n in nodes:
            real = [i for i, ins in zip(blocks[n]["indices"], blocks[n]["instructions"])
                    if not ins.startswith(NOOP) and not ins.startswith("FUNC ")]
            if n not in seen and real:
                unreachable.append({"function": fname, "block": n, "line": line_map[real[0]] if line_map else None})
        reach = [n for n in nodes if n in seen]
        dom = _dominators(reach, entry, {n: [p for p in preds[n] if p in seen] for n in reach})
        by_header = {}
        for a in reach:
            for b in succ[a]:
                if b in dom.get(a, ()):                       # back edge a -> b
                    body, work = {b, a}, [a]
                    while work:
                        x = work.pop()
                        for p in preds[x]:
                            if p not in body and p in seen:
                                body.add(p); work.append(p)
                    entry_ = by_header.setdefault(b, {"function": fname, "header": b, "body": set(), "back_edges": []})
                    entry_["body"] |= body
                    entry_["back_edges"].append([a, b])
        floops = list(by_header.values())
        for L in floops:
            L["depth"] = 1 + sum(1 for o in floops if o is not L and L["header"] in o["body"] and o["body"] > L["body"])
        for L in floops:
            idxs = [i for bid in L["body"] for i in blocks[bid]["indices"]]
            lines = sorted({line_map[i] for i in idxs}) if line_map else []
            L["header_line"] = line_map[blocks[L["header"]]["start"]] if line_map else None
            L["lines"] = [lines[0], lines[-1]] if lines else []
            L["blocks"] = sorted(L["body"], key=lambda x: int(x[1:]))
            del L["body"]
        loops += sorted(floops, key=lambda L: (L["header_line"] or 0))
    return loops, unreachable


# --------------------------------------------------------------- AST bounds
def _names(e):
    if isinstance(e, VarNode): return {e.name}
    if isinstance(e, IndexNode): return {e.name}.union(*[_names(i) for i in e.indices])
    if isinstance(e, CastNode): return _names(e.expr)
    if isinstance(e, BinOpNode): return _names(e.left) | _names(e.right)
    if isinstance(e, CallNode): return set().union(*[_names(a) for a in e.args]) if e.args else set()
    return set()


def _assigned(n, out):
    if n is None: return out
    if isinstance(n, (AssignNode, VarDeclNode)):
        out.setdefault(n.name, []).append(None if getattr(n, "indices", None) else n.expr)
    if isinstance(n, ScanfNode):
        for name, *_ in n.names:
            out.setdefault(name, []).append(None)       # read from input: unknown value
    for k in ("statements", "then_branch", "else_branch", "body", "init", "update"):
        v = getattr(n, k, None)
        if isinstance(v, list):
            for x in v: _assigned(x, out)
        elif v is not None and not isinstance(v, (str, int)):
            _assigned(v, out)
    return out


def _step(var, exprs):
    """How the loop variable changes: ('linear', +c), ('log', *c) or (None, None)."""
    for e in exprs or []:
        if isinstance(e, BinOpNode) and isinstance(e.left, VarNode) and e.left.name == var and isinstance(e.right, NumNode):
            if e.op in "+-":
                return "linear", (e.right.value if e.op == "+" else -e.right.value)
            if e.op in "*/" and e.right.value > 1:
                return "log", e.right.value
    return None, None


def ast_loops(ast):
    """For every loop: line, kind, loop variable, bound kind and an estimate of its trip count."""
    out = []

    def walk(n, fn, depth, params, parent):
        if n is None: return
        if isinstance(n, FunctionNode):
            walk(n.body, n.name, 0, {p[1] for p in n.params}, None); return
        if isinstance(n, (ProgramNode, BlockNode)):
            for s in n.statements: walk(s, fn, depth, params, parent)
            return
        if isinstance(n, IfNode):
            walk(n.then_branch, fn, depth, params, parent); walk(n.else_branch, fn, depth, params, parent); return
        if isinstance(n, SwitchNode):
            for c in n.cases:
                for s in c.statements: walk(s, fn, depth, params, parent)
            return
        if isinstance(n, (WhileNode, ForNode, DoWhileNode)):
            cond = n.condition
            kind = "for" if isinstance(n, ForNode) else "do-while" if isinstance(n, DoWhileNode) else "while"
            info = {"function": fn, "line": n.line, "kind": kind,
                    "depth": depth + 1, "parent": parent, "var": None, "bound": None, "bound_kind": "unknown",
                    "growth": "unknown", "trips": None}
            body_assign = _assigned(n.body, {})
            if isinstance(n, ForNode) and n.update is not None:
                body_assign.setdefault(n.update.name, []).append(n.update.expr)
            if isinstance(cond, BinOpNode) and cond.op in ("<", "<=", ">", ">=", "!="):
                for var_side, bound_side in ((cond.left, cond.right), (cond.right, cond.left)):
                    if isinstance(var_side, VarNode) and var_side.name in body_assign:
                        info["var"] = var_side.name
                        growth, step = _step(var_side.name, body_assign[var_side.name])
                        info["growth"] = growth or "unknown"
                        if isinstance(bound_side, NumNode):
                            info["bound_kind"], info["bound"] = "constant", bound_side.value
                            init = None
                            if isinstance(n, ForNode) and isinstance(getattr(n.init, "expr", None), NumNode):
                                init = n.init.expr.value
                            if growth == "linear" and step and init is not None:
                                info["trips"] = max(0, (bound_side.value - init + (1 if cond.op in ("<=", ">=") else 0)) // abs(step))
                        else:
                            names = _names(bound_side)
                            info["bound_kind"] = "variable" if names else "expression"
                            info["bound"] = " ".join(sorted(names)) or None
                            info["bound_is_param"] = bool(names & params)
                        break
            elif isinstance(cond, NumNode):
                info["bound_kind"] = "infinite" if cond.value else "never"
            out.append(info)
            walk(n.body, fn, depth + 1, params, n.line)
            return

    walk(ast, "<globals>", 0, set(), None)
    return out


def _kind(L):
    """const: fixed number of trips; lin / log: grows with a variable bound; data: depends on values."""
    if L["bound_kind"] == "constant" and L["growth"] == "linear":
        return "const"
    if L["bound_kind"] in ("variable", "expression"):
        return "log" if L["growth"] == "log" else "lin" if L["growth"] == "linear" else "data"
    if L["bound_kind"] == "never":
        return "const"
    return "data"


def complexity(ast_loop_list, call_graph=None):
    """Rough Big-O per function from loop nesting, bound kinds and growth.
    Loops whose trip count depends on the data (e.g. while (b != 0) in gcd) are reported as
    data-dependent instead of being guessed."""
    res = {}
    by_fn = {}
    for L in ast_loop_list:
        by_fn.setdefault(L["function"], []).append(L)
    recursive = set((call_graph or {}).get("recursive", []))
    for fn, ls in by_fn.items():
        by_line = {L["line"]: L for L in ls}
        best, best_path = (0, 0, 0), []
        for L in ls:
            n_lin = n_log = n_data = 0; path = []; cur = L
            while cur:
                k = _kind(cur)
                n_lin += k == "lin"; n_log += k == "log"; n_data += k == "data"
                path.append(cur["line"]); cur = by_line.get(cur["parent"])
            if (n_lin, n_log, n_data) > best:
                best, best_path = (n_lin, n_log, n_data), path
        n_lin, n_log, n_data = best
        parts = ([f"n^{n_lin}" if n_lin > 1 else "n"] if n_lin else []) + (["log n" if n_log == 1 else f"(log n)^{n_log}"] if n_log else [])
        big = "O(" + (" ".join(parts) if parts else "1") + ")"
        data_lines = sorted(L["line"] for L in ls if _kind(L) == "data")
        if data_lines and not parts:
            big = "data-dependent"
        res[fn] = {"big_o": big, "loop_lines": sorted(best_path), "max_depth": max(L["depth"] for L in ls),
                   "data_dependent_loops": data_lines}
    for f in recursive:
        c = res.setdefault(f, {"big_o": "O(1)", "loop_lines": [], "max_depth": 0, "data_dependent_loops": []})
        c["recursive"] = True
        if c["big_o"] == "O(1)":
            c["big_o"] = "recursive"
    return res
