import unittest
from compiler.lexer import Lexer
from compiler.parser import Parser, BinOpNode, ProgramNode
from compiler.semantic import SemanticAnalyzer
from compiler.code_gen import CodeGenerator

class CompilerTests(unittest.TestCase):
    def parse(self, src): return Parser(Lexer(src).tokenize()).parse()
    def test_operators_and_locations(self):
        ts=Lexer('int x = a == 2;\n').tokenize()
        self.assertIn(('OP','=='), [(t.kind,t.value) for t in ts])
        self.assertEqual(ts[0].line,1)
        self.assertEqual(ts[-1].line,2)
    def test_precedence(self):
        ast=self.parse('int x = 10 + 20 * 3;')
        expr=ast.statements[0].expr
        self.assertEqual(expr.op,'+')
        self.assertEqual(expr.right.op,'*')
    def test_semantic_undeclared(self):
        ast=self.parse('int x = y + 1;')
        ds=SemanticAnalyzer().analyze(ast)
        self.assertTrue(any("Undeclared variable 'y'" in d['message'] for d in ds))
    def test_codegen_control_flow(self):
        ast=self.parse('int x = 1; while (x < 3) { x = x + 1; }')
        ir=CodeGenerator().generate(ast)
        self.assertTrue(any('IF_FALSE' in x for x in ir))
        self.assertTrue(any('GOTO' in x for x in ir))
    def test_missing_semicolon(self):
        with self.assertRaises(SyntaxError): self.parse('int x = 1')

if __name__=='__main__': unittest.main()
