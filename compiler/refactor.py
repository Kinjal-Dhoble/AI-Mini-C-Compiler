"""Refactoring by AST pattern matching (deterministic, then verified by running the program).

Patterns:
  while_to_for          i = start; while (cond(i)) { ...; i = step; }  ->  for (i = start; cond; i = step) { ... }
  merge_nested_if       if (a) { if (b) { S } }                       ->  if (a && b) { S }
  return_condition      if (c) { return 1; } else { return 0; }       ->  return c;
  invert_empty_if       if (c) { } else { S }                         ->  if (not c) { S }
  constant_condition    if (1) S / if (0) S else T / while (0) S      ->  the branch that always runs
  identical_branches    if (c) S else S                               ->  S
  redundant_comparison  if ((a < b) == 1)                             ->  if (a < b)

Only the source span of the matched statements is replaced, so comments elsewhere survive.
Every refactoring is checked: the new program must compile, and (when main takes no input)
print the same output and return the same value on the MiniC VM.
"""
import re
from compiler.parser import *
from compiler.printer import expr as pexpr, stmt as pstmt, negate, is_boolean


# ------------------------------------------------------------------ helpers
def _offsets(source):
    starts, pos = [0], 0
    for line in source.split("\n"):
        pos += len(line) + 1
        starts.append(pos)
    return starts


def _off(starts, line, col):
    return starts[line - 1] + col - 1


def _uses(n, name):
    if n is None: return False
    if isinstance(n, ScanfNode): return any(x[0] == name for x in n.names)
    if isinstance(n, (VarNode, IndexNode)) and n.name == name: return True
    if isinstance(n, (AssignNode, VarDeclNode)) and n.name == name: return True
    if isinstance(n, list): return any(_uses(x, name) for x in n)
    if hasattr(n, "__dataclass_fields__"):
        return any(_uses(getattr(n, k), name) for k in n.__dataclass_fields__
                   if isinstance(getattr(n, k), (list,)) or hasattr(getattr(n, k), "__dataclass_fields__"))
    return False


def _assigns(n, name):
    if n is None: return False
    if isinstance(n, ScanfNode): return any(x[0] == name for x in n.names)
    if isinstance(n, (AssignNode, VarDeclNode)) and n.name == name: return True
    if isinstance(n, list): return any(_assigns(x, name) for x in n)
    if hasattr(n, "__dataclass_fields__"):
        return any(_assigns(getattr(n, k), name) for k in n.__dataclass_fields__
                   if isinstance(getattr(n, k), list) or hasattr(getattr(n, k), "__dataclass_fields__"))
    return False


def _risky(e):
    """Would evaluating e when it previously was not evaluated be unsafe? (calls, division)"""
    if isinstance(e, (CallNode, IndexNode)): return True            # a call, or an array access that could be out of bounds
    if isinstance(e, BinOpNode): return e.op in ("/", "%") or _risky(e.left) or _risky(e.right)
    if isinstance(e, CastNode): return _risky(e.expr)
    return False


def _unwrap(b):
    return b.statements if isinstance(b, BlockNode) else [b]


def _ret_const(b):
    s = _unwrap(b) if b is not None else []
    if len(s) == 1 and isinstance(s[0], ReturnNode) and isinstance(s[0].expr, NumNode) and s[0].expr.value in (0, 1):
        return s[0].expr.value
    return None


def _cond_span(source, starts, node):
    """Offsets of the text between the parentheses after if/while."""
    i = source.index("(", _off(starts, node.line, node.column))
    depth, j = 0, i
    while j < len(source):
        if source[j] == "(": depth += 1
        elif source[j] == ")":
            depth -= 1
            if depth == 0: return i + 1, j
        j += 1
    return None


TITLES = {
    "while_to_for": "Turn the while loop into a for loop",
    "merge_nested_if": "Merge the nested if statements",
    "return_condition": "Return the condition directly",
    "invert_empty_if": "Remove the empty if-branch",
    "constant_condition": "Remove a decision that never changes",
    "identical_branches": "Remove an if whose branches are identical",
    "redundant_comparison": "Simplify the comparison",
}


# --------------------------------------------------------------- matching
class Refactorer:
    def __init__(self, source, ast):
        self.source, self.ast = source, ast
        self.starts = _offsets(source)
        m = re.search(r"^([ \t]+)\S", source, re.M)
        self.unit = m.group(1) if m and m.group(1).startswith("\t") else "    "
        self.items = []

    def run(self):
        self._walk_list(self.ast.statements)
        for k, it in enumerate(sorted(self.items, key=lambda x: (x["line"], x["start"]))):
            it["id"] = f"R{k + 1}"
        return sorted(self.items, key=lambda x: (x["line"], x["start"]))

    # spans ---------------------------------------------------------------
    def _span(self, first, last):
        return _off(self.starts, first.line, first.column), _off(self.starts, last.end_line, last.end_column) + 1

    def _indent(self, node):
        line = self.source.split("\n")[node.line - 1]
        return line[:len(line) - len(line.lstrip())]

    def _text(self, node):
        a, b = self._span(node, node)
        return self.source[a:b]

    def _dedent(self, text):
        """Remove one indentation level from every line after the first."""
        lines = text.split("\n")
        return "\n".join([lines[0]] + [l[len(self.unit):] if l.startswith(self.unit) else l.lstrip(" \t") if not l.strip() else l for l in lines[1:]])

    def _block_inner(self, block):
        """The statements inside { } as text, dedented one level, without the braces."""
        t = self._text(block)
        inner = t[t.index("{") + 1:t.rindex("}")].strip("\n")
        lines = [l for l in inner.split("\n")]
        while lines and not lines[-1].strip(): lines.pop()
        while lines and not lines[0].strip(): lines.pop(0)
        out = [l[len(self.unit):] if l.startswith(self.unit) else l for l in lines]
        return "\n".join(out).lstrip()

    def _render(self, nodes, base):
        lines = [l for n in nodes for l in pstmt(n, self.unit, 0)]
        return ("\n" + base).join(lines)

    def _add(self, kind, first, last, new_nodes, explanation=None, start=None, end=None, text=None):
        s, e = self._span(first, last) if start is None else (start, end)
        if text is None:
            text = self._render(new_nodes, self._indent(first)) if new_nodes else ""
        if text == "":                                          # deleting: take whole lines if possible
            ls = self.source.rfind("\n", 0, s) + 1
            le = self.source.find("\n", e)
            le = len(self.source) if le == -1 else le
            if self.source[ls:s].strip() == "" and self.source[e:le].strip() == "":
                s, e = ls, min(le + 1, len(self.source))
        before = self.source[s:e]
        self.items.append({"kind": kind, "title": TITLES[kind], "line": first.line,
                           "end_line": last.end_line or first.line, "start": s, "end": e,
                           "before": before.strip("\n"), "after": text, "explanation": explanation,
                           "comments_lost": len(re.findall(r"//|/\*", before)) > len(re.findall(r"//|/\*", text))})

    # walking ---------------------------------------------------------------
    def _walk_list(self, lst):
        for k, s in enumerate(lst):
            prev = lst[k - 1] if k else None
            nxt = lst[k + 1] if k + 1 < len(lst) else None
            self._match(s, prev, nxt, lst[k + 1:])
            for child in self._children(s):
                if isinstance(child, BlockNode):
                    self._walk_list(child.statements)
                elif child is not None:
                    self._walk_list([child])

    @staticmethod
    def _children(s):
        if isinstance(s, FunctionNode): return [s.body]
        if isinstance(s, IfNode): return [s.then_branch, s.else_branch]
        if isinstance(s, (WhileNode, ForNode, DoWhileNode)): return [s.body]
        if isinstance(s, BlockNode): return [s]
        return []

    def _match(self, s, prev, nxt, rest):
        if isinstance(s, WhileNode):
            self._while_to_for(prev, s, rest)
            if isinstance(s.condition, NumNode) and s.condition.value == 0:
                self._add("constant_condition", s, s, [], f"while (0) on line {s.line} never runs its body, so the whole loop can go.")
        if isinstance(s, (IfNode, WhileNode)):
            self._redundant_comparison(s)
        if not isinstance(s, IfNode):
            return
        c, yes, no = s.condition, s.then_branch, s.else_branch
        # constant condition
        if isinstance(c, NumNode):
            keep = yes if c.value else no
            if keep is None:
                text = ""
            elif isinstance(keep, BlockNode) and not any(isinstance(x, VarDeclNode) for x in keep.statements):
                text = self._block_inner(keep)
            else:
                text = self._dedent(self._text(keep)) if isinstance(keep, BlockNode) else self._text(keep)
            self._add("constant_condition", s, s, None, text=text, explanation=
                      f"The condition on line {s.line} is always {'true' if c.value else 'false'}, so the "
                      f"{'if' if c.value else 'else'} part {'always' if keep is not None else 'never'} runs and the test itself does nothing.")
            return
        # identical branches
        if no is not None and pstmt(yes) == pstmt(no) and not _risky(c):
            text = (self._block_inner(yes) if isinstance(yes, BlockNode) and not any(isinstance(x, VarDeclNode) for x in yes.statements)
                    else self._text(yes))
            self._add("identical_branches", s, s, None, text=text, explanation=
                      f"Both branches of the if on line {s.line} do exactly the same thing, so the decision makes no difference.")
            return
        # return condition
        ry, rn = _ret_const(yes), _ret_const(no)
        tail = None
        if no is None and ry is not None and isinstance(nxt, ReturnNode) and isinstance(nxt.expr, NumNode) and nxt.expr.value in (0, 1):
            rn, tail = nxt.expr.value, nxt
        if ry is not None and rn is not None and ry != rn:
            val = c if is_boolean(c) else BinOpNode(left=c, op='!=', right=NumNode(value=0))
            if ry == 0:
                val = negate(c)
            self._add("return_condition", s, tail or s, [ReturnNode(expr=val)],
                      f"The if on line {s.line} only returns 1 when the condition is {'true' if ry else 'false'} and 0 otherwise. "
                      "A comparison already produces 1 or 0, so you can return it directly.")
            return
        # empty then-branch
        if isinstance(yes, BlockNode) and not yes.statements and no is not None:
            self._add("invert_empty_if", s, s, None, text=f"if ({pexpr(negate(c))}) " + self._text(no), explanation=
                      f"The if-branch on line {s.line} is empty and all the work is in else. Flipping the condition removes the empty branch.")
            return
        # nested ifs
        if no is None:
            inner = yes.statements[0] if isinstance(yes, BlockNode) and len(yes.statements) == 1 else (yes if isinstance(yes, IfNode) else None)
            if isinstance(inner, IfNode) and inner.else_branch is None:
                cond = pexpr(BinOpNode(left=c, op='&&', right=inner.condition))
                body = self._dedent(self._text(inner.then_branch)) if isinstance(yes, BlockNode) else self._text(inner.then_branch)
                self._add("merge_nested_if", s, s, None, text=f"if ({cond}) {body}", explanation=
                          f"The if on line {inner.line} sits alone inside the if on line {s.line}. Both conditions must be true, "
                          "so one if with && says the same thing with one less level of nesting.")

    def _while_to_for(self, prev, w, rest):
        if not isinstance(prev, (VarDeclNode, AssignNode)) or prev.expr is None:
            return
        if getattr(prev, "dims", None) or getattr(prev, "indices", None):
            return                                   # arrays and array elements are not loop counters
        var = prev.name
        body = w.body
        if not isinstance(body, BlockNode) or not body.statements:
            return
        last = body.statements[-1]
        if not (isinstance(last, AssignNode) and last.name == var and not last.indices and _uses(w.condition, var)):
            return
        if _assigns(body.statements[:-1], var):
            return
        used_later = any(_uses(x, var) for x in rest)
        if isinstance(prev, VarDeclNode) and used_later:
            head, init = f"{prev.var_type} {var};\n{self._indent(prev)}", f"{var} = {pexpr(prev.expr)}"
        elif isinstance(prev, VarDeclNode):
            head, init = "", f"{prev.var_type} {var} = {pexpr(prev.expr)}"
        else:
            head, init = "", f"{var} = {pexpr(prev.expr)}"
        header = f"{head}for ({init}; {pexpr(w.condition)}; {var} = {pexpr(last.expr)})"
        # keep the original body text (and its comments), minus the line that updates the counter
        _, cond_end = _cond_span(self.source, self.starts, w)
        w_end = self._span(w, w)[1]
        ls, le = self._span(last, last)
        line_start = self.source.rfind("\n", 0, ls) + 1
        line_end = self.source.find("\n", le)
        if self.source[line_start:ls].strip() == "" and self.source[le:line_end].strip() == "":
            ls, le = line_start, line_end + 1
        body_text = self.source[cond_end + 1:ls] + self.source[le:w_end]
        text = header + body_text
        self._add("while_to_for", prev, w, None, text=text, explanation=
                  f"The loop counter {var} is set on line {prev.line}, tested on line {w.line} and changed on line {last.line}. "
                  "A for loop puts the start, the test and the step on one line, so it is easier to see how many times the loop runs.")

    def _redundant_comparison(self, s):
        c = s.condition
        if not (isinstance(c, BinOpNode) and c.op in ("==", "!=") and isinstance(c.right, NumNode) and c.right.value in (0, 1) and is_boolean(c.left)):
            return
        keep = (c.op == "==" and c.right.value == 1) or (c.op == "!=" and c.right.value == 0)
        new = c.left if keep else negate(c.left)
        span = _cond_span(self.source, self.starts, s)
        if not span:
            return
        a, b = span
        self.items.append({"kind": "redundant_comparison", "title": TITLES["redundant_comparison"], "line": s.line,
                           "end_line": s.line, "start": a, "end": b, "before": self.source[a:b], "after": pexpr(new),
                           "explanation": f"`{pexpr(c.left)}` is already 1 when true and 0 when false, so comparing it with "
                                          f"{c.right.value} again is not needed.", "comments_lost": False})


def _display(source, start, text):
    """Snippet for display: restore the first line's indentation, then remove the common indent."""
    import textwrap
    line_start = source.rfind("\n", 0, start) + 1
    lead = source[line_start:start]
    lead = lead if not lead.strip() else ""
    return textwrap.dedent(lead + text).strip("\n")


# ---------------------------------------------------------- public API
def apply(source, item):
    return source[:item["start"]] + item["after"] + source[item["end"]:]


def apply_all(source, items):
    """Apply every non-overlapping refactoring, bottom-up so offsets stay valid."""
    chosen, last_start = [], None
    for it in sorted(items, key=lambda x: x["start"], reverse=True):
        if last_start is None or it["end"] <= last_start:
            chosen.append(it); last_start = it["start"]
    out = source
    for it in chosen:
        out = apply(out, it)
    return out, [it["id"] for it in chosen]


def find_refactorings(source, check=None):
    """All refactorings for a program that compiles, each verified by compiling and running."""
    from ai_assistant.context_builder import check_source
    from ai_assistant.ir_interpreter import run_program
    check = check or check_source(source)
    if not check["ok"]:
        return {"items": [], "error": "The program must compile before it can be refactored."}
    items = Refactorer(source, check["ast"]).run()
    for it in items:
        if it["kind"] != "redundant_comparison":
            it["before_display"] = _display(source, it["start"], source[it["start"]:it["end"]])
            it["after_display"] = _display(source, it["start"], it["after"])
        else:
            it["before_display"], it["after_display"] = it["before"], it["after"]
    can_run = "main" in check["call_graph"]["functions"] and not any(
        isinstance(f, FunctionNode) and f.name == "main" and f.params for f in check["ast"].statements)

    def outcome(ir):
        r = run_program(ir, max_steps=300_000)
        return r["return_value"], r["stdout"], r["error_kind"]
    base = outcome(check["ir"]) if can_run else None
    for it in items:
        new_src = apply(source, it)
        it["after_code"] = new_src
        c = check_source(new_src)
        if not c["ok"]:
            it["verified"], it["notes"] = False, ["the refactored program does not compile: " + "; ".join(e["message"] for e in c["errors"][:2])]
            continue
        notes = ["compiles", f"IR {len(check['ir'])} -> {len(c['ir'])} instructions"]
        if base is not None:
            new = outcome(c["ir"])
            same = new == base
            notes.append("same output and return value when run" if same else f"behaviour changed: {base} -> {new}")
            it["verified"] = same
        else:
            it["verified"] = True
            notes.append("not executed (main needs input)")
        it["notes"] = notes
    good = [it for it in items if it.get("verified")]
    result = {"items": items}
    if len(good) > 1:
        code, ids = apply_all(source, good)
        c = check_source(code)
        ok = c["ok"] and (base is None or outcome(c["ir"]) == base)
        result.update({"all_code": code, "all_ids": ids, "all_verified": ok})
    return result
