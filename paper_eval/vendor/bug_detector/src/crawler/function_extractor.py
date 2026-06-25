"""Function extractor — find modified functions with docstrings and tests."""

import ast
from pathlib import Path
from typing import Optional

import libcst as cst
import libcst.metadata as meta

from src.models import FunctionInfo
from src.tracker.logger import log_info, log_warn, log_debug


# Dunder methods to skip
_SKIP_METHODS = {"__init__", "__str__", "__repr__", "__eq__"}


class _FunctionCollector(cst.CSTVisitor):
    """Collect all function definitions with their source code and docstrings using libcst."""
    METADATA_DEPENDENCIES = (meta.PositionProvider,)

    def __init__(self):
        self.functions: dict[str, dict] = {}  # name -> {code, docstring, lineno}

    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        name = node.name.value
        code = cst.Module(body=[node]).code
        docstring = self._extract_docstring(node.body)
        pos = self.get_metadata(meta.PositionProvider, node)
        self.functions[name] = {
            "code": code,
            "docstring": docstring,
            "lineno": pos.start.line,
        }

    def _extract_docstring(self, body: cst.BaseSuite) -> Optional[str]:
        """Extract docstring from function body."""
        if isinstance(body, cst.SimpleStatementSuite):
            return None
        statements = body.body
        if statements and isinstance(statements[0], cst.SimpleStatementLine):
            first_stmt = statements[0].body[0]
            if isinstance(first_stmt, cst.Expr):
                value = first_stmt.value
                if isinstance(value, (cst.SimpleString, cst.ConcatenatedString)):
                    raw = cst.Module(body=[]).code_for_node(value)
                    # Strip quotes
                    if raw.startswith('"""') or raw.startswith("'''"):
                        return raw[3:-3].strip()
                    elif raw.startswith('"') or raw.startswith("'"):
                        return raw[1:-1].strip()
                    return raw
                elif isinstance(value, cst.FormattedString):
                    return None
        return None


def _parse_functions(source: str) -> dict[str, dict]:
    """Parse source code and return dict of function name -> info."""
    try:
        tree = cst.parse_module(source)
        wrapper = meta.MetadataWrapper(tree)
        collector = _FunctionCollector()
        wrapper.visit(collector)
        return collector.functions
    except cst.ParserSyntaxError as e:
        log_warn(f"Failed to parse source: {e}")
        return {}


def _functions_differ(code1: str, code2: str) -> bool:
    """Check if two function source codes differ (ignoring docstring changes)."""
    try:
        ast1 = ast.parse(code1)
        ast2 = ast.parse(code2)
        # Remove docstrings before comparing
        for tree in (ast1, ast2):
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if (node.body and isinstance(node.body[0], ast.Expr)
                            and isinstance(node.body[0].value, ast.Constant)
                            and isinstance(node.body[0].value.value, str)):
                        node.body = node.body[1:]
        return ast.dump(ast1) != ast.dump(ast2)
    except SyntaxError:
        return code1 != code2


def _find_test_files(repo_dir: str, func_name: str, changed_file: str | None = None) -> list[str]:
    """Find test files that likely test the given function."""
    repo_path = Path(repo_dir)
    test_files = []

    if changed_file:
        module_name = Path(changed_file).stem
        focused_candidates = [
            repo_path / "tests" / f"test_{module_name}.py",
            repo_path / "test" / f"test_{module_name}.py",
            repo_path / f"test_{module_name}.py",
            repo_path / "tests" / f"{module_name}_test.py",
        ]
        for test_file in focused_candidates:
            if not test_file.exists():
                continue
            try:
                content = test_file.read_text(encoding="utf-8", errors="ignore")
                if func_name in content:
                    return [str(test_file.relative_to(repo_path))]
            except OSError:
                continue

    # Search common test directories
    test_dirs = [
        repo_path / "tests",
        repo_path / "test",
        repo_path,
    ]

    for test_dir in test_dirs:
        if not test_dir.exists():
            continue
        for test_file in test_dir.rglob("test_*.py"):
            try:
                content = test_file.read_text(encoding="utf-8", errors="ignore")
                # Check if test file references the function
                if func_name in content:
                    rel_path = str(test_file.relative_to(repo_path))
                    if rel_path not in test_files:
                        test_files.append(rel_path)
            except OSError:
                continue

    return test_files


def extract_modified_functions(
    pre_commit_dir: str,
    post_commit_dir: str,
    changed_file: str,
    pr_info: dict,
    config: dict,
) -> list[FunctionInfo]:
    """
    So sánh file tại pre và post commit.
    Tìm hàm bị sửa (khác nhau giữa 2 phiên bản).
    Kiểm tra: hàm có docstring đủ dài không?
    Tìm test file tương ứng.
    Trả về list[FunctionInfo].
    """
    crawler_config = config.get("crawler", {})
    min_docstring_length = crawler_config.get("min_docstring_length", 30)
    require_test_files = crawler_config.get("require_test_files", True)

    pre_file = Path(pre_commit_dir) / changed_file
    post_file = Path(post_commit_dir) / changed_file

    # Read source files
    try:
        pre_source = pre_file.read_text(encoding="utf-8", errors="ignore") if pre_file.exists() else ""
        post_source = post_file.read_text(encoding="utf-8", errors="ignore") if post_file.exists() else ""
    except OSError as e:
        log_warn(f"Cannot read {changed_file}: {e}")
        return []

    if not pre_source or not post_source:
        log_debug(f"Skipping {changed_file}: missing pre or post version")
        return []

    # Parse functions from both versions
    pre_funcs = _parse_functions(pre_source)
    post_funcs = _parse_functions(post_source)

    results = []

    # Find functions that exist in both versions but have changed
    common_names = set(pre_funcs.keys()) & set(post_funcs.keys())

    for func_name in common_names:
        # Skip dunder methods
        if func_name in _SKIP_METHODS:
            log_debug(f"Skipping dunder method: {func_name}")
            continue

        pre_info = pre_funcs[func_name]
        post_info = post_funcs[func_name]

        # Check if function actually changed
        if not _functions_differ(pre_info["code"], post_info["code"]):
            log_debug(f"Skipping {func_name}: code unchanged")
            continue

        # Check docstring on POST version (fixed version should have good docs)
        docstring = post_info["docstring"] or ""
        if len(docstring) < min_docstring_length:
            log_warn(f"Skipping {func_name}: docstring too short ({len(docstring)} chars, min {min_docstring_length})")
            continue

        # Find test files
        test_files = _find_test_files(post_commit_dir, func_name, changed_file)
        if require_test_files and not test_files:
            log_warn(f"Skipping {func_name}: no test files found (require_test_files={require_test_files})")
            continue

        fn = FunctionInfo(
            name=func_name,
            repo=pr_info.get("repo", ""),
            pr_number=pr_info.get("pr_number", 0),
            docstring=docstring,
            pre_commit_code=pre_info["code"],
            post_commit_code=post_info["code"],
            pre_commit_sha=pr_info.get("pre_sha", ""),
            post_commit_sha=pr_info.get("post_sha", ""),
            file_path=changed_file,
            lineno=post_info["lineno"],
            test_files=test_files,
        )
        results.append(fn)
        log_info(f"Found modified function: {func_name} in {changed_file}")

    return results
