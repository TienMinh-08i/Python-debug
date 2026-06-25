"""Helper functions for working with Python source code using AST."""

import ast
import textwrap
from typing import Optional


def get_function_source(
    source_code: str,
    func_name: str,
    lineno: int | None = None,
) -> str:
    """Lấy source code của một hàm cụ thể từ file.

    Nếu có nhiều hàm trùng tên (overload stubs), truyền ``lineno`` để chọn đúng định nghĩa.
    """
    try:
        tree = ast.parse(source_code)
    except SyntaxError as e:
        raise ValueError(f"Cannot parse source: {e}")

    matches: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == func_name:
                if lineno is None or node.lineno == lineno:
                    matches.append(node)

    if not matches:
        raise ValueError(f"Function '{func_name}' not found in source")

    node = matches[0] if lineno is not None else matches[-1]
    lines = source_code.splitlines(keepends=True)
    start = node.lineno - 1
    end = node.end_lineno
    return "".join(lines[start:end])


def replace_function(
    file_source: str,
    func_name: str,
    new_func_source: str,
    lineno: int | None = None,
) -> str:
    """Thay thế định nghĩa hàm trong file bằng version mới."""
    file_source = file_source.lstrip("\ufeff")
    try:
        tree = ast.parse(file_source)
    except SyntaxError as e:
        raise ValueError(f"Cannot parse source: {e}")

    lines = file_source.splitlines(keepends=True)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == func_name:
                if lineno is not None and node.lineno != lineno:
                    continue
                start = node.lineno - 1   # 0-indexed
                end = node.end_lineno     # 0-indexed exclusive
                # Preserve indentation of the original definition
                indent = len(lines[start]) - len(lines[start].lstrip())
                indent_str = lines[start][:indent]
                dedented = textwrap.dedent(new_func_source).strip("\n")
                new_func_indented = textwrap.indent(dedented, indent_str)
                # Ensure trailing newline
                if not new_func_indented.endswith("\n"):
                    new_func_indented += "\n"
                new_content = (
                    "".join(lines[:start])
                    + new_func_indented
                    + "".join(lines[end:])
                )
                return new_content

    raise ValueError(f"Function '{func_name}' not found in source")


def get_docstring(func_source: str) -> str:
    """Lấy docstring từ source code của hàm."""
    try:
        tree = ast.parse(func_source)
    except SyntaxError:
        return ""

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return ast.get_docstring(node) or ""
    return ""


def get_return_statements(func_source: str) -> list[ast.Return]:
    """Lấy tất cả return statements trong hàm (top-level only, không đệ quy vào nested func)."""
    try:
        tree = ast.parse(func_source)
    except SyntaxError:
        return []

    returns = []

    class ReturnCollector(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef):
            # Visit children of the OUTERMOST function, skip nested defs
            if not returns and node.lineno == tree.body[0].lineno if tree.body else True:
                self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
            self.visit_FunctionDef(node)

        def visit_Return(self, node: ast.Return):
            returns.append(node)

    collector = ReturnCollector()
    collector.visit(tree)
    return returns
