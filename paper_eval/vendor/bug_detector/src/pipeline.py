"""Pipeline orchestrator for Tier 1 â€” correctness evaluation."""

import json
import shutil
from pathlib import Path
from dataclasses import asdict

from src.models import FunctionInfo, AssertionResult, ExecResult
from src.tracker.cache import (
    load_cache, save_cache, cache_exists,
    load_functions_cache, save_functions_cache,
)
from src.tracker.logger import log_info, log_warn, log_error, log_debug
from src.tracker.output_manager import OutputManager
from src.crawler.repo_manager import RepoManager
from src.crawler.pr_crawler import get_candidate_prs
from src.crawler.function_extractor import extract_modified_functions
from src.nl2postcond.interface import generate_assertions, wrap_assertion
from src.executor.runner import run_pytest
from src.injector.code_util import replace_function


def _run_pytest_with_test_overlay(
    test_files: list[str],
    repo_dir: str,
    *,
    timeout: int,
    test_source_dir: str | None = None,
) -> ExecResult:
    if not test_source_dir or Path(test_source_dir).resolve() == Path(repo_dir).resolve():
        return run_pytest(test_files, repo_dir, timeout=timeout)

    backups: dict[Path, str | None] = {}
    try:
        for test_file in test_files:
            source = Path(test_source_dir) / test_file
            target = Path(repo_dir) / test_file
            if not source.exists():
                continue
            backups[target] = target.read_text(encoding="utf-8", errors="ignore") if target.exists() else None
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        return run_pytest(test_files, repo_dir, timeout=timeout)
    finally:
        for target, original in backups.items():
            if original is None:
                try:
                    target.unlink()
                except FileNotFoundError:
                    pass
            else:
                target.write_text(original, encoding="utf-8")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_config(config_path: str = "configs/config.json") -> dict:
    """Load config.json from disk."""
    path = Path(config_path)
    if not path.exists():
        log_warn(f"Config not found at {config_path}, using defaults")
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _run_assertion_on_version(
    func: FunctionInfo,
    func_code: str,
    assertion: str,
    repo_dir: str,
    config: dict,
    test_source_dir: str | None = None,
) -> str:
    """
    Wrap func_code vá»›i assertion theo pattern nl2postcond rá»“i cháº¡y.
    Returns: 'pass' | 'fail' | 'error' | 'timeout'

    DÃ¹ng wrap_assertion() tá»« nl2postcond/interface.py:
    - Rename func â†’ func_original
    - Táº¡o wrapper func() gá»i func_original(), lÆ°u káº¿t quáº£ vÃ o `return_value`
    - Cháº¡y assertion `assert return_value ...` á»Ÿ trong wrapper
    - Test file gá»i wrapper func() nhÆ° bÃ¬nh thÆ°á»ng
    """
    timeout = config.get("executor", {}).get("timeout_seconds", 30)
    target_path = Path(repo_dir) / func.file_path
    if not target_path.exists():
        log_warn(f"  Target file not found: {target_path}")
        return "error"

    original_source = target_path.read_text(encoding="utf-8", errors="ignore")
    try:
        wrapped = wrap_assertion(func_code, func.name, assertion)
        patched_source = replace_function(original_source, func.name, wrapped)
        target_path.write_text(patched_source, encoding="utf-8")
        result: ExecResult = _run_pytest_with_test_overlay(
            func.test_files,
            repo_dir,
            timeout=timeout,
            test_source_dir=test_source_dir,
        )
        return result.status
    except Exception as e:
        log_warn(f"  Assertion evaluation failed for {func.name}: {e}")
        return "error"
    finally:
        try:
            target_path.write_text(original_source, encoding="utf-8")
        except OSError as e:
            log_error(f"  Failed to restore {target_path}: {e}")


def _evaluate_assertion(
    func: FunctionInfo,
    assertion: str,
    pre_dir: str,
    post_dir: str,
    config: dict,
) -> AssertionResult:
    """
    Evaluate one assertion on both pre and post commit versions.
    Returns AssertionResult.
    """
    pre_status = _run_assertion_on_version(
        func, func.pre_commit_code, assertion,
        pre_dir, config,
    )
    post_status = _run_assertion_on_version(
        func, func.post_commit_code, assertion,
        post_dir, config,
    )

    return AssertionResult(
        function_name=func.name,
        assertion_code=assertion,
        pre_status=pre_status,
        post_status=post_status,
    )


def _process_function(
    func: FunctionInfo,
    pre_dir: str,
    post_dir: str,
    config: dict,
    func_idx: int,
    total_funcs: int,
    output_manager: OutputManager = None,
) -> list[AssertionResult]:
    """
    Full Tier 1 flow for a single function.
    Returns list of AssertionResult (one per assertion).
    """
    log_info(f"[{func_idx}/{total_funcs}] Processing function '{func.name}' "
             f"in PR #{func.pr_number} ({func.repo})")

    results = []

    # Step 1: check cache for tier1 results
    cache_key = f"tier1_{func.name}"
    cached = load_cache(func.repo, func.pr_number, cache_key)
    if cached is not None:
        log_info(f"  Cache hit for {func.name} â€” skipping LLM call")
        for item in cached:
            results.append(AssertionResult(**item))
        
        # Even on cache hit, save output files
        if output_manager and results:
            try:
                # Extract assertions from cached results
                assertions = [r.assertion_code for r in results]
                
                # Save generated assertions to .py file (all of them)
                assertions_file = output_manager.save_generated_assertions(
                    repo=func.repo,
                    pr_number=func.pr_number,
                    function_name=func.name,
                    docstring=func.docstring,
                    assertions=assertions,
                    function_code=func.post_commit_code
                )
                log_debug(f"  Saved generated assertions to {assertions_file}")
                
                # Save injected code files - use ONLY FIRST assertion (like nl2postcond)
                best_assertion = [assertions[0]] if assertions else []

                # Truyá»n code Gá»C â€” output_manager sáº½ tá»± gá»i wrap_assertion()
                output_manager.save_injected_function_pre(
                    repo=func.repo,
                    pr_number=func.pr_number,
                    function_name=func.name,
                    pre_commit_code=func.pre_commit_code,
                    assertions=best_assertion
                )

                output_manager.save_injected_function_post(
                    repo=func.repo,
                    pr_number=func.pr_number,
                    function_name=func.name,
                    post_commit_code=func.post_commit_code,
                    assertions=best_assertion
                )
                
                # Save tier1 summary
                output_manager.save_tier1_summary(
                    repo=func.repo,
                    pr_number=func.pr_number,
                    function_name=func.name,
                    tier1_results=[asdict(r) for r in results]
                )
                
                log_debug(f"  Saved injected code and tier1 summary for {func.name}")
            except Exception as e:
                log_warn(f"  Failed to save output files: {e}")
        
        return results

    # Step 2: generate assertions via LLM
    log_info(f"  Generating assertions for {func.name} ...")
    assertions = generate_assertions(
        docstring=func.docstring,
        function_code=func.post_commit_code,
        config=config,
    )

    if not assertions:
        log_warn(f"  No assertions generated for {func.name}, skipping")
        return []

    log_info(f"  {len(assertions)} assertion(s) generated")

    # Save generated assertions to .py file
    if output_manager:
        try:
            assertions_file = output_manager.save_generated_assertions(
                repo=func.repo,
                pr_number=func.pr_number,
                function_name=func.name,
                docstring=func.docstring,
                assertions=assertions,
                function_code=func.post_commit_code
            )
            log_debug(f"  Saved generated assertions to {assertions_file}")
        except Exception as e:
            log_warn(f"  Failed to save generated assertions: {e}")

    # Step 3: evaluate each assertion on pre and post commit
    for i, assertion in enumerate(assertions, 1):
        log_info(f"  [{i}/{len(assertions)}] Evaluating: {assertion[:60]}...")
        try:
            ar = _evaluate_assertion(func, assertion, pre_dir, post_dir, config)
            results.append(ar)
            log_info(f"    pre={ar.pre_status}  post={ar.post_status}  "
                     f"correct={ar.is_correct}")
        except Exception as e:
            log_error(f"  Error evaluating assertion for {func.name}: {e}")
            continue

    # Save injected code files
    if output_manager and results:
        try:
            # Use ONLY FIRST assertion for injection (like nl2postcond)
            best_assertion = [assertions[0]] if assertions else []

            # Truyá»n code Gá»C â€” output_manager sáº½ tá»± gá»i wrap_assertion()
            # KHÃ”NG dÃ¹ng inject_into_file() Ä‘á»ƒ trÃ¡nh double-injection
            output_manager.save_injected_function_pre(
                repo=func.repo,
                pr_number=func.pr_number,
                function_name=func.name,
                pre_commit_code=func.pre_commit_code,
                assertions=best_assertion
            )

            output_manager.save_injected_function_post(
                repo=func.repo,
                pr_number=func.pr_number,
                function_name=func.name,
                post_commit_code=func.post_commit_code,
                assertions=best_assertion
            )
            
            # Save tier1 summary
            output_manager.save_tier1_summary(
                repo=func.repo,
                pr_number=func.pr_number,
                function_name=func.name,
                tier1_results=[asdict(r) for r in results]
            )
            
            log_debug(f"  Saved injected code and tier1 summary for {func.name}")
        except Exception as e:
            log_warn(f"  Failed to save injected code: {e}")

    # Step 4: cache results
    if results:
        save_cache(
            func.repo, func.pr_number, cache_key,
            [asdict(r) for r in results],
        )

    return results


def _process_pr(
    repo: str,
    pr_info: dict,
    repo_manager: RepoManager,
    config: dict,
    pr_idx: int,
    total_prs: int,
) -> list[FunctionInfo]:
    """
    Clone repo at pre/post SHA, extract modified functions.
    Returns list[FunctionInfo] (cached if available).
    """
    pr_number = pr_info["pr_number"]
    log_info(f"[PR {pr_idx}/{total_prs}] PR #{pr_number}: {pr_info.get('title', '')}")

    # Check functions cache
    cached_funcs = load_functions_cache(repo, pr_number)
    if cached_funcs is not None:
        log_info(f"  Functions cache hit â€” {len(cached_funcs)} function(s)")
        funcs = []
        for d in cached_funcs:
            funcs.append(FunctionInfo(**d))
        return funcs

    # Clone / checkout
    try:
        pre_dir = repo_manager.get_pre_commit_dir(pr_info["pre_sha"])
        post_dir = repo_manager.get_post_commit_dir(pr_info["post_sha"])
    except Exception as e:
        log_error(f"  Failed to checkout repo for PR #{pr_number}: {e}")
        return []

    # Extract functions from each changed file
    all_funcs: list[FunctionInfo] = []
    for changed_file in pr_info.get("changed_files", []):
        if not changed_file.endswith(".py"):
            continue
        try:
            # Inject repo name into pr_info for FunctionInfo.repo field
            pr_info_with_repo = {**pr_info, "repo": repo}
            funcs = extract_modified_functions(
                pre_commit_dir=pre_dir,
                post_commit_dir=post_dir,
                changed_file=changed_file,
                pr_info=pr_info_with_repo,
                config=config,
            )
            if funcs:
                log_info(f"    Extracted {len(funcs)} function(s) from {changed_file}")
            else:
                log_debug(f"    No valid functions extracted from {changed_file}")
            all_funcs.extend(funcs)
        except Exception as e:
            log_error(f"  Error extracting from {changed_file}: {e}")
            import traceback
            log_debug(traceback.format_exc())
            continue

    log_info(f"  Extracted {len(all_funcs)} function(s) from PR #{pr_number}")

    # Cache extracted functions
    if all_funcs:
        save_functions_cache(repo, pr_number, [asdict(f) for f in all_funcs])

    return all_funcs


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_tier1(
    repo: str,
    pr_numbers: list[int],
    config: dict,
) -> list[AssertionResult]:
    """
    Full Tier 1 pipeline:
    1. Vá»›i má»—i PR: load cache hoáº·c crawl
    2. Extract functions
    3. Generate assertions
    4. Inject + run trÃªn pre vÃ  post commit
    5. ÄÃ¡nh giÃ¡ correctness
    6. LÆ°u cache
    7. Tráº£ vá» táº¥t cáº£ AssertionResult
    """
    cache_dir = config.get("cache_dir", ".cache")
    repo_manager = RepoManager(repo, cache_dir)
    
    # Initialize OutputManager for saving readable .py files
    output_dir = config.get("output_dir", "data/outputs")
    output_manager = OutputManager(output_dir)

    # -----------------------------------------------------------------------
    # Resolve PR list: either explicit numbers or crawl from GitHub
    # -----------------------------------------------------------------------
    if pr_numbers:
        # Fetch each PR directly (more efficient than crawling all)
        log_info(f"Fetching info for {len(pr_numbers)} specified PRs ...")
        from src.crawler.pr_crawler import get_pr_by_number
        pr_infos = []
        for n in pr_numbers:
            pr_info = get_pr_by_number(repo, n, config)
            if pr_info:
                pr_infos.append(pr_info)
            else:
                log_warn(f"PR #{n} not found or not a valid candidate, skipping")
    else:
        log_info(f"Crawling PRs from {repo} ...")
        pr_infos = get_candidate_prs(repo, config)

    if not pr_infos:
        log_warn("No PRs to process")
        return []

    log_info(f"Processing {len(pr_infos)} PR(s) from {repo}")

    # -----------------------------------------------------------------------
    # Process each PR
    # -----------------------------------------------------------------------
    all_results: list[AssertionResult] = []
    all_funcs: list[FunctionInfo] = []

    for pr_idx, pr_info in enumerate(pr_infos, 1):
        try:
            funcs = _process_pr(
                repo=repo,
                pr_info=pr_info,
                repo_manager=repo_manager,
                config=config,
                pr_idx=pr_idx,
                total_prs=len(pr_infos),
            )
            all_funcs.extend(funcs)
        except Exception as e:
            log_error(f"Error processing PR #{pr_info.get('pr_number')}: {e}")
            continue

    # -----------------------------------------------------------------------
    # Process each function
    # -----------------------------------------------------------------------
    total_funcs = len(all_funcs)
    log_info(f"Total functions to evaluate: {total_funcs}")

    # We need pre/post dirs per function; store them on the object via pr_info cache
    # Re-open repo_manager checkouts for each function
    pr_dirs: dict[int, tuple[str, str]] = {}  # pr_number -> (pre_dir, post_dir)

    for func_idx, func in enumerate(all_funcs, 1):
        try:
            # Resolve checkout dirs (re-checkout only if not already done)
            if func.pr_number not in pr_dirs:
                pr_dirs[func.pr_number] = (
                    repo_manager.get_pre_commit_dir(func.pre_commit_sha),
                    repo_manager.get_post_commit_dir(func.post_commit_sha),
                )
            pre_dir, post_dir = pr_dirs[func.pr_number]

            results = _process_function(
                func=func,
                pre_dir=pre_dir,
                post_dir=post_dir,
                config=config,
                func_idx=func_idx,
                total_funcs=total_funcs,
                output_manager=output_manager,
            )
            all_results.extend(results)
        except Exception as e:
            log_error(f"Error processing function '{func.name}': {e}")
            continue

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    correct = sum(1 for r in all_results if r.is_correct)
    total = len(all_results)
    rate = correct / total if total > 0 else 0.0
    log_info(
        f"Tier 1 complete â€” {total} assertion(s) evaluated, "
        f"{correct} correct ({rate:.1%} correctness rate)"
    )

    return all_results


def run_tier2(
    tier1_results: list[AssertionResult],
    all_funcs: list[FunctionInfo],
    config: dict,
    output_manager: OutputManager = None,
) -> list:
    """
    Tier 2 mutation testing pipeline.
    
    Only process functions that have at least 1 correct assertion from tier 1.
    For each such function:
      1. Create isolated workspace
      2. Run mutmut to generate mutants
      3. Check which assertions kill which mutants
      4. Return MutationResult
    
    Returns: list[MutationResult]
    """
    from src.mutator.workspace import MutantWorkspace
    from src.mutator.mutmut_runner import run_mutmut
    from src.mutation_eval.kill_checker import check_kills_for_function
    from src.models import MutationResult
    
    # Group tier1 results by function name and keep track of which are correct
    correct_by_func: dict[str, list[str]] = {}
    for result in tier1_results:
        if result.is_correct:
            if result.function_name not in correct_by_func:
                correct_by_func[result.function_name] = []
            correct_by_func[result.function_name].append(result.assertion_code)
    
    if not correct_by_func:
        log_warn("No functions with correct assertions found for Tier 2")
        return []
    
    log_info(f"Tier 2: Processing {len(correct_by_func)} functions with correct assertions")
    
    mutation_results: list[MutationResult] = []
    cache_dir = config.get("cache_dir", ".cache")
    post_dirs: dict[tuple[str, str], str] = {}
    
    for func in all_funcs:
        if func.name not in correct_by_func:
            continue
        
        correct_assertions = correct_by_func[func.name]
        
        try:
            log_info(f"Tier 2: {func.name} ({len(correct_assertions)} correct assertions)")

            post_key = (func.repo, func.post_commit_sha)
            if post_key not in post_dirs:
                post_dirs[post_key] = RepoManager(func.repo, cache_dir).get_post_commit_dir(
                    func.post_commit_sha
                )
            
            # Create workspace
            workspace = MutantWorkspace(
                post_commit_dir=post_dirs[post_key],
                cache_dir=cache_dir,
                repo=func.repo,
                pr=func.pr_number,
            )
            
            with workspace as workspace_dir:
                # Run mutmut to generate mutants
                mutant_ids = run_mutmut(
                    workspace_dir=workspace_dir,
                    target_file=func.file_path,
                    config=config,
                )
                
                if not mutant_ids:
                    log_warn(f"No mutants generated for {func.name}")
                    result = MutationResult(
                        function_name=func.name,
                        total_mutants=0,
                        killed=0,
                        survived=0,
                        errors=0,
                    )
                else:
                    # Check which assertions kill which mutants
                    result = check_kills_for_function(
                        fn=func,
                        correct_assertions=correct_assertions,
                        workspace_dir=workspace_dir,
                        mutant_ids=mutant_ids,
                        config=config,
                    )
                
                mutation_results.append(result)
                
                # Save tier2 summary
                if output_manager:
                    try:
                        output_manager.save_tier2_summary(
                            repo=func.repo,
                            pr_number=func.pr_number,
                            function_name=func.name,
                            mutation_result=asdict(result)
                        )
                        log_debug(f"  Saved tier2 summary for {func.name}")
                    except Exception as e:
                        log_warn(f"  Failed to save tier2 summary: {e}")
        
        except Exception as e:
            log_error(f"Error processing {func.name} in Tier 2: {e}")
            result = MutationResult(
                function_name=func.name,
                total_mutants=0,
                killed=0,
                survived=0,
                errors=1,
            )
            mutation_results.append(result)
    
    log_info(f"Tier 2 complete â€” {len(mutation_results)} function(s) tested")
    
    return mutation_results
