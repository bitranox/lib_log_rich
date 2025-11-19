#!/usr/bin/env python3.13
"""Manual complexity and code quality analysis."""

import ast
import sys
from pathlib import Path
from typing import Tuple


def count_lines(filepath: Path) -> Tuple[int, int, int]:
    """Count total, code, and comment lines."""
    with open(filepath) as f:
        lines = f.readlines()

    total = len(lines)
    code = 0
    comments = 0

    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            code += 1
        elif stripped.startswith("#"):
            comments += 1

    return total, code, comments


def analyze_function(node: ast.FunctionDef) -> dict:
    """Analyze a function for complexity metrics."""
    # Count lines
    start_line = node.lineno
    end_line = node.end_lineno if hasattr(node, "end_lineno") else start_line
    line_count = end_line - start_line + 1

    # Count complexity indicators
    class ComplexityVisitor(ast.NodeVisitor):
        def __init__(self):
            self.complexity = 1
            self.nesting_depth = 0
            self.max_nesting = 0

        def visit_If(self, node):
            self.complexity += 1
            self.nesting_depth += 1
            self.max_nesting = max(self.max_nesting, self.nesting_depth)
            self.generic_visit(node)
            self.nesting_depth -= 1

        def visit_For(self, node):
            self.complexity += 1
            self.nesting_depth += 1
            self.max_nesting = max(self.max_nesting, self.nesting_depth)
            self.generic_visit(node)
            self.nesting_depth -= 1

        def visit_While(self, node):
            self.complexity += 1
            self.nesting_depth += 1
            self.max_nesting = max(self.max_nesting, self.nesting_depth)
            self.generic_visit(node)
            self.nesting_depth -= 1

        def visit_Try(self, node):
            self.complexity += len(node.handlers)
            self.generic_visit(node)

        def visit_BoolOp(self, node):
            self.complexity += len(node.values) - 1
            self.generic_visit(node)

    visitor = ComplexityVisitor()
    visitor.visit(node)

    return {
        "name": node.name,
        "line": start_line,
        "lines": line_count,
        "complexity": visitor.complexity,
        "max_nesting": visitor.max_nesting,
    }


def analyze_file(filepath: Path) -> dict:
    """Analyze a Python file."""
    with open(filepath) as f:
        content = f.read()

    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        return {"error": str(e)}

    total, code, comments = count_lines(filepath)

    functions = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            func_info = analyze_function(node)
            functions.append(func_info)

    return {
        "file": str(filepath),
        "total_lines": total,
        "code_lines": code,
        "comment_lines": comments,
        "functions": functions,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: analyze_complexity.py <file.py>")
        sys.exit(1)

    filepath = Path(sys.argv[1])
    result = analyze_file(filepath)

    if "error" in result:
        print(f"ERROR: {result['error']}")
        sys.exit(1)

    print(f"File: {result['file']}")
    print(f"Lines: {result['total_lines']} total, {result['code_lines']} code, {result['comment_lines']} comments")
    print(f"\nFunctions ({len(result['functions'])}):")
    print(f"{'Name':<40} {'Line':<6} {'Lines':<6} {'Complexity':<12} {'MaxNest':<8}")
    print("-" * 80)

    for func in sorted(result["functions"], key=lambda f: f["complexity"], reverse=True):
        flag = ""
        if func["lines"] > 50:
            flag = "⚠ LONG"
        elif func["complexity"] > 10:
            flag = "⚠ COMPLEX"
        elif func["max_nesting"] > 3:
            flag = "⚠ NESTED"

        print(f"{func['name']:<40} {func['line']:<6} {func['lines']:<6} {func['complexity']:<12} {func['max_nesting']:<8} {flag}")
