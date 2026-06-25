from __future__ import annotations

import argparse
import json
import os
import shutil
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .extraction import extract_modified_functions_with_exclusions
from .generators import generate_mock, generate_nl2postcond, generate_specmind
from .io_utils import append_jsonl, parse_prs, utc_run_id, write_json, write_llm_outputs_py
from .model_config import DEFAULT_MODEL_CONFIG, resolve_model
from .paths import BUG_DETECTOR_ROOT, ensure_project_paths
from .schemas import DetectabilityRecord, MutationRecord, SCHEMA_VERSION, SampleRecord
from .summarize import write_summary_files

ensure_project_paths()

from src.crawler.pr_crawler import get_candidate_prs, get_pr_by_number  # noqa: E402
from src.crawler.repo_manager import RepoManager  # noqa: E402
from src.executor.runner import run_pytest  # noqa: E402
from src.pipeline import _run_assertion_on_version  # noqa: E402
from specmind_pr_eval.run_specmind_pr_eval import run_ast_mutation_for_function  # noqa: E402


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def resolve_pr_infos(repo: str, pr_numbers: list[int], max_prs: int | None, config: dict[str, Any]) -> list[dict[str, Any]]:
    if pr_numbers:
        infos = []
        for pr_number in pr_numbers:
            info = get_pr_by_number(repo, pr_number, config)
            if info:
                infos.append(info)
        return infos[:max_prs] if max_prs is not None else infos
    infos = get_candidate_prs(repo, config)
    return infos[:max_prs] if max_prs is not None else infos


def run_pytest_with_test_overlay(
    test_files: list[str],
    repo_dir: str,
    *,
    timeout: int,
    test_source_dir: str | None = None,
):
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


def detectability_check(
    func,
    pre_dir: str,
    post_dir: str,
    config: dict[str, Any],
    *,
    pre_test_source: str = "version",
) -> DetectabilityRecord:
    timeout = int(config.get("executor", {}).get("timeout_seconds", 30))
    post_result = run_pytest_with_test_overlay(func.test_files, post_dir, timeout=timeout)
    pre_result = run_pytest_with_test_overlay(
        func.test_files,
        pre_dir,
        timeout=timeout,
        test_source_dir=post_dir if pre_test_source == "post" else None,
    )
    detectable: bool | None
    if post_result.status == "pass":
        detectable = pre_result.status == "fail"
    else:
        detectable = None
    return DetectabilityRecord(
        post_tests_status=post_result.status,
        pre_tests_status=pre_result.status,
        detectable_by_tests=detectable,
        post_tests_stdout=(post_result.stdout + "\n" + post_result.stderr)[-4000:],
        pre_tests_stdout=(pre_result.stdout + "\n" + pre_result.stderr)[-4000:],
    )


def mutation_record(func, post_dir: str, postcondition: str, config: dict[str, Any]) -> MutationRecord:
    result = run_ast_mutation_for_function(func, post_dir, postcondition, config)
    valid = result.killed + result.survived
    return MutationRecord(
        backend="function-ast",
        total_mutants=result.total_mutants,
        killed=result.killed,
        survived=result.survived,
        errors=result.errors,
        mutation_score_raw=result.killed / result.total_mutants if result.total_mutants else 0.0,
        mutation_score_valid=result.killed / valid if valid else 0.0,
        per_assertion_kills=result.per_assertion_kills,
    )


def make_sample_id(repo: str, pr_number: int, file_path: str, function_name: str, lineno: int) -> str:
    safe_repo = repo.replace("/", "_")
    safe_file = file_path.replace("\\", "/").replace("/", "__")
    return f"{safe_repo}__pr{pr_number}__{safe_file}__{function_name}__L{lineno}"


def evaluate_function(
    *,
    run_id: str,
    dataset: str,
    repo: str,
    pr_info: dict[str, Any],
    func,
    pre_dir: str,
    post_dir: str,
    config: dict[str, Any],
    args: argparse.Namespace,
) -> SampleRecord:
    def post_evaluator(postcondition: str) -> str:
        return _run_assertion_on_version(
            func=func,
            func_code=func.post_commit_code,
            assertion=postcondition,
            repo_dir=post_dir,
            config=config,
        )

    detectability = None
    if not args.skip_detectability:
        try:
            detectability = detectability_check(
                func,
                pre_dir,
                post_dir,
                config,
                pre_test_source=args.pre_test_source,
            )
        except Exception as exc:
            detectability = DetectabilityRecord(
                post_tests_status="error",
                pre_tests_status="error",
                detectable_by_tests=None,
                post_tests_stdout=f"detectability error: {type(exc).__name__}: {exc}",
                pre_tests_stdout="",
            )

    if args.mock_postcondition:
        generation = generate_mock(
            args.mock_postcondition,
            method=args.method,
            mode=args.mode,
            model=args.model,
            evaluate_postcondition=post_evaluator,
        )
    elif args.method == "specmind":
        generation = generate_specmind(
            function_code=func.post_commit_code,
            entrypoint=func.name,
            evaluate_postcondition=post_evaluator,
            mode=args.mode,
            max_turns=args.max_turns,
            model=args.model,
        )
    else:
        generation = generate_nl2postcond(
            function_code=func.post_commit_code,
            entrypoint=func.name,
            evaluate_postcondition=post_evaluator,
            model=args.model,
            context=args.nl2postcond_context,
            prompt_v=args.nl2postcond_prompt_v,
        )

    postcondition = generation.postcondition
    post_status = generation.post_status
    pre_status = "not_run"
    bug_detected = False
    mutation = None

    if postcondition and post_status == "pass":
        pre_status = _run_assertion_on_version(
            func=func,
            func_code=func.pre_commit_code,
            assertion=postcondition,
            repo_dir=pre_dir,
            config=config,
            test_source_dir=post_dir if args.pre_test_source == "post" else None,
        )
        bug_detected = pre_status == "fail"
        if not args.skip_mutation:
            try:
                mutation = mutation_record(func, post_dir, postcondition, config)
            except Exception as exc:
                mutation = MutationRecord(
                    backend="function-ast",
                    total_mutants=0,
                    killed=0,
                    survived=0,
                    errors=1,
                    mutation_score_raw=0.0,
                    mutation_score_valid=0.0,
                    error=f"{type(exc).__name__}: {exc}",
                )

    return SampleRecord(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        dataset=dataset,
        project=repo,
        method=args.method,
        mode=args.mode if args.method == "specmind" else "singlepass",
        model=args.model,
        sample_id=make_sample_id(repo, func.pr_number, func.file_path, func.name, func.lineno),
        pr_number=func.pr_number,
        pr_title=str(pr_info.get("title", "")),
        pre_commit=func.pre_commit_sha,
        post_commit=func.post_commit_sha,
        function_name=func.name,
        file_path=func.file_path,
        lineno=func.lineno,
        test_files=func.test_files,
        docstring=func.docstring,
        postcondition=postcondition,
        post_status=post_status,
        pre_status=pre_status,
        post_pass=post_status == "pass",
        pre_fail=pre_status == "fail",
        bug_detected=bug_detected,
        detectability=detectability.to_dict() if detectability else None,
        mutation=mutation.to_dict() if mutation else None,
        generation=generation.to_dict(),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Unified PR/BFC evaluator for the postcondition paper.")
    parser.add_argument("--repo", required=True, help="OWNER/REPO, e.g. marshmallow-code/marshmallow")
    parser.add_argument("--prs", help="Comma-separated PR numbers. If omitted, crawl candidates.")
    parser.add_argument("--max-prs", type=int, default=None)
    parser.add_argument("--max-functions", type=int, default=None)
    parser.add_argument("--dataset", default="real_pr")
    parser.add_argument("--method", choices=["specmind", "nl2postcond"], default="specmind")
    parser.add_argument("--mode", choices=["singlepass", "retry", "multiturn"], default="multiturn")
    parser.add_argument("--model", default=None, help="Raw OpenAI-compatible model id. Overrides SPECMIND_MODEL.")
    parser.add_argument("--model-alias", help="Model alias from paper_eval/configs/models.json.")
    parser.add_argument("--model-config", type=Path, default=DEFAULT_MODEL_CONFIG)
    parser.add_argument("--max-turns", type=int, default=12)
    parser.add_argument("--nl2postcond-context", choices=["stub", "full"], default="full")
    parser.add_argument("--nl2postcond-prompt-v", choices=["base", "simple"], default="simple")
    parser.add_argument("--mock-postcondition", help="Skip LLM and use this assertion.")
    parser.add_argument("--skip-mutation", action="store_true")
    parser.add_argument("--skip-detectability", action="store_true")
    parser.add_argument(
        "--pre-test-source",
        choices=["version", "post"],
        default="version",
        help="Use the pre version's own tests, or overlay post-commit tests when evaluating pre-commit behavior.",
    )
    parser.add_argument("--max-mutants", type=int, default=20)
    parser.add_argument("--config", type=Path, default=BUG_DETECTOR_ROOT / "configs" / "config.json")
    parser.add_argument("--cache-dir", type=Path, default=Path("paper_eval/.cache"))
    parser.add_argument("--output-root", type=Path, default=Path("paper_eval/runs"))
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()

    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")

    selection = resolve_model(
        model=args.model,
        model_alias=args.model_alias,
        config_path=args.model_config,
        default_model=os.getenv("SPECMIND_MODEL", "gpt-5.5"),
    )
    args.model = selection.model_id

    if args.method == "nl2postcond":
        args.mode = "singlepass"

    run_id = args.run_id or utc_run_id(f"{args.method}_{args.mode}")
    run_dir = args.output_root / run_id
    samples_path = run_dir / "samples.jsonl"
    exclusions_path = run_dir / "exclusions.jsonl"

    config = load_config(args.config)
    config["cache_dir"] = str(args.cache_dir.resolve())
    config.setdefault("mutation", {})["max_mutants_per_function"] = args.max_mutants
    if args.max_prs is not None:
        config.setdefault("crawler", {})["max_prs"] = args.max_prs

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "repo": args.repo,
        "dataset": args.dataset,
        "method": args.method,
        "mode": args.mode,
        "output_processing": "specmind_original" if args.method == "specmind" else "nl2postcond_original",
        "model": selection.model_id,
        "model_alias": selection.alias,
        "model_display_name": selection.display_name,
        "model_provider": selection.provider,
        "model_config_path": str(args.model_config),
        "model_config_entry": selection.config_entry,
        "max_turns": args.max_turns,
        "max_mutants": args.max_mutants,
        "skip_mutation": args.skip_mutation,
        "skip_detectability": args.skip_detectability,
        "pre_test_source": args.pre_test_source,
        "nl2postcond_context": args.nl2postcond_context,
        "nl2postcond_prompt_v": args.nl2postcond_prompt_v,
        "mock_postcondition": bool(args.mock_postcondition),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "config_path": str(args.config),
    }
    write_json(run_dir / "manifest.json", manifest)

    pr_infos = resolve_pr_infos(args.repo, parse_prs(args.prs), args.max_prs, config)
    repo_manager = RepoManager(args.repo, config["cache_dir"])

    samples: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    evaluated_functions = 0

    for index, pr_info in enumerate(pr_infos, start=1):
        print(f"[{index}/{len(pr_infos)}] PR #{pr_info['pr_number']}: {pr_info.get('title', '')}")
        try:
            pre_dir = repo_manager.get_pre_commit_dir(pr_info["pre_sha"])
            post_dir = repo_manager.get_post_commit_dir(pr_info["post_sha"])
        except Exception as exc:
            record = {
                "run_id": run_id,
                "project": args.repo,
                "pr_number": pr_info.get("pr_number", 0),
                "pr_title": pr_info.get("title", ""),
                "file_path": None,
                "function_name": None,
                "reason": "checkout_error",
                "detail": f"{type(exc).__name__}: {exc}",
            }
            exclusions.append(record)
            append_jsonl(exclusions_path, [record])
            continue

        pr_functions = []
        for changed_file in pr_info.get("changed_files", []):
            funcs, file_exclusions = extract_modified_functions_with_exclusions(
                run_id=run_id,
                repo=args.repo,
                pre_commit_dir=pre_dir,
                post_commit_dir=post_dir,
                changed_file=changed_file,
                pr_info={**pr_info, "repo": args.repo},
                config=config,
            )
            pr_functions.extend(funcs)
            exclusion_dicts = [item.to_dict() for item in file_exclusions]
            exclusions.extend(exclusion_dicts)
            append_jsonl(exclusions_path, exclusion_dicts)

        for func in pr_functions:
            if args.max_functions is not None and evaluated_functions >= args.max_functions:
                break
            print(f"  evaluating {func.name} ({func.file_path})")
            sample = evaluate_function(
                run_id=run_id,
                dataset=args.dataset,
                repo=args.repo,
                pr_info=pr_info,
                func=func,
                pre_dir=pre_dir,
                post_dir=post_dir,
                config=config,
                args=args,
            ).to_dict()
            samples.append(sample)
            append_jsonl(samples_path, [sample])
            evaluated_functions += 1

        if args.max_functions is not None and evaluated_functions >= args.max_functions:
            break

    summary = write_summary_files(run_dir, samples, exclusions)
    llm_outputs_path = write_llm_outputs_py(run_dir, samples)
    print(json.dumps(summary, indent=2))
    print(f"LLM outputs: {llm_outputs_path}")
    print(f"Run directory: {run_dir.resolve()}")


if __name__ == "__main__":
    main()
