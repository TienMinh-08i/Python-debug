"""Load Tier 1 cache (assertions + functions) for mutation evaluation."""

import json
from pathlib import Path

from src.models import AssertionResult, FunctionInfo
from src.tracker.logger import log_warn


def deserialize_assertion_result(data: dict) -> AssertionResult:
    return AssertionResult(
        function_name=data["function_name"],
        assertion_code=data["assertion_code"],
        pre_status=data["pre_status"],
        post_status=data["post_status"],
        pre_error_msg=data.get("pre_error_msg", ""),
        post_error_msg=data.get("post_error_msg", ""),
    )


def deserialize_function_info(data: dict) -> FunctionInfo:
    return FunctionInfo(
        name=data["name"],
        repo=data["repo"],
        pr_number=data["pr_number"],
        docstring=data["docstring"],
        pre_commit_code=data["pre_commit_code"],
        post_commit_code=data["post_commit_code"],
        pre_commit_sha=data["pre_commit_sha"],
        post_commit_sha=data["post_commit_sha"],
        file_path=data["file_path"],
        lineno=data["lineno"],
        test_files=data.get("test_files", []),
    )


def normalize_test_files(test_files: list[str], repo_dir: Path) -> list[str]:
    """Resolve test paths for the current OS; dedupe while preserving order."""
    seen: set[str] = set()
    resolved: list[str] = []

    for tf in test_files:
        tf_norm = tf.replace("\\", "/")
        path = Path(tf_norm)
        if not path.is_absolute():
            path = repo_dir / path
        path = path.resolve()
        key = str(path)
        if key in seen:
            continue
        if path.exists():
            seen.add(key)
            resolved.append(key)

    return resolved


def load_pr_cache(
    cache_dir: str,
    owner: str,
    repo_name: str,
    pr_number: int,
) -> tuple[list[AssertionResult], list[FunctionInfo]]:
    """Load all tier1 assertions and functions.json for one PR."""
    pr_dir = Path(cache_dir) / "results" / owner / repo_name / str(pr_number)
    assertions: list[AssertionResult] = []
    functions: list[FunctionInfo] = []

    if not pr_dir.exists():
        return assertions, functions

    for tier1_file in sorted(pr_dir.glob("tier1_*.json")):
        try:
            with open(tier1_file, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                for item in data:
                    assertions.append(deserialize_assertion_result(item))
        except (json.JSONDecodeError, OSError) as e:
            log_warn(f"Failed to load {tier1_file}: {e}")

    functions_file = pr_dir / "functions.json"
    if functions_file.exists():
        try:
            with open(functions_file, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                for item in data:
                    functions.append(deserialize_function_info(item))
        except (json.JSONDecodeError, OSError) as e:
            log_warn(f"Failed to load {functions_file}: {e}")

    return assertions, functions


def auto_discover_prs(cache_dir: str, owner: str, repo_name: str) -> list[int]:
    repo_cache = Path(cache_dir) / "results" / owner / repo_name
    pr_numbers: list[int] = []
    if not repo_cache.exists():
        return pr_numbers

    for d in repo_cache.iterdir():
        if d.is_dir() and d.name.isdigit():
            has_tier1 = any(
                f.name.startswith("tier1_") and f.name.endswith(".json")
                for f in d.iterdir()
                if f.is_file()
            )
            if has_tier1:
                pr_numbers.append(int(d.name))

    return sorted(pr_numbers)
