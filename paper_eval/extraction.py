from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import libcst as cst
import libcst.metadata as meta

from .paths import ensure_project_paths
from .schemas import ExclusionRecord

ensure_project_paths()

from src.models import FunctionInfo  # noqa: E402


SKIP_METHODS = {"__init__", "__str__", "__repr__", "__eq__"}
DOCS_TEST_PREFIXES = ("docs/", "doc/", "test/", "tests/", "examples/")


class _FunctionCollector(cst.CSTVisitor):
    METADATA_DEPENDENCIES = (meta.PositionProvider,)

    def __init__(self) -> None:
        self.functions: dict[str, dict[str, Any]] = {}

    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        name = node.name.value
        code = cst.Module(body=[node]).code
        docstring = self._extract_docstring(node.body)
        pos = self.get_metadata(meta.PositionProvider, node)
        self.functions[name] = {"code": code, "docstring": docstring, "lineno": pos.start.line}

    def _extract_docstring(self, body: cst.BaseSuite) -> str | None:
        if isinstance(body, cst.SimpleStatementSuite):
            return None
        statements = body.body
        if not statements or not isinstance(statements[0], cst.SimpleStatementLine):
            return None
        first_stmt = statements[0].body[0]
        if not isinstance(first_stmt, cst.Expr):
            return None
        value = first_stmt.value
        if isinstance(value, (cst.SimpleString, cst.ConcatenatedString)):
            raw = cst.Module(body=[]).code_for_node(value)
            if raw.startswith('"""') or raw.startswith("'''"):
                return raw[3:-3].strip()
            if raw.startswith('"') or raw.startswith("'"):
                return raw[1:-1].strip()
            return raw
        return None


def _parse_functions(source: str) -> dict[str, dict[str, Any]]:
    try:
        tree = cst.parse_module(source)
        wrapper = meta.MetadataWrapper(tree)
        collector = _FunctionCollector()
        wrapper.visit(collector)
        return collector.functions
    except cst.ParserSyntaxError:
        return {}


def _functions_differ(code1: str, code2: str) -> bool:
    try:
        ast1 = ast.parse(code1)
        ast2 = ast.parse(code2)
        for tree in (ast1, ast2):
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if (
                        node.body
                        and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant)
                        and isinstance(node.body[0].value.value, str)
                    ):
                        node.body = node.body[1:]
        return ast.dump(ast1) != ast.dump(ast2)
    except SyntaxError:
        return code1 != code2


def _find_test_files(repo_dir: str, func_name: str, changed_file: str | None = None) -> list[str]:
    repo_path = Path(repo_dir)
    test_files: list[str] = []

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
            except OSError:
                continue
            if func_name in content:
                return [str(test_file.relative_to(repo_path))]

    for test_dir in (repo_path / "tests", repo_path / "test", repo_path):
        if not test_dir.exists():
            continue
        for test_file in test_dir.rglob("test_*.py"):
            try:
                content = test_file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if func_name in content:
                rel_path = str(test_file.relative_to(repo_path))
                if rel_path not in test_files:
                    test_files.append(rel_path)
    return test_files


def _exclude(
    exclusions: list[ExclusionRecord],
    run_id: str,
    repo: str,
    pr_info: dict[str, Any],
    file_path: str | None,
    function_name: str | None,
    reason: str,
    detail: str = "",
) -> None:
    exclusions.append(
        ExclusionRecord(
            run_id=run_id,
            project=repo,
            pr_number=int(pr_info.get("pr_number", 0)),
            pr_title=str(pr_info.get("title", "")),
            file_path=file_path,
            function_name=function_name,
            reason=reason,
            detail=detail,
        )
    )


def extract_modified_functions_with_exclusions(
    run_id: str,
    repo: str,
    pre_commit_dir: str,
    post_commit_dir: str,
    changed_file: str,
    pr_info: dict[str, Any],
    config: dict[str, Any],
) -> tuple[list[FunctionInfo], list[ExclusionRecord]]:
    crawler_config = config.get("crawler", {})
    min_docstring_length = int(crawler_config.get("min_docstring_length", 30))
    require_test_files = bool(crawler_config.get("require_test_files", True))
    exclusions: list[ExclusionRecord] = []

    if not changed_file.endswith(".py"):
        _exclude(exclusions, run_id, repo, pr_info, changed_file, None, "non_python_file")
        return [], exclusions
    if any(changed_file.startswith(prefix) for prefix in DOCS_TEST_PREFIXES):
        _exclude(exclusions, run_id, repo, pr_info, changed_file, None, "docs_or_tests_only_file")
        return [], exclusions

    pre_file = Path(pre_commit_dir) / changed_file
    post_file = Path(post_commit_dir) / changed_file
    if not pre_file.exists() or not post_file.exists():
        _exclude(exclusions, run_id, repo, pr_info, changed_file, None, "missing_pre_or_post_file")
        return [], exclusions

    try:
        pre_source = pre_file.read_text(encoding="utf-8", errors="ignore")
        post_source = post_file.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        _exclude(exclusions, run_id, repo, pr_info, changed_file, None, "source_read_error", str(exc))
        return [], exclusions

    pre_funcs = _parse_functions(pre_source)
    post_funcs = _parse_functions(post_source)
    if not pre_funcs or not post_funcs:
        _exclude(exclusions, run_id, repo, pr_info, changed_file, None, "parse_failed_or_no_functions")
        return [], exclusions

    results: list[FunctionInfo] = []
    common_names = sorted(set(pre_funcs) & set(post_funcs))
    if not common_names:
        _exclude(exclusions, run_id, repo, pr_info, changed_file, None, "no_common_functions")
        return [], exclusions

    for func_name in common_names:
        if func_name in SKIP_METHODS:
            _exclude(exclusions, run_id, repo, pr_info, changed_file, func_name, "unsupported_dunder_method")
            continue

        pre_info = pre_funcs[func_name]
        post_info = post_funcs[func_name]
        if not _functions_differ(pre_info["code"], post_info["code"]):
            _exclude(exclusions, run_id, repo, pr_info, changed_file, func_name, "function_unchanged")
            continue

        docstring = post_info.get("docstring") or ""
        if len(docstring) < min_docstring_length:
            _exclude(
                exclusions,
                run_id,
                repo,
                pr_info,
                changed_file,
                func_name,
                "docstring_too_short",
                f"length={len(docstring)}, min={min_docstring_length}",
            )
            continue

        test_files = _find_test_files(post_commit_dir, func_name, changed_file)
        if require_test_files and not test_files:
            _exclude(exclusions, run_id, repo, pr_info, changed_file, func_name, "no_linked_tests")
            continue

        results.append(
            FunctionInfo(
                name=func_name,
                repo=repo,
                pr_number=int(pr_info.get("pr_number", 0)),
                docstring=docstring,
                pre_commit_code=pre_info["code"],
                post_commit_code=post_info["code"],
                pre_commit_sha=str(pr_info.get("pre_sha", "")),
                post_commit_sha=str(pr_info.get("post_sha", "")),
                file_path=changed_file,
                lineno=int(post_info["lineno"]),
                test_files=test_files,
            )
        )

    return results, exclusions

