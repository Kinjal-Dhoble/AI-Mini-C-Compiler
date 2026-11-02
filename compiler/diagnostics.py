"""Human-readable compiler diagnostics: one-line format, plain-English explanation, fix hint."""
import re


def format_diagnostic(diagnostic):
    category = diagnostic.get("category", "Error")
    line = diagnostic.get("line", "?")
    column = diagnostic.get("column", "?")
    message = diagnostic.get("message", "Unknown error")
    return f"{category} at line {line}, column {column}: {message}"


RULES = [
    ("ignored: mini c needs no header", "Header files declare library functions like printf and strlen. Mini C has them built in, so the line is skipped (keeping it makes the file valid standard C)."),
    ("bad character literal", "A char holds exactly one character, written in single quotes: 'a', '\\n' or '\\0'."),
    ("array index out of bounds", "The index is outside the array. An array of N elements has indexes 0 to N - 1."),
    ("too many initialisers", "The { ... } list has more values than the array has places."),
    ("array size", "An array needs a fixed number of elements, known before the program runs, e.g. int a[10];"),
    ("only the first array size", "In a 2-D array only the number of rows may be left out; every other size must be written."),
    ("not an array", "Square brackets [ ] can only be used on a variable declared as an array."),
    ("indexes were given", "The array has fewer dimensions than the number of [ ] used here."),
    ("array index must be", "An index counts positions, so it must be a whole number (int or char), not a decimal."),
    ("whole array", "C cannot copy a whole array with '='. Copy it element by element, or use strcpy for strings."),
    ("too long for", "The text needs one place per character plus one more for the '\\0' that ends every string."),
    ("leaves no room", "Every string ends with a hidden '\\0'. Without room for it, string functions do not know where the text stops."),
    ("single quotes", "Single quotes make one character ('a'); double quotes make a string (\"abc\"), which is a char array."),
    ("compares the addresses", "For arrays, == checks whether they are the same array in memory, not whether they hold the same text. Use strcmp(a, b) == 0."),
    ("needs a string", "This library function works on text: give it a char array or a string in double quotes."),
    ("writes into its first argument", "strcpy, strcat and similar store their result in the first argument, so it must be an array you own, not a fixed piece of text."),
    ("printf %", "The printf placeholder does not fit the value: %d int, %ld long, %f float/double, %c char, %s string."),
    ("printf format has", "Each placeholder (%d, %f, %s ...) needs exactly one value after the format string."),
    ("scanf %", "The scanf conversion does not fit the variable: %d int, %ld long, %f float, %lf double, %c char, %s string."),
    ("needs the address", "scanf stores into a variable, so it needs the variable's address: &x. Arrays (strings) are already addresses."),
    ("needs no &", "An array name already tells scanf where to store the text, so & is not needed."),
    ("% only works", "The remainder operator % is only defined for whole numbers."),
    ("print() shows", "print(x) prints one int. printf can print every type with the right placeholder."),
    ("cannot convert", "Only single values can be converted with a cast; an array cannot."),
    ("cannot return", "A function returns one value. Arrays are passed to functions (by reference) instead of being returned."),
    ("argument", "The value passed does not fit the parameter: arrays go to array parameters ([]), numbers to number parameters."),
    ("unexpected character", "The lexer found a character that is not part of Mini C."),
    ("in expression", "A value was expected here (a number, a variable, a call or '('), but the next token cannot start one."),
    ("function body must be a block", "A function's body must be wrapped in { and }."),
    ("unsupported parameter type", "Function parameters need a type: char, short, int, long, float or double (add [] for an array)."),
    ("undeclared variable", "The program uses a variable name that has not been declared in the current scope. Check the spelling and declare it before use."),
    ("undeclared function", "The program calls a function that is not defined anywhere in the file."),
    ("expects", "The call passes a different number of values than the function has parameters."),
    ("used before initialization", "The variable may be read before it has been given a value. Assign a value before using it."),
    ("duplicate declaration", "This name is already declared in the same scope. Rename the variable or remove the duplicate declaration."),
    ("duplicate function", "Two functions have the same name, so the compiler cannot tell which one a call means."),
    ("void function", "A void function does not produce a value, so it cannot return one or be used inside an expression."),
    ("result of a void", "A void function does not produce a value, so its result cannot be used."),
    ("void value", "A void function does not produce a value, so its result cannot be used."),
    ("should return", "This function is declared to return a value, but this return statement gives none."),
    ("top level", "Functions cannot be defined inside other functions."),
    ("cannot initialize", "The initializer's type does not match the declared variable type."),
    ("cannot assign", "The expression's type does not match the variable's type."),
    ("incompatible operand types", "The operator is being applied to operands with different types."),
    ("division by constant zero", "The expression divides by zero, which crashes the program at runtime."),
    ("never read", "A value is assigned to this variable but nothing reads it later. Remove it or use it."),
    ("never called", "Nothing in the program calls this function, so its code never runs."),
    ("magic number", "An unexplained number makes code hard to read and change. A named variable says what it means."),
    ("unreachable", "No path through the program can reach this code, so it never runs."),
    ("always true", "The condition is a fixed number, so the decision is the same every time."),
    ("always false", "The condition is a fixed number, so this code can never run."),
    ("deeply nested", "Several levels of nesting make the logic hard to follow. Consider simplifying it."),
    ("more than 20", "This function does many things. Consider splitting it into smaller functions."),
    ("parameters;", "Functions with many parameters are hard to call correctly."),
]


def explain_diagnostic(diagnostic):
    """Plain-language explanation for a diagnostic (syntax errors get expected/found wording)."""
    message = diagnostic.get("message", "")
    lower = message.lower()
    if diagnostic.get("category") == "Syntax Error" and lower.startswith("expected"):
        m = re.search(r"Expected (.*?), got (.*?)(?: at line|$)", message)
        if m:
            got = "the end of the file" if m.group(2) == "'EOF'" else m.group(2)
            return (f"The parser expected {m.group(1)} but found {got}. A token is missing or out of place, "
                    "often at the end of the previous line.")
    for key, text in RULES:
        if key in lower:
            return text
    if diagnostic.get("category") in ("Warning", "Code Smell"):
        return "This does not stop compilation, but it is worth fixing to avoid bugs or confusing code."
    return "Review the reported code and make the names, types and Mini C syntax consistent."


def enrich(d):
    """Add explanation / suggestion text used by both the CLI and the IDE."""
    out = dict(d)
    out["explanation"] = explain_diagnostic(d)
    if not out.get("suggestion"):
        out["suggestion"] = d.get("hint")
    return out
