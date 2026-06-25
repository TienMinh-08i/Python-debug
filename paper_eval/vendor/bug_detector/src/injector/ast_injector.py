"""AST-based assertion injector — insert assertions before every return statement.

Convention: biến return value là `return_value` (giống nl2postcond).
"""

import ast
import textwrap
from typing import Optional

from src.injector.code_util import replace_function, get_function_source

# Tên biến giữ return value — phải khớp với convention nl2postcond
_RETURN_VAR = "return_value"


class _AssertionInjector(ast.NodeTransformer):
    """
    Transforms a function so that every `return <expr>` becomes:
        return_value = <expr>
        <assertions...>
        return return_value

    Dùng `return_value` (không phải `_result`) để khớp với convention nl2postcond:
    các assertion sinh ra bởi nl2postcond đều dùng biến `return_value`.

    For bare `return` (no value), assertions are injected before it as-is.
    Nested function definitions are NOT transformed.
    """

    def __init__(self, assertions: list[str], is_top_level: bool = True):
        self.assertions = assertions
        self.is_top_level = is_top_level

    def _make_assign(self, value_node: ast.expr) -> ast.Assign:
        """Create: return_value = <value_node>"""
        return ast.Assign(
            targets=[ast.Name(id=_RETURN_VAR, ctx=ast.Store())],
            value=value_node,
            lineno=value_node.lineno if hasattr(value_node, "lineno") else 0,
            col_offset=0,
        )

    def _make_assertions(self) -> list[ast.stmt]:
        """Parse assertion strings into AST statement nodes."""
        stmts = []
        for assert_str in self.assertions:
            try:
                parsed = ast.parse(assert_str.strip(), mode="exec")
                stmts.extend(parsed.body)
            except SyntaxError:
                pass
        return stmts

    def _make_return_result(self) -> ast.Return:
        """Create: return return_value"""
        return ast.Return(
            value=ast.Name(id=_RETURN_VAR, ctx=ast.Load()),
            lineno=0,
            col_offset=0,
        )

    def visit_FunctionDef(self, node: ast.FunctionDef):
        if self.is_top_level:
            # Transform the outermost function, but use a non-top-level
            # transformer for any nested defs so they are left unchanged
            new_body = []
            for stmt in node.body:
                new_stmt = _AssertionInjector(
                    self.assertions, is_top_level=False
                ).visit(stmt)
                if isinstance(new_stmt, list):
                    new_body.extend(new_stmt)
                else:
                    new_body.append(new_stmt)
            node.body = new_body
            return node
        else:
            # Nested function — do NOT transform
            return node

    # AsyncFunctionDef follows the same logic
    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        return self.visit_FunctionDef(node)  # type: ignore

    def visit_Return(self, node: ast.Return) -> list[ast.stmt]:
        assert_stmts = self._make_assertions()

        if node.value is None:
            # bare `return` — inject assertions then return
            return [*assert_stmts, node]

        # `return <expr>` → `_result = <expr>` + assertions + `return _result`
        assign = self._make_assign(node.value)
        ret = self._make_return_result()

        # Copy line numbers from the original return for accurate tracebacks
        assign.lineno = node.lineno
        assign.col_offset = node.col_offset
        ret.lineno = node.lineno
        ret.col_offset = node.col_offset

        return [assign, *assert_stmts, ret]


def inject_assertions(func_source: str, assertions: list[str]) -> str:
    """
    Chèn các assertion vào TRƯỚC mỗi return statement trong hàm.

    Convention: biến return value là `return_value` (khớp với nl2postcond).

    Input:
        func_source: source code của hàm
        assertions: list assertion strings, vd ['assert return_value > 0']

    Output:
        source code đã chèn assertion

    Ví dụ:
        def add(a, b):
            return a + b
        →
        def add(a, b):
            return_value = a + b
            assert return_value > 0
            return return_value
    """
    if not assertions:
        return func_source

    # Dedent so ast.parse always works regardless of indentation
    dedented = textwrap.dedent(func_source)

    try:
        tree = ast.parse(dedented)
    except SyntaxError as e:
        raise ValueError(f"Cannot parse function source: {e}")

    transformer = _AssertionInjector(assertions, is_top_level=True)
    new_tree = transformer.visit(tree)
    ast.fix_missing_locations(new_tree)

    return ast.unparse(new_tree)


def inject_into_file(file_source: str, func_name: str, assertions: list[str]) -> str:
    """Inject assertions vào một hàm cụ thể trong file."""
    original_func = get_function_source(file_source, func_name)
    injected_func = inject_assertions(original_func, assertions)
    return replace_function(file_source, func_name, injected_func)
