"""Rule-based error explanations; this module does not call an external LLM."""
import re


class AIAssistantDebugger:
    @staticmethod
    def analyze_error(source_code, exception):
        msg = str(exception)
        suggestions = []
        line_match = re.search(r"line\s+(\d+)", msg, re.IGNORECASE)
        col_match = re.search(r"column\s+(\d+)", msg, re.IGNORECASE)
        line = int(line_match.group(1)) if line_match else None
        column = int(col_match.group(1)) if col_match else 1

        if "Unexpected character" in msg:
            diagnosis = "The lexer found a character this Mini C compiler does not support at the reported position."
            suggestions.append("Remove the character or replace it with a supported operator or delimiter.")
        elif "Unexpected token" in msg and "in expression" in msg:
            diagnosis = "An expression was expected here, but the next token cannot start an expression."
            suggestions.extend([
                "Check for a missing operand, such as `x = ;` or `x + ;`.",
                "Check that parentheses and operators are in the correct order."
            ])
        elif "Expected" in msg:
            expected_match = re.search(r"Expected (.*?), got (.*?)(?: at line|$)", msg)
            expected = expected_match.group(1) if expected_match else "a required token"
            actual = expected_match.group(2) if expected_match else "the current token"
            diagnosis = f"The parser expected {expected}, but found {actual}. This usually means a token is missing or out of place."
            if ";" in msg:
                suggestions.append("Add a semicolon (`;`) at the end of the declaration, assignment, or return statement.")
            if ")" in msg:
                suggestions.append("Check that every opening parenthesis `(` has a matching closing parenthesis `)`.")
            if "}" in msg:
                suggestions.append("Check that every opening block brace `{` has a matching closing brace `}`.")
            if not suggestions:
                suggestions.append("Correct the token near the reported line and column, then compile again.")
        elif "Unsupported parameter type" in msg:
            diagnosis = "The function parameter uses a type that is not supported by this Mini C subset."
            suggestions.append("Use `int` parameters; this educational compiler supports only a limited set of C features.")
        else:
            diagnosis = "The compiler could not parse this source construct."
            suggestions.append("Check the reported line and column and compare the construct with the supported Mini C syntax.")

        if line is not None:
            suggestions.append(f"Inspect line {line}, column {column}; the caret location is where the parser noticed the problem.")
        return {
            "error": msg,
            "ai_diagnosis": diagnosis,
            "recommended_fixes": suggestions,
            "line": line,
            "column": column,
            "diagnostics": [{
                "category": "Syntax Error" if not "Unexpected character" in msg else "Lexical Error",
                "message": msg, "line": line or 1, "column": column or 1
            }]
        }
