"""The whole front and middle end in one call, shared by the CLI, the IDE and the AI assistant.

analyze(source) runs every phase it can and returns plain data:
  success, stage ('lex' | 'parse' | 'semantic' | 'done'), tokens, ast,
  errors, warnings, static          (lists of diagnostic dicts with category/message/line/column)
  ir, line_map, cfg, call_graph, loops (CFG natural loops), ast_loops, complexity, unreachable
Syntax errors never hide semantic errors when the parser could repair the tree in place.
"""
from compiler.lexer import Lexer, LexErrors
from compiler.parser import Parser, ParseErrors, ParseError, MAX_NESTING
from compiler.semantic import SemanticAnalyzer
from compiler.static_analysis import StaticAnalyzer
from compiler.code_gen import CodeGenerator
from compiler.cfg import ControlFlowGraph
from compiler.call_graph import CallGraph
from compiler.loops import cfg_loops, ast_loops, complexity

ERROR_CATEGORIES = ("Semantic Error", "Type Error", "Syntax Error", "Lexical Error")


def analyze(source, frontend="hand"):
    """frontend: "hand" (recursive descent, compiler/parser.py) or "yacc" (LALR, compiler/yacc_frontend.py)."""
    res = {"success": False, "stage": "lex", "tokens": [], "ast": None, "errors": [], "warnings": [],
           "static": [], "ir": [], "line_map": [], "cfg": None, "call_graph": None, "loops": [],
           "ast_loops": [], "complexity": {}, "unreachable": []}
    lexer = Lexer(source)
    try:
        tokens = lexer.tokenize()
    except LexErrors as e:
        res["errors"] = e.errors
        res["warnings"] = list(lexer.notes)
        res["tokens"] = [t for t in e.tokens if t.kind != "EOF"]
        return res
    res["tokens"] = [t for t in tokens if t.kind != "EOF"]
    res["warnings"] = list(lexer.notes)
    res["stage"] = "parse"
    res["frontend"] = frontend
    if frontend == "yacc":
        from compiler import yacc_frontend
        y = yacc_frontend.parse(source)
        if y["errors"]:
            res["errors"] = y["errors"]
            return res
        ast = y["ast"]
        return _after_parse(res, ast)
    try:
        ast = Parser(tokens).parse()
    except ParseErrors as e:
        res["errors"] = list(e.errors)
        if not e.complete:
            return res
        ast = e.ast                          # repaired in place: keep going to find more errors
    except ParseError as e:                  # defensive: should be collected above
        res["errors"] = [{"category": "Syntax Error", "message": str(e), "line": e.line, "column": e.column}]
        return res
    return _after_parse(res, ast)


def nesting_depth(ast):
    """Deepest chain of nested AST nodes, measured without recursion (works for any front end)."""
    deepest, stack = 0, [(ast, 1)]
    while stack:
        node, d = stack.pop()
        deepest = max(deepest, d)
        if d > 10 * MAX_NESTING:
            break
        for v in vars(node).values() if hasattr(node, "__dict__") else ():
            for x in (v if isinstance(v, (list, tuple)) else (v,)):
                for y in (x if isinstance(x, (list, tuple)) else (x,)):
                    if hasattr(y, "__dict__") and type(y).__module__.startswith("compiler"):
                        stack.append((y, d + 1))
    return deepest


def _after_parse(res, ast):
    if nesting_depth(ast) > 3 * MAX_NESTING:
        res["ast"] = None
        res["errors"] = res["errors"] + [{"category": "Syntax Error", "line": 1, "column": 1,
                         "message": f"The program is nested too deeply (more than {MAX_NESTING} levels of blocks or brackets)",
                         "hint": "Split the code into functions or use fewer nested blocks and brackets."}]
        return res
    try:
        return _after_parse_inner(res, ast)
    except RecursionError:                   # last safety net: never crash on extreme input
        res["success"] = False
        res["errors"] = res["errors"] + [{"category": "Syntax Error", "line": 1, "column": 1,
                         "message": "The program is too deeply nested for the compiler to analyse",
                         "hint": "Split the code into functions or use fewer nested blocks and brackets."}]
        return res


def _after_parse_inner(res, ast):
    res["ast"] = ast
    syntax_failed = bool(res["errors"])
    if not syntax_failed:
        res["stage"] = "semantic"
    for d in SemanticAnalyzer().analyze(ast):
        (res["errors"] if d.get("category") in ERROR_CATEGORIES else res["warnings"]).append(d)
    res["call_graph"] = CallGraph().build(ast)
    if res["errors"]:
        return res
    res["stage"] = "done"
    cg = CodeGenerator()
    res["ir"] = cg.generate(ast)
    res["line_map"] = cg.line_map
    res["cfg"] = ControlFlowGraph().build(res["ir"])
    res["loops"], res["unreachable"] = cfg_loops(res["cfg"], res["line_map"])
    res["ast_loops"] = ast_loops(ast)
    res["complexity"] = complexity(res["ast_loops"], res["call_graph"])
    res["static"] = StaticAnalyzer().analyze(ast, res["call_graph"])
    seen = {d["line"] for d in res["static"] if d.get("rule") == "unreachable"}
    for u in res["unreachable"]:
        if u["line"] and u["line"] not in seen and u["function"] != "<globals>":
            seen.add(u["line"])
            res["static"].append({"category": "Warning", "rule": "unreachable", "line": u["line"], "column": 1,
                                  "message": f"Unreachable code in {u['function']}(): no path through the program reaches this line"})
    res["success"] = True
    return res
