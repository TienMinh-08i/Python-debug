from __future__ import annotations

import argparse
import math
import json
import os
import pickle
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .generators import generate_mock, generate_nl2postcond, generate_specmind
from .io_utils import append_jsonl, read_jsonl, utc_run_id, write_json, write_llm_outputs_py
from .model_config import DEFAULT_MODEL_CONFIG, resolve_model
from .paths import ensure_project_paths
from .schemas import MutationRecord, SCHEMA_VERSION, SampleRecord
from .summarize import write_summary_files

ensure_project_paths()

import generate_and_test_postconditions_general as specmind_core  # noqa: E402


PROBLEMATIC_TASKS = {"HumanEval/36", "HumanEval/83", "HumanEval/139", "HumanEval/160", "HumanEval/32"}
DEFAULT_BUGGY_CODES_FILE = Path("code_mutants/all_code_mutants_with_bad_output.jsonl.zip")


def require_evalplus():
    try:
        from evalplus.data import get_human_eval_plus, get_human_eval_plus_hash
        from evalplus.gen.util import trusted_exec
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "Missing dependency for RQ1: evalplus. Install it first, for example:\n"
            "  pip install evalplus\n"
            f"Original error: {type(exc).__name__}: {exc}"
        ) from exc
    return get_human_eval_plus, get_human_eval_plus_hash, trusted_exec


def get_groundtruth(
    problems: dict[str, dict[str, Any]],
    hashcode: str,
    trusted_exec_fn: Any,
    cache_root: Path,
) -> dict[str, Any]:
    cache_root.mkdir(parents=True, exist_ok=True)
    cache_file = cache_root / f"{hashcode}.pkl"
    if cache_file.exists():
        print(f"Load expected outputs from {cache_file}")
        with cache_file.open("rb") as f:
            return pickle.load(f)

    print("Computing EvalPlus expected outputs...")
    start = time.time()
    expected_output = {}
    for task_id, problem in problems.items():
        try:
            code = problem["prompt"] + problem["canonical_solution"]
            oracle = {}
            oracle["base"], oracle["base_time"] = trusted_exec_fn(
                code,
                problem["base_input"],
                problem["entry_point"],
                record_time=True,
            )
            oracle["plus"], oracle["plus_time"] = trusted_exec_fn(
                code,
                problem["plus_input"],
                problem["entry_point"],
                record_time=True,
            )
            expected_output[task_id] = oracle
        except Exception as exc:
            print(f"Skipping {task_id} while computing groundtruth: {type(exc).__name__}: {exc}")

    with cache_file.open("wb") as f:
        pickle.dump(expected_output, f)
    print(f"Expected outputs computed in {time.time() - start:.2f}s")
    return expected_output


def parse_tasks(raw: str | None, all_tasks: list[str], limit: int | None, skip_problematic: bool) -> list[str]:
    if raw:
        tasks = [item.strip() for item in raw.split(",") if item.strip()]
    else:
        tasks = list(all_tasks)
    if skip_problematic:
        tasks = [task for task in tasks if task not in PROBLEMATIC_TASKS]
    if limit is not None:
        tasks = tasks[:limit]
    return tasks


def function_code_for_problem(problem: dict[str, Any]) -> str:
    return problem["prompt"] + problem["canonical_solution"]


DIRECT_EVAL_SCRIPT = r'''
from __future__ import annotations

import json
import math
import pickle
import traceback
from pathlib import Path


def close_enough(actual, expected, atol):
    if isinstance(actual, float) or isinstance(expected, float):
        try:
            return math.isclose(actual, expected, abs_tol=atol)
        except TypeError:
            return False
    if isinstance(actual, (list, tuple)) and isinstance(expected, (list, tuple)):
        return len(actual) == len(expected) and all(close_enough(a, b, atol) for a, b in zip(actual, expected))
    if isinstance(actual, dict) and isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(close_enough(actual[k], expected[k], atol) for k in actual)
    return actual == expected


payload = pickle.loads(Path(__PAYLOAD__).read_bytes())
namespace = {}
try:
    exec(payload["wrapped_code"], namespace)
    fn = namespace[payload["wrapped_entry"]]
    failures = []
    for index, (args, expected) in enumerate(zip(payload["inputs"], payload["expected"])):
        if not isinstance(args, (list, tuple)):
            args = [args]
        try:
            actual = fn(*args)
        except AssertionError as exc:
            failures.append({"index": index, "kind": "assertion", "error": str(exc)})
            continue
        except Exception as exc:
            failures.append({"index": index, "kind": "exception", "error": f"{type(exc).__name__}: {exc}"})
            continue
        if not close_enough(actual, expected, payload["atol"]):
            failures.append({"index": index, "kind": "wrong_output", "actual": repr(actual), "expected": repr(expected)})
    if failures:
        print(json.dumps({"status": "fail", "failures": failures[:20]}, ensure_ascii=False))
    else:
        print(json.dumps({"status": "pass", "failures": []}, ensure_ascii=False))
except SyntaxError as exc:
    print(json.dumps({"status": "error", "error": f"SyntaxError: {exc}"}, ensure_ascii=False))
except Exception as exc:
    print(json.dumps({"status": "error", "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()[-4000:]}, ensure_ascii=False))
'''


DIRECT_MUTANT_SCRIPT = r'''
from __future__ import annotations

import json
import pickle
import traceback
from pathlib import Path


payload = pickle.loads(Path(__PAYLOAD__).read_bytes())
namespace = {}
try:
    exec(payload["wrapped_code"], namespace)
    fn = namespace[payload["entry_point"]]
    killed = 0
    total = 0
    failures = []
    for index, args in enumerate(payload["inputs"]):
        if not isinstance(args, (list, tuple)):
            args = [args]
        total += 1
        try:
            fn(*args)
        except Exception as exc:
            killed += 1
            failures.append({"index": index, "error": f"{type(exc).__name__}: {exc}"})
    print(json.dumps({"status": "ok", "total": total, "killed": killed, "failures": failures[:5]}, ensure_ascii=False))
except SyntaxError as exc:
    print(json.dumps({"status": "error", "error": f"SyntaxError: {exc}"}, ensure_ascii=False))
except Exception as exc:
    print(json.dumps({"status": "error", "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()[-4000:]}, ensure_ascii=False))
'''


def direct_postcondition_check(
    problem: dict[str, Any],
    postcondition: str,
    inputs: list[Any],
    expected: list[Any],
    atol: float,
    timeout_seconds: float = 30.0,
) -> tuple[str, dict[str, Any]]:
    wrapped_code = specmind_core.wrap_with_postcondition(
        problem["prompt"] + problem["canonical_solution"],
        postcondition,
        problem["entry_point"],
    )
    payload = {
        "wrapped_code": wrapped_code,
        "wrapped_entry": problem["entry_point"] + "_wrapped",
        "inputs": inputs,
        "expected": expected,
        "atol": atol,
    }
    with tempfile.TemporaryDirectory(prefix="paper_eval_rq1_") as tmp:
        tmp_dir = Path(tmp)
        payload_path = tmp_dir / "payload.pkl"
        script_path = tmp_dir / "check_postcondition.py"
        payload_path.write_bytes(pickle.dumps(payload))
        script_path.write_text(DIRECT_EVAL_SCRIPT.replace("__PAYLOAD__", repr(str(payload_path))), encoding="utf-8")
        try:
            result = subprocess.run(
                [sys.executable, str(script_path)],
                cwd=str(tmp_dir),
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return "timeout", {"status": "timeout", "wrapped_code": wrapped_code}

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    if not stdout:
        return "error", {"status": "error", "stderr": stderr[-4000:], "wrapped_code": wrapped_code}
    try:
        detail = json.loads(stdout.splitlines()[-1])
    except json.JSONDecodeError:
        return "error", {"status": "error", "stdout": stdout[-4000:], "stderr": stderr[-4000:], "wrapped_code": wrapped_code}
    if result.returncode != 0 and detail.get("status") == "pass":
        detail["status"] = "error"
        detail["stderr"] = stderr[-4000:]
    detail["wrapped_code"] = wrapped_code
    return detail.get("status", "error"), detail


def make_evalplus_evaluator(problem: dict[str, Any], expected_output: dict[str, Any], base_only: bool):
    def evaluator(postcondition: str) -> str:
        status, _detail = evaluate_postcondition_detail(problem, expected_output, postcondition, base_only)
        return status

    return evaluator


def evaluate_postcondition_detail(
    problem: dict[str, Any],
    expected_output: dict[str, Any],
    postcondition: str,
    base_only: bool,
) -> tuple[str, dict[str, Any] | None]:
    base_status, base_detail = direct_postcondition_check(
        problem=problem,
        postcondition=postcondition,
        inputs=problem["base_input"],
        expected=expected_output["base"],
        atol=problem.get("atol", 0),
    )
    if base_only or base_status != "pass":
        return base_status, {"base": base_detail, "plus": None}
    plus_status, plus_detail = direct_postcondition_check(
        problem=problem,
        postcondition=postcondition,
        inputs=problem["plus_input"],
        expected=expected_output["plus"],
        atol=problem.get("atol", 0),
    )
    return plus_status, {"base": base_detail, "plus": plus_detail}


def ensure_evalplus_compat() -> None:
    """Patch small EvalPlus API differences expected by the original nl2postcond evaluator."""
    import evalplus.data as evalplus_data
    import evalplus.eval as evalplus_eval

    if not hasattr(evalplus_data, "CACHE_DIR"):
        cache_dir = Path("paper_eval/cache/evalplus_groundtruth").resolve()
        cache_dir.mkdir(parents=True, exist_ok=True)
        evalplus_data.CACHE_DIR = str(cache_dir)
    if not hasattr(evalplus_eval, "SUCCESS") and hasattr(evalplus_eval, "PASS"):
        evalplus_eval.SUCCESS = evalplus_eval.PASS
    if not hasattr(evalplus_eval, "FAILED") and hasattr(evalplus_eval, "FAIL"):
        evalplus_eval.FAILED = evalplus_eval.FAIL


def load_buggy_codes(task_id: str, buggy_codes_file: Path) -> list[dict[str, Any]]:
    buggy_codes = []
    with zipfile.ZipFile(buggy_codes_file, "r") as zip_ref:
        for filename in zip_ref.namelist():
            if not filename.endswith(".jsonl"):
                continue
            with zip_ref.open(filename) as handle:
                for raw_line in handle:
                    if not raw_line.strip():
                        continue
                    row = json.loads(raw_line.decode("utf-8"))
                    if row.get("task_id") == task_id:
                        buggy_codes.append(row)
    return buggy_codes


def direct_mutant_check(
    wrapped_code: str,
    entry_point: str,
    inputs: list[Any],
    timeout_seconds: float,
) -> tuple[str, dict[str, Any]]:
    payload = {
        "wrapped_code": wrapped_code,
        "entry_point": entry_point,
        "inputs": inputs,
    }
    with tempfile.TemporaryDirectory(prefix="paper_eval_mutant_") as tmp:
        tmp_dir = Path(tmp)
        payload_path = tmp_dir / "payload.pkl"
        script_path = tmp_dir / "check_mutant.py"
        payload_path.write_bytes(pickle.dumps(payload))
        script_path.write_text(DIRECT_MUTANT_SCRIPT.replace("__PAYLOAD__", repr(str(payload_path))), encoding="utf-8")
        try:
            result = subprocess.run(
                [sys.executable, str(script_path)],
                cwd=str(tmp_dir),
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return "timeout", {"status": "timeout", "total": len(inputs), "killed": 0}

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    if not stdout:
        return "error", {"status": "error", "stderr": stderr[-4000:], "total": len(inputs), "killed": 0}
    try:
        detail = json.loads(stdout.splitlines()[-1])
    except json.JSONDecodeError:
        return "error", {"status": "error", "stdout": stdout[-4000:], "stderr": stderr[-4000:], "total": len(inputs), "killed": 0}
    return detail.get("status", "error"), detail


def power_eval_record(
    problem: dict[str, Any],
    postcondition: str,
    skip_power_eval: bool,
    buggy_codes_file: Path = DEFAULT_BUGGY_CODES_FILE,
) -> MutationRecord | None:
    if skip_power_eval:
        return None
    try:
        ensure_evalplus_compat()
        from response_preprocessing import code_sanitize, wrap_code_solution

        sanitized = code_sanitize(postcondition)
        if not sanitized:
            raise ValueError("Postcondition could not be sanitized for power evaluation.")

        buggy_codes = load_buggy_codes(problem["task_id"], buggy_codes_file)
    except Exception as exc:
        return MutationRecord(
            backend="evalplus-code-mutants",
            total_mutants=0,
            killed=0,
            survived=0,
            errors=1,
            mutation_score_raw=0.0,
            mutation_score_valid=0.0,
            error=f"{type(exc).__name__}: {exc}",
        )

    total_codes = len(buggy_codes)
    killed_codes = 0
    total_bopi = 0
    killed_bopi = 0
    errors = 0
    killed_ids = []
    for buggy_code in buggy_codes:
        try:
            wrapped = wrap_code_solution(None, buggy_code["solution"], problem["entry_point"], sanitized)
            inputs = buggy_code.get("unique_bopi") or []
            timeout = max(5.0, min(120.0, 2.0 + 0.05 * len(inputs)))
            status, detail = direct_mutant_check(wrapped, problem["entry_point"], inputs, timeout)
            if status != "ok":
                errors += 1
                continue
            total_bopi += int(detail.get("total", 0) or 0)
            killed = int(detail.get("killed", 0) or 0)
            killed_bopi += killed
            if killed > 0:
                killed_codes += 1
                killed_ids.append(str(buggy_code.get("response_num", buggy_code.get("mutant_id", ""))))
        except Exception:
            errors += 1

    survived = max(0, total_codes - killed_codes - errors)
    raw = killed_bopi / total_bopi if total_bopi else 0.0
    valid = killed_codes / (killed_codes + survived) if (killed_codes + survived) else 0.0
    return MutationRecord(
        backend="evalplus-code-mutants-direct",
        total_mutants=total_codes,
        killed=killed_codes,
        survived=survived,
        errors=errors,
        mutation_score_raw=raw,
        mutation_score_valid=valid,
        per_assertion_kills={postcondition: killed_ids},
    )


def rq1_summary(samples: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(samples)
    correct = sum(1 for item in samples if item.get("post_pass"))
    executable = sum(1 for item in samples if item.get("post_status") in {"pass", "fail"})
    invalid = sum(1 for item in samples if item.get("post_status") in {"error", "timeout"})
    mutation_items = [item.get("mutation") for item in samples if isinstance(item.get("mutation"), dict)]
    killed = sum(int(item.get("killed", 0) or 0) for item in mutation_items)
    survived = sum(int(item.get("survived", 0) or 0) for item in mutation_items)
    total_mutants = sum(int(item.get("total_mutants", 0) or 0) for item in mutation_items)
    return {
        "task_count": total,
        "correct": correct,
        "correctness": correct / total if total else 0.0,
        "executable": executable,
        "executable_rate": executable / total if total else 0.0,
        "invalid": invalid,
        "invalid_rate": invalid / total if total else 0.0,
        "mutation_evaluated_tasks": len(mutation_items),
        "total_mutants": total_mutants,
        "killed": killed,
        "survived": survived,
        "micro_mutation_score_valid": killed / (killed + survived) if (killed + survived) else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="RQ1 runner: EvalPlus/HumanEval+ postcondition evaluation.")
    parser.add_argument("--method", choices=["specmind", "nl2postcond"], required=True)
    parser.add_argument("--mode", choices=["singlepass", "retry", "multiturn"], default="multiturn")
    parser.add_argument("--model", default=None)
    parser.add_argument("--model-alias", help="Model alias from paper_eval/configs/models.json, e.g. llama4-scout.")
    parser.add_argument("--model-config", type=Path, default=DEFAULT_MODEL_CONFIG)
    parser.add_argument("--tasks", help="Comma-separated tasks, e.g. HumanEval/0,HumanEval/1")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-turns", type=int, default=12)
    parser.add_argument("--base-only", action="store_true")
    parser.add_argument("--skip-power-eval", action="store_true")
    parser.add_argument(
        "--completeness-threshold",
        type=float,
        default=90.0,
        help="SpecMind exploratory threshold tau in percent. Used during generation when power eval is enabled.",
    )
    parser.add_argument(
        "--buggy-codes-file",
        type=Path,
        default=DEFAULT_BUGGY_CODES_FILE,
        help="EvalPlus mutant zip used for completeness/power evaluation.",
    )
    parser.add_argument("--skip-problematic", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--nl2postcond-context", choices=["stub", "full"], default="full")
    parser.add_argument("--nl2postcond-prompt-v", choices=["base", "simple"], default="simple")
    parser.add_argument("--mock-postcondition")
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
    if not args.skip_power_eval and not args.buggy_codes_file.exists():
        raise SystemExit(
            "Cannot run completeness/power evaluation because the EvalPlus mutant file is missing:\n"
            f"  {args.buggy_codes_file}\n"
            "The SpecMind paper's completeness metric requires this mutant set. "
            "Run again with --skip-power-eval for correctness only, or provide/create the mutant zip first."
        )

    get_human_eval_plus, get_human_eval_plus_hash, trusted_exec = require_evalplus()
    problems = get_human_eval_plus()
    dataset_hash = get_human_eval_plus_hash()
    groundtruth = get_groundtruth(
        problems=problems,
        hashcode=dataset_hash,
        trusted_exec_fn=trusted_exec,
        cache_root=Path("paper_eval/cache/evalplus_groundtruth"),
    )
    task_ids = parse_tasks(args.tasks, sorted(problems), args.limit, args.skip_problematic)

    run_id = args.run_id or utc_run_id(f"rq1_{args.method}_{args.mode}")
    run_dir = args.output_root / run_id
    samples_path = run_dir / "samples.jsonl"
    exclusions_path = run_dir / "exclusions.jsonl"
    samples: list[dict[str, Any]] = []

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "rq": "RQ1",
        "dataset": "evalplus",
        "benchmark": "HumanEval+",
        "method": args.method,
        "mode": args.mode,
        "output_processing": "specmind_original" if args.method == "specmind" else "nl2postcond_original",
        "model": selection.model_id,
        "model_alias": selection.alias,
        "model_display_name": selection.display_name,
        "model_provider": selection.provider,
        "model_config_path": str(args.model_config),
        "model_config_entry": selection.config_entry,
        "task_count_planned": len(task_ids),
        "tasks": task_ids,
        "max_turns": args.max_turns,
        "base_only": args.base_only,
        "skip_power_eval": args.skip_power_eval,
        "completeness_threshold": args.completeness_threshold if args.method == "specmind" else None,
        "buggy_codes_file": str(args.buggy_codes_file),
        "nl2postcond_context": args.nl2postcond_context,
        "nl2postcond_prompt_v": args.nl2postcond_prompt_v,
        "mock_postcondition": bool(args.mock_postcondition),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json(run_dir / "manifest.json", manifest)

    for index, task_id in enumerate(task_ids, start=1):
        if task_id not in problems:
            exclusion = {
                "run_id": run_id,
                "project": "evalplus",
                "pr_number": 0,
                "pr_title": "",
                "file_path": None,
                "function_name": None,
                "reason": "task_not_found",
                "detail": task_id,
            }
            append_jsonl(exclusions_path, [exclusion])
            continue

        print(f"[{index}/{len(task_ids)}] {task_id}")
        problem = dict(problems[task_id])
        problem["task_id"] = task_id
        expected_output = groundtruth[task_id]
        function_code = function_code_for_problem(problem)
        evaluator = make_evalplus_evaluator(problem, expected_output, args.base_only)
        power_cache: dict[str, dict[str, Any]] = {}

        def completeness_evaluator(postcondition: str) -> dict[str, Any] | None:
            if postcondition in power_cache:
                return power_cache[postcondition]
            record = power_eval_record(problem, postcondition, args.skip_power_eval, args.buggy_codes_file)
            if record is None:
                return None
            payload = record.to_dict()
            power_cache[postcondition] = payload
            return payload

        if args.mock_postcondition:
            generation = generate_mock(
                args.mock_postcondition,
                method=args.method,
                mode=args.mode,
                model=args.model,
                evaluate_postcondition=evaluator,
            )
        elif args.method == "specmind":
            generation = generate_specmind(
                function_code=function_code,
                entrypoint=problem["entry_point"],
                evaluate_postcondition=evaluator,
                mode=args.mode,
                max_turns=args.max_turns,
                model=args.model,
                evaluate_completeness=None if args.skip_power_eval else completeness_evaluator,
                completeness_threshold=args.completeness_threshold,
            )
        else:
            generation = generate_nl2postcond(
                function_code=function_code,
                entrypoint=problem["entry_point"],
                evaluate_postcondition=evaluator,
                model=args.model,
                context=args.nl2postcond_context,
                prompt_v=args.nl2postcond_prompt_v,
                original_problem=problem,
            )

        postcondition = generation.postcondition
        post_status = generation.post_status
        eval_result = None
        mutation = None
        if postcondition:
            post_status, eval_result = evaluate_postcondition_detail(
                problem=problem,
                expected_output=expected_output,
                postcondition=postcondition,
                base_only=args.base_only,
            )
            if post_status == "pass":
                cached_power = power_cache.get(postcondition)
                if cached_power is not None:
                    mutation = MutationRecord(
                        backend=cached_power.get("backend", "evalplus-code-mutants-direct"),
                        total_mutants=int(cached_power.get("total_mutants", 0) or 0),
                        killed=int(cached_power.get("killed", 0) or 0),
                        survived=int(cached_power.get("survived", 0) or 0),
                        errors=int(cached_power.get("errors", 0) or 0),
                        mutation_score_raw=float(cached_power.get("mutation_score_raw", 0.0) or 0.0),
                        mutation_score_valid=float(cached_power.get("mutation_score_valid", 0.0) or 0.0),
                        per_assertion_kills=cached_power.get("per_assertion_kills", {}),
                        error=cached_power.get("error"),
                    )
                else:
                    mutation = power_eval_record(problem, postcondition, args.skip_power_eval, args.buggy_codes_file)

        sample = SampleRecord(
            schema_version=SCHEMA_VERSION,
            run_id=run_id,
            dataset="evalplus",
            project="HumanEval+",
            method=args.method,
            mode=args.mode,
            model=args.model,
            sample_id=task_id.replace("/", "_"),
            pr_number=0,
            pr_title="",
            pre_commit="",
            post_commit="",
            function_name=problem["entry_point"],
            file_path=task_id,
            lineno=0,
            test_files=[],
            docstring=problem.get("prompt", ""),
            postcondition=postcondition,
            post_status=post_status,
            pre_status="not_applicable",
            post_pass=post_status == "pass",
            pre_fail=False,
            bug_detected=False,
            detectability=None,
            mutation=mutation.to_dict() if mutation else None,
            generation=generation.to_dict(),
            notes=json.dumps({"evalplus_result": eval_result}, ensure_ascii=False)[:8000] if eval_result else None,
        ).to_dict()
        samples.append(sample)
        append_jsonl(samples_path, [sample])

    exclusions = read_jsonl(exclusions_path)
    summary = write_summary_files(run_dir, samples, exclusions)
    rq1 = rq1_summary(samples)
    write_json(run_dir / "rq1_summary.json", rq1)
    llm_outputs_path = write_llm_outputs_py(run_dir, samples)

    print(json.dumps(rq1, indent=2))
    print(f"Generic summary: {run_dir / 'summary.json'}")
    print(f"RQ1 summary: {run_dir / 'rq1_summary.json'}")
    print(f"LLM outputs: {llm_outputs_path}")
    print(f"Run directory: {run_dir.resolve()}")


if __name__ == "__main__":
    main()
