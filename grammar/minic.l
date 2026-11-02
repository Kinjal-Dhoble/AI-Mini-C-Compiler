/* minic.l - flex scanner for Mini C.
 *
 * Build (see tools/build_yacc.py):  flex -o lex.yy.c minic.l
 * Produces the same tokens as compiler/lexer.py (numbers, decimals 3.14 / 2.5f, characters 'a', strings). printf and scanf are keywords here;
 * #include lines are skipped, like the hand-written lexer does. #define / #undef are expanded before this scanner
 * runs (tools/build_yacc.py passes the program through compiler/preprocess.py first).
 */
%{
#include <stdio.h>
#include <string.h>
#include "minic.tab.h"

extern int n_errors;
static int col = 1;
#define YY_USER_ACTION { yylloc.first_line = yylloc.last_line = yylineno; \
                         yylloc.first_column = col; col += yyleng; yylloc.last_column = col - 1; }
%}

%option yylineno noyywrap nounput noinput
%x COMMENT

DIGIT   [0-9]
LETTER  [A-Za-z_]

%%
"/*"                    { BEGIN(COMMENT); }
<COMMENT>"*/"           { BEGIN(INITIAL); }
<COMMENT>\n             { col = 1; }
<COMMENT>.              { /* inside a block comment */ }
"//".*                  { /* line comment */ }
^[ \t]*"#"[ \t]*"include"[^\n]*  { /* #include lines are accepted and ignored */ }

"int"                   { return INT; }
"void"                  { return VOID; }
"if"                    { return IF; }
"else"                  { return ELSE; }
"while"                 { return WHILE; }
"for"                   { return FOR; }
"return"                { return RETURN; }
"print"                 { return PRINT; }
"printf"                { return PRINTF; }
"scanf"                 { return SCANF; }
"char"                  { return CHAR; }
"short"                 { return SHORT; }
"long"                  { return LONG; }
"float"                 { return FLOAT; }
"double"                { return DOUBLE; }
"sizeof"                { return SIZEOF; }
"break"                 { return BREAK; }
"continue"              { return CONTINUE; }
"do"                    { return DO; }
"switch"                { return SWITCH; }
"case"                  { return CASE; }
"default"               { return DEFAULT; }
"const"                 { return CONST; }

({DIGIT}+"."{DIGIT}*|"."{DIGIT}+)([eE][-+]?{DIGIT}+)?[fF]?  { return FLOATNUM; }
{DIGIT}+[eE][-+]?{DIGIT}+[fF]?  { return FLOATNUM; }
{DIGIT}+                { yylval.num = atoi(yytext); return NUMBER; }
\'([^'\\\n]|\\.)\'   { return CHARLIT; }
{LETTER}({LETTER}|{DIGIT})*  { yylval.str = strdup(yytext); return IDENT; }
\"([^"\\\n]|\\.)*\"     { return STRING; }

"=="                    { return EQ; }
"!="                    { return NE; }
"<="                    { return LE; }
">="                    { return GE; }
"&&"                    { return AND; }
"||"                    { return OR; }
"++"                    { return INC; }
"--"                    { return DEC; }
"+="                    { return ADDEQ; }
"-="                    { return SUBEQ; }
"*="                    { return MULEQ; }
"/="                    { return DIVEQ; }
"%="                    { return MODEQ; }
[-+*/%<>=(){};,&\[\]!:]  { return yytext[0]; }

[ \t\r]+                { /* whitespace */ }
\n                      { col = 1; }
.                       { n_errors++;
                          printf("ERROR %d:%d lexical error: unexpected character '%s'\n", yylineno, col - 1, yytext); }
%%
