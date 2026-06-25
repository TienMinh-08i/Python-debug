"""Evaluate Tier 1 assertions against exported mutant function sources."""

import json
from pathlib import Path

from src.executor.runner import run_pytest
from src.crawler.repo_manager import RepoManager
from src.injector.code_util import replace_function
from src.models import AssertionResult, FunctionInfo
from src.mutation_eval.cache_loader import normalize_test_files
from src.nl2postcond.interface import wrap_assertion
from src.tracker.logger import log_info, log_warn, log_debug


def categorize_kill_rate(kill_rate: float) -> str:
    if kill_rate >= 0.8:
        return "EXCELLENT"
    if kill_rate >= 0.6:
        return "GOOD"
    if kill_rate >= 0.4:
        return "FAIR"
    return "WEAK"


def _run_assertion_on_mutant(
    func: FunctionInfo,
    mutant_source: str,
    assertion_code: str,
    test_files: list[str],
    repo_dir: str,
    config: dict,
) -> str:
    timeout = config.get("executor", {}).get("timeout_seconds", 30)
    try:
        wrapped = wrap_assertion(mutant_source, func.name, assertion_code)
    except Exception as e:
        log_warn(f"wrap_assertion failed for {func.name}: {e}")
        return "error"

    target_path = Path(repo_dir) / func.file_path
    if not target_path.exists():
        log_warn(f"Target file not found: {target_path}")
        return "error"

    original_source = target_path.read_text(encoding="utf-8", errors="ignore")
    try:
        patched_source = replace_function(original_source, func.name, wrapped)
        target_path.write_text(patched_source, encoding="utf-8")
        result = run_pytest(test_files, repo_dir, timeout=timeout)
        return result.status
    finally:
        target_path.write_text(original_source, encoding="utf-8")


def evaluate_exported_mutants(
    func: FunctionInfo,
    manifest_path: Path,
    assertions: list[AssertionResult],
    config: dict,
    verbose: bool = False,
) -> dict:
    """
    Test every Tier 1 assertion (pass or fail on pre/post) against each exported mutant.

    Killed = assertion status "fail" on mutant (AssertionError — caught behavioral change).
    """
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)

    export_dir = manifest_path.parent
    repo_dir = Path(
        RepoManager(func.repo, config.get("cache_dir", ".cache"))
        .get_post_commit_dir(func.post_commit_sha)
    )
    if not repo_dir.is_absolute():
        repo_dir = (Path.cwd() / repo_dir).resolve()

    if not repo_dir.exists():
        raise FileNotFoundError(
            f"Clone directory not found: {repo_dir}. Run Tier 1 pipeline first."
        )

    test_files = normalize_test_files(func.test_files, repo_dir)
    if not test_files:
        log_warn(f"No test files found under {repo_dir} for {func.name}")

    func_assertions = [a for a in assertions if a.function_name == func.name]
    if not func_assertions:
        log_warn(f"No assertions in cache for {func.name}")
        return {
            "function_name": func.name,
            "total_assertions": 0,
            "total_mutants": 0,
            "assertions": [],
        }

    mutant_entries = manifest.get("mutants", [])
    log_info(
        f"Evaluating {len(func_assertions)} assertion(s) × "
        f"{len(mutant_entries)} mutant(s) for {func.name}"
    )

    assertion_stats: dict[str, dict] = {}
    for assertion in func_assertions:
        assertion_stats[assertion.assertion_code] = {
            "killed": 0,
            "survived": 0,
            "errors": 0,
            "pre_status": assertion.pre_status,
            "post_status": assertion.post_status,
            "is_correct": assertion.is_correct,
        }

    for entry in mutant_entries:
        mutant_file = export_dir / entry["file"]
        if not mutant_file.exists():
            log_warn(f"Missing mutant file: {mutant_file}")
            for code in assertion_stats:
                assertion_stats[code]["errors"] += 1
            continue

        with open(mutant_file, encoding="utf-8") as f:
            mutant_data = json.load(f)

        mutant_id = mutant_data["id"]
        mutant_source = mutant_data["function_source"]

        for assertion in func_assertions:
            code = assertion.assertion_code
            try:
                status = _run_assertion_on_mutant(
                    func,
                    mutant_source,
                    code,
                    test_files,
                    str(repo_dir),
                    config,
                )
            except Exception as e:
                log_warn(f"Error mutant {mutant_id} assertion: {e}")
                assertion_stats[code]["errors"] += 1
                continue

            if status == "fail":
                assertion_stats[code]["killed"] += 1
                if verbose:
                    log_debug(f"  mutant {mutant_id}: KILLED")
            elif status in ("error", "timeout"):
                assertion_stats[code]["errors"] += 1
            else:
                assertion_stats[code]["survived"] += 1
                if verbose:
                    log_debug(f"  mutant {mutant_id}: SURVIVED")

    assertion_results = []
    for assertion in func_assertions:
        code = assertion.assertion_code
        stats = assertion_stats[code]
        denom = stats["killed"] + stats["survived"]
        kill_rate = stats["killed"] / denom if denom > 0 else 0.0
        assertion_results.append({
            "assertion_code": code,
            "pre_status": stats["pre_status"],
            "post_status": stats["post_status"],
            "is_correct": stats["is_correct"],
            "killed": stats["killed"],
            "survived": stats["survived"],
            "errors": stats["errors"],
            "kill_rate": round(kill_rate, 4),
            "category": categorize_kill_rate(kill_rate),
        })

    return {
        "function_name": func.name,
        "total_assertions": len(func_assertions),
        "total_mutants": len(mutant_entries),
        "assertions": assertion_results,
    }
