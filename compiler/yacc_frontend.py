"""Lex & yacc front-end (PLY: Python Lex-Yacc), the bottom-up twin of compiler/parser.py.

The grammar below is the same as grammar/minic.y (bison) and builds exactly the same AST
classes as the hand-written recursive-descent parser, so the rest of the compiler can use
either front-end. PLY builds an LALR(1) parse table from the docstrings of the p_ functions,
just like yacc builds one from a .y file.

    parse(source, trace=True) -> {"ast", "errors", "tokens", "trace", "accepted"}
    grammar_info()            -> productions, number of LALR states, conflicts
    same_ast(a, b)            -> None if both trees are identical (ignoring positions), else where they differ

The trace is the shift/reduce log a textbook shows: for each step the parser state, the
symbol stack, the lookahead token and the action taken.
"""
import re
from compiler.vendor.ply import lex, yacc
from compiler.parser import (BreakNode, ContinueNode, DoWhileNode, CaseNode, SwitchNode, make_assign,
                             ProgramNode, VarDeclNode, AssignNode, BinOpNode, NumNode, VarNode, CallNode,
                             BlockNode, IfNode, WhileNode, ForNode, ReturnNode, ExprStmtNode, PrintNode,
                             PrintfNode, ScanfNode, FunctionNode, FloatNode, CharNode, StringNode, IndexNode,
                             CastNode, SizeofNode, InitListNode)
from compiler.typesys import char_value

READABLE = {"ADDEQ": "'+='", "SUBEQ": "'-='", "MULEQ": "'*='", "DIVEQ": "'/='", "MODEQ": "'%='","EQ": "'=='", "NE": "'!='", "LE": "'<='", "GE": "'>='", "AND": "'&&'", "OR": "'||'",
            "INC": "'++'", "DEC": "'--'", "IDENT": "a name", "NUMBER": "a number", "STRING": "a string",
            "FLOATNUM": "a decimal number", "CHARLIT": "a character",
            "$end": "end of file"}


class _Collect:
    """PLY logger that keeps messages instead of printing them."""
    def __init__(self):
        self.lines = []

    def _add(self, kind, msg, *args):
        self.lines.append((kind, msg % args if args else msg))
    debug = info = critical = lambda self, msg, *a: self._add("info", msg, *a)
    warning = lambda self, msg, *a: self._add("warning", msg, *a)
    error = lambda self, msg, *a: self._add("error", msg, *a)


class _Trace:
    """Turns PLY's parse debug log into rows: state, stack, lookahead, action."""
    def __init__(self, limit):
        self.rows, self.limit, self.state, self.stack, self.truncated = [], limit, None, "", False

    def debug(self, msg, *args):
        text = msg % args if args else msg
        if text.startswith("State  :"):
            self.state = text.split(":", 1)[1].strip()
        elif text.startswith("Stack  :"):
            self.stack = text.split(":", 1)[1].strip()
        elif text.startswith(("Action :", "Error  :", "Done   :")):
            if len(self.rows) >= self.limit:
                self.truncated = True
                return
            stack, _, look = self.stack.rpartition(" . ") if " . " in self.stack else ("", "", self.stack.lstrip(". "))
            action = text.split(":", 1)[1].strip()
            m = re.match(r"Reduce rule \[(.*)\] with (.*) and goto state (\d+)", action)
            if m:
                action = f"reduce {m.group(1)}  (goto {m.group(3)})"
            m = re.match(r"Shift and goto state (\d+)", action)
            if m:
                action = f"shift, goto {m.group(1)}"
            kind = "reduce" if action.startswith("reduce") else "shift" if action.startswith("shift") else "accept" if text.startswith("Done") else "error"
            if kind == "accept":
                action = "accept"
            self.rows.append({"state": self.state, "stack": stack.strip(), "lookahead": _token_text(look.strip()), "action": action, "kind": kind})
    info = debug                     # PLY logs reductions and results at info level
    warning = error = critical = lambda self, *a: None


def _token_text(t):
    """LexToken(TYPE,VALUE,line,pos) -> readable lookahead."""
    if t in ("None", ""):
        return "(not needed)"
    if not t.startswith("LexToken("):
        return t.replace("$end", "end of file")
    body = t[len("LexToken("):-1].rsplit(",", 2)[0]
    kind, _, value = body.partition(",")
    value = value.strip("'")
    return f"{kind.lower()} {value}" if kind in ("IDENT", "NUMBER", "STRING", "FLOATNUM", "CHARLIT") else value


class MiniCGrammar:
    # ------------------------------------------------------------------ lex
    reserved = {"int": "INT", "void": "VOID", "if": "IF", "else": "ELSE", "while": "WHILE", "for": "FOR",
                "return": "RETURN", "print": "PRINT", "printf": "PRINTF", "scanf": "SCANF",
                "char": "CHAR", "short": "SHORT", "long": "LONG", "float": "FLOAT", "double": "DOUBLE", "sizeof": "SIZEOF",
                "break": "BREAK", "continue": "CONTINUE", "do": "DO", "switch": "SWITCH", "case": "CASE",
                "default": "DEFAULT", "const": "CONST"}
    tokens = ["NUMBER", "FLOATNUM", "CHARLIT", "IDENT", "STRING", "EQ", "NE", "LE", "GE", "AND", "OR", "INC", "DEC",
              "ADDEQ", "SUBEQ", "MULEQ", "DIVEQ", "MODEQ"] + sorted(set(reserved.values()))
    literals = "+-*/%<>=(){};,&[]!:"
    t_ignore = " \t\r"
    t_EQ, t_NE, t_LE, t_GE = r"==", r"!=", r"<=", r">="
    t_AND, t_OR, t_INC, t_DEC = r"&&", r"\|\|", r"\+\+", r"--"
    t_ADDEQ, t_SUBEQ, t_MULEQ, t_DIVEQ, t_MODEQ = r"\+=", r"-=", r"\*=", r"/=", r"%="

    def t_ignore_COMMENT(self, t):
        r"/\*(.|\n)*?\*/|//[^\n]*"
        t.lexer.lineno += t.value.count("\n")

    def t_ignore_INCLUDE(self, t):
        r"\#[ \t]*include[^\n]*"

    def t_STRING(self, t):
        r'"([^"\\\n]|\\.)*"'
        return t

    def t_CHARLIT(self, t):
        r"'([^'\\\n]|\\.)*'"
        value, problem = char_value(t.value)
        if problem:
            line, col = t.lexer.lineno, _col(t.lexer.lexdata, t.lexpos)
            self.errors.append({"category": "Lexical Error", "line": line, "column": col, "found": t.value,
                                "message": f"Bad character literal {t.value} at line {line}, column {col}: {problem}"})
            return None
        t.value = (value, t.value)
        return t

    def t_FLOATNUM(self, t):
        r"(\d+\.\d*|\.\d+)([eE][+-]?\d+)?[fF]?|\d+[eE][+-]?\d+[fF]?"
        return t

    def t_NUMBER(self, t):
        r"\d+"
        t.value = int(t.value)
        return t

    def t_IDENT(self, t):
        r"[A-Za-z_][A-Za-z0-9_]*"
        t.type = self.reserved.get(t.value, "IDENT")
        return t

    def t_newline(self, t):
        r"\n+"
        t.lexer.lineno += len(t.value)

    def t_error(self, t):
        line, col = t.lexer.lineno, _col(t.lexer.lexdata, t.lexpos)
        self.errors.append({"category": "Lexical Error", "line": line, "column": col, "found": t.value[0],
                            "message": f"Unexpected character {t.value[0]!r} at line {line}, column {col}"})
        t.lexer.skip(1)

    # ---------------------------------------------------------------- yacc
    precedence = (
        ("left", "OR"), ("left", "AND"), ("left", "EQ", "NE"), ("left", "<", ">", "LE", "GE"),
        ("left", "+", "-"), ("left", "*", "/", "%"), ("right", "UMINUS"),
        ("nonassoc", "IFX"), ("nonassoc", "ELSE"),
    )
    start = "program"

    def _pos(self, p, i):
        return {"line": p.lineno(i), "column": _col(p.lexer.lexdata, p.lexpos(i))}

    def p_program(self, p):
        "program : stmts"
        p[0] = ProgramNode(line=1, column=1, statements=p[1])

    def p_stmts_empty(self, p):
        "stmts :"
        p[0] = []

    def p_stmts_more(self, p):
        "stmts : stmts stmt"
        p[0] = p[1] + (p[2] if isinstance(p[2], list) else [p[2]] if p[2] is not None else [])

    @staticmethod
    def _one(x):
        """A declaration list used as a body (if (c) int a, b;) becomes a block, as in the hand-written parser."""
        if isinstance(x, list):
            return BlockNode(line=x[0].line, column=x[0].column, statements=x)
        return x

    def p_stmt_simple(self, p):
        """stmt : decl
                | function
                | block"""
        p[0] = p[1]

    def p_stmt_assign(self, p):
        "stmt : assignment ';'"
        p[0] = p[1]

    def p_stmt_break(self, p):
        """stmt : BREAK ';'
                | CONTINUE ';'"""
        p[0] = (BreakNode if p[1] == "break" else ContinueNode)(**self._pos(p, 1))

    def p_stmt_do(self, p):
        "stmt : DO stmt WHILE '(' expr ')' ';'"
        p[0] = DoWhileNode(**self._pos(p, 1), body=self._one(p[2]), condition=p[5])

    def p_stmt_switch(self, p):
        "stmt : SWITCH '(' expr ')' '{' switch_items '}'"
        cases = []
        for kind, item in p[6]:
            if kind == "label":
                cases.append(item)
            elif not cases:
                self.errors.append({"category": "Syntax Error", "line": item.line, "column": item.column, "found": "",
                                    "message": f"Statement before the first case at line {item.line}; expected 'case' or 'default'",
                                    "expected": "'case'", "recovery": "the statement was ignored"})
            else:
                cases[-1].statements.extend(item if isinstance(item, list) else [item])
        p[0] = SwitchNode(**self._pos(p, 1), expr=p[3], cases=cases)

    def p_switch_items(self, p):
        """switch_items :
                        | switch_items CASE expr ':'
                        | switch_items DEFAULT ':'
                        | switch_items stmt"""
        if len(p) == 1:
            p[0] = []
        elif len(p) == 5:
            p[0] = p[1] + [("label", CaseNode(**self._pos(p, 2), value=p[3]))]
        elif len(p) == 4:
            p[0] = p[1] + [("label", CaseNode(**self._pos(p, 2), value=None))]
        else:
            p[0] = p[1] + ([("stmt", p[2])] if p[2] is not None else [])

    def p_stmt_expr(self, p):
        "stmt : expr ';'"
        p[0] = ExprStmtNode(line=p[1].line, column=p[1].column, expr=p[1])

    def p_stmt_empty(self, p):
        "stmt : ';'"
        p[0] = BlockNode(**self._pos(p, 1), statements=[])

    def p_stmt_if(self, p):
        "stmt : IF '(' expr ')' stmt %prec IFX"
        p[0] = IfNode(**self._pos(p, 1), condition=p[3], then_branch=self._one(p[5]))

    def p_stmt_if_else(self, p):
        "stmt : IF '(' expr ')' stmt ELSE stmt"
        p[0] = IfNode(**self._pos(p, 1), condition=p[3], then_branch=self._one(p[5]), else_branch=self._one(p[7]))

    def p_stmt_while(self, p):
        "stmt : WHILE '(' expr ')' stmt"
        p[0] = WhileNode(**self._pos(p, 1), condition=p[3], body=self._one(p[5]))

    def p_stmt_for(self, p):
        "stmt : FOR '(' for_init ';' opt_expr ';' for_update ')' stmt"
        p[0] = ForNode(**self._pos(p, 1), init=p[3], condition=p[5], update=p[7], body=self._one(p[9]))

    def p_stmt_return(self, p):
        """stmt : RETURN ';'
                | RETURN expr ';'"""
        p[0] = ReturnNode(**self._pos(p, 1), expr=p[2] if len(p) == 4 else None)

    def p_stmt_print(self, p):
        "stmt : PRINT '(' expr ')' ';'"
        p[0] = PrintNode(**self._pos(p, 1), expr=p[3])

    def p_stmt_printf(self, p):
        "stmt : PRINTF '(' STRING printf_args ')' ';'"
        p[0] = PrintfNode(**self._pos(p, 1), format=p[3][1:-1], args=p[4])

    def p_stmt_scanf(self, p):
        "stmt : SCANF '(' STRING scanf_args ')' ';'"
        p[0] = ScanfNode(**self._pos(p, 1), format=p[3][1:-1], names=p[4])

    def p_stmt_error(self, p):
        "stmt : error ';'"
        p[0] = None                                       # panic-mode recovery, like `error ';'` in minic.y

    def p_decl(self, p):
        """decl : type declarators ';'
                | CONST type declarators ';'"""
        const = len(p) == 5
        typ, tpos = p[2] if const else p[1]
        pos = self._pos(p, 1) if const else tpos
        out = []
        for i, (name, dims, init, npos) in enumerate(p[3] if const else p[2]):
            out.append(VarDeclNode(**(pos if i == 0 else npos), var_type=typ, name=name, dims=dims, expr=init, const=const))
        p[0] = out[0] if len(out) == 1 else out

    def p_declarators(self, p):
        """declarators : declarator
                       | declarators ',' declarator"""
        p[0] = [p[1]] if len(p) == 2 else p[1] + [p[3]]

    def p_declarator(self, p):
        """declarator : IDENT
                      | IDENT '=' init
                      | IDENT dims
                      | IDENT dims '=' init"""
        pos = self._pos(p, 1)
        dims = p[2] if len(p) in (3, 5) else []
        init = p[len(p) - 1] if len(p) in (4, 5) else None
        p[0] = (p[1], dims, init, pos)

    def p_dims(self, p):
        """dims : '[' ']'
                | '[' expr ']'
                | dims '[' expr ']'"""
        if len(p) == 3:
            p[0] = [None]
        elif len(p) == 4:
            p[0] = [p[2]]
        else:
            p[0] = p[1] + [p[3]]

    def p_init(self, p):
        """init : expr
                | '{' '}'
                | '{' init_items '}'
                | '{' init_items ',' '}'"""
        if len(p) == 2:
            p[0] = p[1]
        else:
            p[0] = InitListNode(**self._pos(p, 1), items=p[2] if len(p) > 3 else [])

    def p_init_items(self, p):
        """init_items : init
                      | init_items ',' init"""
        p[0] = [p[1]] if len(p) == 2 else p[1] + [p[3]]

    def p_indices(self, p):
        """indices : '[' expr ']'
                   | indices '[' expr ']'"""
        p[0] = [p[2]] if len(p) == 4 else p[1] + [p[3]]

    def p_function(self, p):
        "function : type IDENT '(' params ')' block"
        typ, pos = p[1]
        p[0] = FunctionNode(**pos, return_type=typ, name=p[2], params=p[4], body=p[6])

    def p_type(self, p):
        """type : INT
                | VOID
                | CHAR
                | SHORT
                | LONG
                | FLOAT
                | DOUBLE"""
        p[0] = (p[1], self._pos(p, 1))

    def p_params(self, p):
        """params :
                  | VOID
                  | param_list"""
        p[0] = p[1] if len(p) == 2 and isinstance(p[1], list) else []

    def p_param_list(self, p):
        """param_list : param
                      | param_list ',' param"""
        p[0] = [p[1]] if len(p) == 2 else p[1] + [p[3]]

    def p_param(self, p):
        """param : type IDENT
                 | type IDENT dims"""
        pos = self._pos(p, 2)
        p[0] = (p[1][0], p[2], pos["line"], pos["column"]) + ((p[3],) if len(p) == 4 else ())

    def p_block(self, p):
        "block : '{' stmts '}'"
        p[0] = BlockNode(**self._pos(p, 1), statements=p[2])

    def p_for_init(self, p):
        """for_init :
                    | type IDENT
                    | type IDENT '=' expr
                    | CONST type IDENT '=' expr
                    | assignment"""
        if len(p) == 1:
            p[0] = None
        elif len(p) == 2:
            p[0] = p[1]
        elif p[1] == "const":
            typ, pos = p[2]
            p[0] = VarDeclNode(**pos, var_type=typ, name=p[3], expr=p[5], const=True)
        else:
            typ, pos = p[1]
            p[0] = VarDeclNode(**pos, var_type=typ, name=p[2], expr=p[4] if len(p) == 5 else None)

    def p_for_update(self, p):
        """for_update :
                      | assignment"""
        p[0] = p[1] if len(p) == 2 else None

    def p_assignment(self, p):
        """assignment : IDENT assign_op expr
                      | IDENT indices assign_op expr
                      | IDENT INC
                      | IDENT DEC
                      | IDENT indices INC
                      | IDENT indices DEC
                      | INC IDENT
                      | DEC IDENT
                      | INC IDENT indices
                      | DEC IDENT indices"""
        pos = self._pos(p, 1)
        if p[1] in ("++", "--"):                          # ++x / --a[i]
            p[0] = make_assign(p[2], p[3] if len(p) == 4 else [], p[1], None, **pos)
        elif len(p) == 3:                                 # x++
            p[0] = make_assign(p[1], [], p[2], None, **pos)
        elif len(p) == 4 and p[3] in ("++", "--"):        # a[i]++
            p[0] = make_assign(p[1], p[2], p[3], None, **pos)
        elif len(p) == 4:                                 # x op= e
            p[0] = make_assign(p[1], [], p[2], p[3], **pos)
        else:                                             # a[i] op= e
            p[0] = make_assign(p[1], p[2], p[3], p[4], **pos)

    def p_assign_op(self, p):
        """assign_op : '='
                     | ADDEQ
                     | SUBEQ
                     | MULEQ
                     | DIVEQ
                     | MODEQ"""
        p[0] = p[1]

    def p_opt_expr(self, p):
        """opt_expr :
                    | expr"""
        p[0] = p[1] if len(p) == 2 else None

    def p_printf_args(self, p):
        """printf_args :
                       | printf_args ',' expr"""
        p[0] = [] if len(p) == 1 else p[1] + [p[3]]

    def p_scanf_args(self, p):
        """scanf_args :
                      | scanf_args ',' '&' IDENT
                      | scanf_args ',' '&' IDENT indices
                      | scanf_args ',' IDENT
                      | scanf_args ',' IDENT indices"""
        if len(p) == 1:
            p[0] = []
            return
        amp = p[3] == "&"
        k = 4 if amp else 3
        pos = self._pos(p, k)
        idx = p[k + 1] if len(p) > k + 1 else []
        item = (p[k], pos["line"], pos["column"]) if amp and not idx else (p[k], pos["line"], pos["column"], idx, amp)
        p[0] = p[1] + [item]

    def p_expr_number(self, p):
        "expr : NUMBER"
        p[0] = NumNode(**self._pos(p, 1), value=p[1])

    def p_expr_name(self, p):
        "expr : IDENT"
        p[0] = VarNode(**self._pos(p, 1), name=p[1])

    def p_expr_index(self, p):
        "expr : IDENT indices"
        p[0] = IndexNode(**self._pos(p, 1), name=p[1], indices=p[2])

    def p_expr_float(self, p):
        "expr : FLOATNUM"
        p[0] = FloatNode(**self._pos(p, 1), value=float(p[1].rstrip("fF")), text=p[1])

    def p_expr_char(self, p):
        "expr : CHARLIT"
        p[0] = CharNode(**self._pos(p, 1), value=p[1][0], text=p[1][1])

    def p_expr_string(self, p):
        "expr : STRING"
        p[0] = StringNode(**self._pos(p, 1), value=p[1][1:-1])

    def p_expr_cast(self, p):
        "expr : '(' type ')' expr %prec UMINUS"
        p[0] = CastNode(**self._pos(p, 1), to_type=p[2][0], expr=p[4])

    def p_expr_sizeof(self, p):
        """expr : SIZEOF '(' type ')'
                | SIZEOF '(' expr ')'"""
        if isinstance(p[3], tuple):
            p[0] = SizeofNode(**self._pos(p, 1), of_type=p[3][0])
        else:
            p[0] = SizeofNode(**self._pos(p, 1), expr=p[3])

    def p_expr_call(self, p):
        "expr : IDENT '(' args ')'"
        p[0] = CallNode(**self._pos(p, 1), name=p[1], args=p[3])

    def p_expr_group(self, p):
        "expr : '(' expr ')'"
        p[0] = p[2]

    def p_expr_not(self, p):
        "expr : '!' expr %prec UMINUS"
        pos = self._pos(p, 1)
        p[0] = BinOpNode(**pos, left=p[2], op="==", right=NumNode(**pos, value=0))

    def p_expr_minus(self, p):
        "expr : '-' expr %prec UMINUS"
        pos = self._pos(p, 1)
        p[0] = BinOpNode(**pos, left=NumNode(**pos, value=0), op="-", right=p[2])

    def p_expr_binary(self, p):
        """expr : expr '+' expr
                | expr '-' expr
                | expr '*' expr
                | expr '/' expr
                | expr '%' expr
                | expr '<' expr
                | expr '>' expr
                | expr LE expr
                | expr GE expr
                | expr EQ expr
                | expr NE expr
                | expr AND expr
                | expr OR expr"""
        p[0] = BinOpNode(**self._pos(p, 2), left=p[1], op=p[2], right=p[3])

    def p_args(self, p):
        """args :
                | arg_list"""
        p[0] = p[1] if len(p) == 2 else []

    def p_arg_list(self, p):
        """arg_list : expr
                    | arg_list ',' expr"""
        p[0] = [p[1]] if len(p) == 2 else p[1] + [p[3]]

    def p_error(self, t):
        state = self.parser.statestack[-1] if self.parser.statestack else 0
        expected = sorted(k for k in self.parser.action.get(state, {}) if k != "error")
        exp_text = ", ".join(READABLE.get(k, f"'{k}'" if len(k) == 1 else k.lower()) for k in expected[:8])
        if t is None:
            self.errors.append({"category": "Syntax Error", "line": self.lexer.lineno, "column": 1, "found": "EOF",
                                "message": "Unexpected end of file" + (f"; expected {exp_text}" if exp_text else ""),
                                "expected": exp_text, "recovery": "yacc reached the end of the file while recovering."})
            return
        line, col = t.lineno, _col(t.lexer.lexdata, t.lexpos)
        shown = t.value[1] if t.type == "CHARLIT" else str(t.value)
        what = f"{READABLE[t.type]} {shown!r}" if t.type in ("IDENT", "NUMBER", "STRING", "FLOATNUM", "CHARLIT") else repr(shown)
        self.errors.append({"category": "Syntax Error", "line": line, "column": col, "found": shown,
                            "message": f"Unexpected {what} at line {line}, column {col}"
                                       + (f"; expected {exp_text}" if exp_text else ""),
                            "expected": exp_text,
                            "recovery": "yacc error recovery: discarded input up to the next ';' (rule  stmt : error ';')"})


def _col(data, pos):
    return pos - data.rfind("\n", 0, pos)


# ------------------------------------------------------------------ build once
_G = None


def _grammar():
    global _G
    if _G is None:
        g = MiniCGrammar()
        g.errors = []
        log = _Collect()
        g.lexer = lex.lex(module=g, errorlog=log)
        g.parser = yacc.yacc(module=g, start="program", debug=False, write_tables=False, errorlog=log)
        g.log = log
        _G = g
    return _G


def grammar_info():
    g = _grammar()
    prods = [str(p) for p in g.parser.productions[1:]]
    conflicts = [m for k, m in g.log.lines if "conflict" in m]
    return {"productions": prods, "states": len(g.parser.action), "conflicts": conflicts,
            "tokens": list(g.tokens), "literals": list(g.literals)}


def tokens_of(source):
    """Token stream from the PLY lexer: list of (type, value, line, column)."""
    from compiler.preprocess import preprocess
    source = preprocess(source)[0]
    g = _grammar()
    lx = g.lexer.clone()
    lx.input(source); lx.lineno = 1
    g.errors = []
    out = []
    while True:
        t = lx.token()
        if t is None:
            break
        out.append((t.type, t.value, t.lineno, _col(source, t.lexpos)))
    return out, list(g.errors)


def parse(source, trace=False, max_rows=3000):
    from compiler.preprocess import preprocess
    source, pre_errors = preprocess(source)               # #define / #undef, like the hand-written front end
    g = _grammar()
    g.errors = list(pre_errors)
    lx = g.lexer.clone()
    lx.lineno = 1
    g.lexer = lx
    tr = _Trace(max_rows) if trace else None
    ast = g.parser.parse(source, lexer=lx, debug=tr, tracking=True)
    errors = list(g.errors)
    return {"ast": ast if not errors else None, "partial_ast": ast, "errors": errors, "accepted": not errors,
            "trace": tr.rows if tr else None, "trace_truncated": tr.truncated if tr else False}


# ------------------------------------------------------------ comparisons
POSITION_FIELDS = {"line", "column", "end_line", "end_column"}


def _norm(n):
    if isinstance(n, list):
        return [_norm(x) for x in n]
    if isinstance(n, tuple):
        return tuple(_norm(x) for x in n if not isinstance(x, int) or True)
    if hasattr(n, "__dataclass_fields__"):
        out = {"type": type(n).__name__}
        for k in n.__dataclass_fields__:
            if k in POSITION_FIELDS:
                continue
            v = getattr(n, k)
            if k in ("params", "names"):                   # (type, name, line, col[, sizes / indexes, &])
                v = [tuple(x for x in item if not (isinstance(x, int) and not isinstance(x, bool))) for item in v]
            out[k] = _norm(v)
        return out
    return n


def same_ast(a, b, path="program"):
    """None if the trees are identical (ignoring positions), else a description of the first difference."""
    return _diff(_norm(a), _norm(b), path)


def _diff(a, b, path):
    if type(a) != type(b):
        return f"{path}: {type(a).__name__} vs {type(b).__name__}"
    if isinstance(a, dict):
        if a.get("type") != b.get("type"):
            return f"{path}: {a.get('type')} vs {b.get('type')}"
        for k in a:
            d = _diff(a[k], b.get(k), f"{path}.{k}")
            if d:
                return d
        return None
    if isinstance(a, list):
        if len(a) != len(b):
            return f"{path}: {len(a)} vs {len(b)} items"
        for i, (x, y) in enumerate(zip(a, b)):
            d = _diff(x, y, f"{path}[{i}]")
            if d:
                return d
        return None
    return None if a == b else f"{path}: {a!r} vs {b!r}"


def statement_count(ast):
    """Statements the way minic.y counts them (function definitions and function bodies excluded)."""
    def count(n):
        if n is None:
            return 0
        if isinstance(n, FunctionNode):
            return sum(count(s) for s in n.body.statements)
        if isinstance(n, BlockNode):
            return 1 + sum(count(s) for s in n.statements)
        if isinstance(n, IfNode):
            return 1 + count(n.then_branch) + count(n.else_branch)
        if isinstance(n, (WhileNode, ForNode, DoWhileNode)):
            return 1 + count(n.body)
        if isinstance(n, SwitchNode):
            return 1 + sum(count(s) for c in n.cases for s in c.statements)
        return 1
    return sum(count(s) for s in ast.statements)
