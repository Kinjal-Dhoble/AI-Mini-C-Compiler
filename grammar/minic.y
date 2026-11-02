/* minic.y - bison grammar for Mini C (LALR(1)).
 *
 * Build (see tools/build_yacc.py):  bison -d -o minic.tab.c minic.y
 * The same language as the hand-written recursive-descent parser in compiler/parser.py.
 * tools/parser_agreement.py checks that both parsers accept and reject the same programs
 * and see the same functions and the same number of statements.
 *
 * Output (one item per line):
 *   ERROR line:col message      for every syntax or lexical error (error recovery keeps going)
 *   RESULT accept|reject
 *   ERRORS n
 *   FUNCTIONS name,name,...
 *   STATEMENTS n
 */
%{
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int yylex(void);
void yyerror(const char *msg);
extern FILE *yyin;
extern int yylineno;

int n_errors = 0;
int n_statements = 0;
static char functions[8192] = "";

static void add_function(const char *name) {
    if (strlen(functions) + strlen(name) + 2 < sizeof functions) {
        if (functions[0]) strcat(functions, ",");
        strcat(functions, name);
    }
}
%}

%define parse.error verbose
%locations

%union { int num; char *str; }

%token <num> NUMBER "number"
%token <str> IDENT  "identifier"
%token STRING "string" FLOATNUM "decimal number" CHARLIT "character"
%token INT "int" VOID "void" IF "if" ELSE "else" WHILE "while" FOR "for" RETURN "return"
%token PRINT "print" PRINTF "printf" SCANF "scanf"
%token CHAR "char" SHORT "short" LONG "long" FLOAT "float" DOUBLE "double" SIZEOF "sizeof"
%token BREAK "break" CONTINUE "continue" DO "do" SWITCH "switch" CASE "case" DEFAULT "default" CONST "const"
%token EQ "==" NE "!=" LE "<=" GE ">=" AND "&&" OR "||" INC "++" DEC "--"
%token ADDEQ "+=" SUBEQ "-=" MULEQ "*=" DIVEQ "/=" MODEQ "%="

/* precedence, lowest first: exactly C's order for these operators */
%left OR
%left AND
%left EQ NE
%left '<' '>' LE GE
%left '+' '-'
%left '*' '/' '%'
%precedence UMINUS
/* dangling else: an else belongs to the nearest if */
%precedence IFX
%precedence ELSE

%start program

%%

program     : %empty
            | program stmt
            ;

stmt        : decl                                         /* counted once per variable, in declarator */
            | function
            | assignment ';'                               { n_statements++; }
            | BREAK ';'                                    { n_statements++; }
            | CONTINUE ';'                                 { n_statements++; }
            | DO stmt WHILE '(' expr ')' ';'               { n_statements++; }
            | SWITCH '(' expr ')' '{' switch_body '}'      { n_statements++; }
            | expr ';'                                     { n_statements++; }
            | ';'                                          { n_statements++; }
            | block                                        { n_statements++; }
            | IF '(' expr ')' stmt %prec IFX               { n_statements++; }
            | IF '(' expr ')' stmt ELSE stmt               { n_statements++; }
            | WHILE '(' expr ')' stmt                      { n_statements++; }
            | FOR '(' for_init ';' opt_expr ';' for_update ')' stmt  { n_statements++; }
            | RETURN ';'                                   { n_statements++; }
            | RETURN expr ';'                              { n_statements++; }
            | PRINT '(' expr ')' ';'                       { n_statements++; }
            | PRINTF '(' STRING printf_args ')' ';'        { n_statements++; }
            | SCANF '(' STRING scanf_args ')' ';'          { n_statements++; }
            | error ';'                                    { yyerrok; }   /* panic-mode recovery */
            ;

decl        : type declarators ';'
            | CONST type declarators ';'
            ;

declarators : declarator
            | declarators ',' declarator
            ;

declarator  : IDENT                                        { n_statements++; free($1); }
            | IDENT '=' init                               { n_statements++; free($1); }
            | IDENT dims                                   { n_statements++; free($1); }
            | IDENT dims '=' init                          { n_statements++; free($1); }
            ;

switch_body : %empty
            | labelled
            ;

labelled    : label
            | labelled label
            | labelled stmt
            ;

label       : CASE expr ':'
            | DEFAULT ':'
            ;

dims        : '[' ']'
            | '[' expr ']'
            | dims '[' expr ']'
            ;

init        : expr
            | '{' '}'
            | '{' init_items '}'
            | '{' init_items ',' '}'
            ;

init_items  : init
            | init_items ',' init
            ;

indices     : '[' expr ']'
            | indices '[' expr ']'
            ;

function    : type IDENT '(' params ')' block              { add_function($2); free($2); }
            ;

type        : INT
            | VOID
            | CHAR
            | SHORT
            | LONG
            | FLOAT
            | DOUBLE
            ;

params      : %empty
            | VOID
            | param_list
            ;

param_list  : param
            | param_list ',' param
            ;

param       : type IDENT                                   { free($2); }
            | type IDENT dims                              { free($2); }
            ;

block       : '{' stmts '}'
            ;

stmts       : %empty
            | stmts stmt
            ;

for_init    : %empty
            | type IDENT                                   { free($2); }
            | type IDENT '=' expr                          { free($2); }
            | CONST type IDENT '=' expr                    { free($3); }
            | assignment
            ;

for_update  : %empty
            | assignment
            ;

assignment  : IDENT assign_op expr                         { free($1); }
            | IDENT indices assign_op expr                 { free($1); }
            | IDENT INC                                    { free($1); }
            | IDENT DEC                                    { free($1); }
            | IDENT indices INC                            { free($1); }
            | IDENT indices DEC                            { free($1); }
            | INC IDENT                                    { free($2); }
            | DEC IDENT                                    { free($2); }
            | INC IDENT indices                            { free($2); }
            | DEC IDENT indices                            { free($2); }
            ;

assign_op   : '=' | ADDEQ | SUBEQ | MULEQ | DIVEQ | MODEQ
            ;

opt_expr    : %empty
            | expr
            ;

printf_args : %empty
            | printf_args ',' expr
            ;

scanf_args  : %empty
            | scanf_args ',' '&' IDENT                     { free($4); }
            | scanf_args ',' '&' IDENT indices             { free($4); }
            | scanf_args ',' IDENT                         { free($3); }
            | scanf_args ',' IDENT indices                 { free($3); }
            ;

expr        : NUMBER
            | FLOATNUM
            | CHARLIT
            | STRING
            | IDENT                                        { free($1); }
            | IDENT indices                                { free($1); }
            | '(' type ')' expr %prec UMINUS
            | SIZEOF '(' type ')'
            | SIZEOF '(' expr ')'
            | IDENT '(' args ')'                           { free($1); }
            | '(' expr ')'
            | '-' expr %prec UMINUS
            | '!' expr %prec UMINUS
            | expr '+' expr
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
            | expr OR expr
            ;

args        : %empty
            | arg_list
            ;

arg_list    : expr
            | arg_list ',' expr
            ;

%%

void yyerror(const char *msg) {
    n_errors++;
    printf("ERROR %d:%d %s\n", yylloc.first_line, yylloc.first_column, msg);
}

int main(int argc, char **argv) {
    if (argc > 1) {
        yyin = fopen(argv[1], "r");
        if (!yyin) { perror(argv[1]); return 2; }
    }
    int rc = yyparse();
    int ok = (rc == 0 && n_errors == 0);
    printf("RESULT %s\n", ok ? "accept" : "reject");
    printf("ERRORS %d\n", n_errors);
    printf("FUNCTIONS %s\n", functions);
    printf("STATEMENTS %d\n", n_statements);
    return ok ? 0 : 1;
}
