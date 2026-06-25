"""PR Crawler — fetch PRs from GitHub API and filter candidates."""

import os
from typing import Optional

from github import Github, Auth
from github.GithubException import GithubException

from src.tracker.logger import log_info, log_warn, log_error, log_debug


# Directories that indicate non-source changes (docs/tests only)
_DOCS_TEST_PREFIXES = ("docs/", "doc/", "test/", "tests/", "examples/")


def _get_github_client() -> Github:
    """Create a PyGithub client using GITHUB_TOKEN env var."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        log_warn(
            "⚠️  GITHUB_TOKEN not set — API rate limits will be VERY LOW (60 req/h)\n"
            "    Set it: export GITHUB_TOKEN='ghp_your_token_here'\n"
            "    Or (Windows): set GITHUB_TOKEN=ghp_your_token_here\n"
            "    Get token: https://github.com/settings/tokens (classic, public_repo scope)"
        )
        return Github()
    return Github(auth=Auth.Token(token))


def get_pr_by_number(repo_name: str, pr_number: int, config: dict) -> dict | None:
    """
    Fetch a specific PR by number (direct, without crawling all PRs).
    More efficient when specific PR numbers are known.

    Returns PR info dict or None if PR not valid (not merged, no Python files, etc).
    Raises no exception — returns None on any error.
    """
    github = _get_github_client()

    try:
        repo = github.get_repo(repo_name)
        pr = repo.get_pull(pr_number)

        # Check if merged
        if not pr.merged:
            log_debug(f"PR #{pr_number}: not merged, skipping")
            return None

        log_debug(f"Checking PR #{pr_number}: {pr.title}")

        # Get changed files
        try:
            files = pr.get_files()
            changed_py_files = []
            has_only_docs_tests = True

            for f in files:
                if not f.filename.endswith(".py"):
                    continue
                changed_py_files.append(f.filename)
                if not any(f.filename.startswith(p) for p in _DOCS_TEST_PREFIXES):
                    has_only_docs_tests = False

            # Skip if no Python files changed
            if not changed_py_files:
                log_debug(f"  PR #{pr_number}: no Python files changed, skipping")
                return None

            # Skip if only docs/tests changed
            if has_only_docs_tests:
                log_debug(f"  PR #{pr_number}: only docs/tests changed, skipping")
                return None

            # Get SHAs
            pre_sha = pr.base.sha
            post_sha = pr.merge_commit_sha

            if not post_sha:
                log_debug(f"  PR #{pr_number}: no merge commit SHA, skipping")
                return None

            candidate = {
                "pr_number": pr.number,
                "title": pr.title,
                "pre_sha": pre_sha,
                "post_sha": post_sha,
                "changed_files": changed_py_files,
            }
            log_info(f"  Candidate PR #{pr.number}: {pr.title} "
                     f"({len(changed_py_files)} Python files)")
            return candidate

        except GithubException as e:
            log_warn(f"  Error processing PR #{pr_number}: {e}")
            return None

    except GithubException as e:
        log_error(f"Failed to fetch PR #{pr_number} from {repo_name}: {e}")
        return None
    except Exception as e:
        # Catch rate limit or other unexpected errors
        log_error(f"Unexpected error fetching PR #{pr_number}: {e}")
        return None


def get_candidate_prs(repo_name: str, config: dict) -> list[dict]:
    """
    Trả về list PR info, mỗi item gồm:
    {
        "pr_number": int,
        "title": str,
        "pre_sha": str,   # commit SHA trước khi merge
        "post_sha": str,  # commit SHA sau khi merge
        "changed_files": [str],  # chỉ .py files
    }

    Filter: chỉ lấy PR thỏa mãn:
    - Chỉ sửa Python files
    - PR đã được merged (không phải open/closed)
    - Không phải chỉ sửa docs/tests
    """
    crawler_config = config.get("crawler", {})
    max_prs = crawler_config.get("max_prs", 50)

    github = _get_github_client()

    try:
        repo = github.get_repo(repo_name)
    except GithubException as e:
        log_error(f"Failed to access repo {repo_name}: {e}")
        return []

    log_info(f"Fetching merged PRs from {repo_name} (max: {max_prs})...")

    candidates = []
    # Get closed PRs sorted by recently updated, then filter merged
    pulls = repo.get_pulls(state="closed", sort="updated", direction="desc")

    checked = 0
    for pr in pulls:
        if len(candidates) >= max_prs:
            break

        try:
            # Skip unmerged PRs (this can trigger lazy fetch → RateLimitExceededException)
            if not pr.merged:
                continue

            checked += 1
            log_debug(f"Checking PR #{pr.number}: {pr.title}")

            # Get changed files
            files = pr.get_files()
            changed_py_files = []
            has_only_docs_tests = True

            for f in files:
                if not f.filename.endswith(".py"):
                    continue
                changed_py_files.append(f.filename)
                # Check if at least one file is NOT docs/tests
                if not any(f.filename.startswith(p) for p in _DOCS_TEST_PREFIXES):
                    has_only_docs_tests = False

            # Skip if no Python files changed
            if not changed_py_files:
                log_debug(f"  PR #{pr.number}: no Python files changed, skipping")
                continue

            # Skip if only docs/tests changed
            if has_only_docs_tests:
                log_debug(f"  PR #{pr.number}: only docs/tests changed, skipping")
                continue

            # Get pre and post commit SHAs
            # pre_sha = base commit (before merge)
            # post_sha = merge commit (after merge)
            pre_sha = pr.base.sha
            post_sha = pr.merge_commit_sha

            if not post_sha:
                log_debug(f"  PR #{pr.number}: no merge commit SHA, skipping")
                continue

            candidate = {
                "pr_number": pr.number,
                "title": pr.title,
                "pre_sha": pre_sha,
                "post_sha": post_sha,
                "changed_files": changed_py_files,
            }
            candidates.append(candidate)
            log_info(f"  Candidate PR #{pr.number}: {pr.title} "
                     f"({len(changed_py_files)} Python files)")

        except GithubException as e:
            # Catch any GitHub API errors, including RateLimitExceeded
            log_warn(f"  Error processing PR: {e} — stopping crawl")
            if "403" in str(e) or "rate" in str(e).lower():
                log_warn(f"  Rate limit exceeded. Try setting GITHUB_TOKEN env var.")
            break

    log_info(f"Found {len(candidates)} candidate PRs out of {checked} merged PRs checked")
    return candidates


def filter_has_docstring_and_test(pr_info: dict, repo_dir: str) -> bool:
    """
    Kiểm tra PR có hàm bị sửa với docstring + test file không.

    Đọc các changed files trong repo_dir, kiểm tra:
    - File có chứa hàm với docstring không
    - Có file test tương ứng trong repo không
    """
    import ast
    from pathlib import Path

    changed_files = pr_info.get("changed_files", [])
    repo_path = Path(repo_dir)

    for changed_file in changed_files:
        # Skip test files themselves
        if any(changed_file.startswith(p) for p in _DOCS_TEST_PREFIXES):
            continue

        file_path = repo_path / changed_file
        if not file_path.exists():
            continue

        try:
            source = file_path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue

        # Check for functions with docstrings
        has_docstring_func = False
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                docstring = ast.get_docstring(node)
                if docstring and len(docstring) > 0:
                    has_docstring_func = True
                    break

        if not has_docstring_func:
            continue

        # Check for corresponding test file
        # Look for test files that might test this module
        module_name = Path(changed_file).stem
        test_patterns = [
            f"test_{module_name}.py",
            f"tests/test_{module_name}.py",
            f"test/test_{module_name}.py",
            f"tests/{module_name}_test.py",
        ]
        for pattern in test_patterns:
            if (repo_path / pattern).exists():
                return True

    return False
