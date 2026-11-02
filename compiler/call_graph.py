"""Call graph: which function calls which, recursion, and functions nothing calls."""
from compiler.parser import FunctionNode, ProgramNode, CallNode


def _calls(node, out):
    if isinstance(node, CallNode):
        out.append((node.name, node.line))
    if isinstance(node, list):
        for x in node:
            _calls(x, out)
    elif hasattr(node, "__dataclass_fields__"):
        for k in node.__dataclass_fields__:
            v = getattr(node, k)
            if isinstance(v, (list,)) or hasattr(v, "__dataclass_fields__"):
                _calls(v, out)
    return out


class CallGraph:
    def build(self, program):
        funcs = [n for n in program.statements if isinstance(n, FunctionNode)] if isinstance(program, ProgramNode) else []
        names = [f.name for f in funcs]
        edges = []
        for f in funcs:
            for callee, line in _calls(f.body, []):
                edges.append((f.name, callee, line))
        succ = {n: sorted({c for a, c, _ in edges if a == n and c in names}) for n in names}
        # recursion: functions on a cycle (Tarjan's strongly connected components)
        index, low, stack, on, sccs, counter = {}, {}, [], set(), [], [0]

        def strong(v):
            index[v] = low[v] = counter[0]; counter[0] += 1; stack.append(v); on.add(v)
            for w in succ[v]:
                if w not in index:
                    strong(w); low[v] = min(low[v], low[w])
                elif w in on:
                    low[v] = min(low[v], index[w])
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop(); on.discard(w); comp.append(w)
                    if w == v: break
                sccs.append(comp)
        for v in names:
            if v not in index:
                strong(v)
        recursive = sorted({v for c in sccs for v in c if len(c) > 1 or v in succ[v]})
        reach, work = set(), ["main"] if "main" in names else []
        while work:
            v = work.pop()
            if v not in reach:
                reach.add(v); work += succ[v]
        return {"functions": names, "edges": edges, "calls": succ, "recursive": recursive,
                "unreachable": [n for n in names if n not in reach and n != "main"] if "main" in names else [],
                "line": {f.name: f.line for f in funcs}}
