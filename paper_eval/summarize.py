from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path
from typing import Any

from .io_utils import read_jsonl, write_json


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def summarize_records(samples: list[dict[str, Any]], exclusions: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(samples)
    post_pass = sum(1 for item in samples if item.get("post_pass"))
    pre_fail = sum(1 for item in samples if item.get("pre_fail"))
    bug_detected = sum(1 for item in samples if item.get("bug_detected"))
    executable = sum(1 for item in samples if item.get("post_status") in {"pass", "fail"})
    invalid = sum(1 for item in samples if item.get("post_status") in {"error", "timeout"})

    detectable_items = [
        item
        for item in samples
        if (item.get("detectability") or {}).get("detectable_by_tests") is True
    ]
    detectable_detected = sum(1 for item in detectable_items if item.get("bug_detected"))
    post_pass_items = [item for item in samples if item.get("post_pass")]

    mutation_items = [item.get("mutation") for item in samples if item.get("mutation")]
    mutation_items = [item for item in mutation_items if isinstance(item, dict)]
    total_mutants = sum(int(item.get("total_mutants", 0) or 0) for item in mutation_items)
    killed = sum(int(item.get("killed", 0) or 0) for item in mutation_items)
    survived = sum(int(item.get("survived", 0) or 0) for item in mutation_items)
    errors = sum(int(item.get("errors", 0) or 0) for item in mutation_items)

    macro_raw_values = [float(item.get("mutation_score_raw", 0.0) or 0.0) for item in mutation_items]
    macro_valid_values = [float(item.get("mutation_score_valid", 0.0) or 0.0) for item in mutation_items]

    attempts = [float((item.get("generation") or {}).get("attempts", 0) or 0) for item in samples]
    submissions = [float((item.get("generation") or {}).get("submissions", 0) or 0) for item in samples]
    assertion_turns = [float((item.get("generation") or {}).get("assertion_turns", 0) or 0) for item in samples]
    prompt_tokens = sum(int((item.get("generation") or {}).get("token_usage", {}).get("prompt_tokens", 0) or 0) for item in samples)
    completion_tokens = sum(
        int((item.get("generation") or {}).get("token_usage", {}).get("completion_tokens", 0) or 0)
        for item in samples
    )
    total_tokens = sum(int((item.get("generation") or {}).get("token_usage", {}).get("total_tokens", 0) or 0) for item in samples)

    exclusion_counts = Counter(item.get("reason", "unknown") for item in exclusions)
    method_counts = Counter(item.get("method", "unknown") for item in samples)
    model_counts = Counter(item.get("model", "unknown") for item in samples)

    return {
        "total_samples": total,
        "method_counts": dict(method_counts),
        "model_counts": dict(model_counts),
        "post_pass": post_pass,
        "post_pass_rate": post_pass / total if total else 0.0,
        "pre_fail": pre_fail,
        "pre_fail_rate": pre_fail / total if total else 0.0,
        "bug_detected": bug_detected,
        "overall_bfc_detection_rate": bug_detected / total if total else 0.0,
        "post_pass_conditioned_detection_rate": bug_detected / len(post_pass_items) if post_pass_items else 0.0,
        "detectable_samples": len(detectable_items),
        "detectable_bug_detected": detectable_detected,
        "detectable_bfc_detection_rate": detectable_detected / len(detectable_items) if detectable_items else 0.0,
        "executable": executable,
        "executable_rate": executable / total if total else 0.0,
        "invalid": invalid,
        "invalid_rate": invalid / total if total else 0.0,
        "mutation_evaluated_samples": len(mutation_items),
        "total_mutants": total_mutants,
        "killed": killed,
        "survived": survived,
        "errors": errors,
        "mutant_error_rate": errors / total_mutants if total_mutants else 0.0,
        "macro_mutation_score_raw": _avg(macro_raw_values),
        "macro_mutation_score_valid": _avg(macro_valid_values),
        "micro_mutation_score_raw": killed / total_mutants if total_mutants else 0.0,
        "micro_mutation_score_valid": killed / (killed + survived) if (killed + survived) else 0.0,
        "avg_attempts": _avg(attempts),
        "avg_submissions": _avg(submissions),
        "avg_assertion_turns": _avg(assertion_turns),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "avg_total_tokens_per_sample": total_tokens / total if total else 0.0,
        "exclusion_count": len(exclusions),
        "exclusion_reasons": dict(exclusion_counts),
    }


def write_summary_files(run_dir: Path, samples: list[dict[str, Any]], exclusions: list[dict[str, Any]]) -> dict[str, Any]:
    summary = summarize_records(samples, exclusions)
    write_json(run_dir / "summary.json", summary)

    csv_path = run_dir / "samples.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "sample_id",
        "project",
        "method",
        "mode",
        "model",
        "pr_number",
        "function_name",
        "file_path",
        "post_status",
        "pre_status",
        "post_pass",
        "pre_fail",
        "bug_detected",
        "detectable_by_tests",
        "mutants_total",
        "mutants_killed",
        "mutants_survived",
        "mutants_error",
        "mutation_score_valid",
        "attempts",
        "submissions",
        "assertion_turns",
        "total_tokens",
        "failure_category",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in samples:
            mutation = item.get("mutation") or {}
            generation = item.get("generation") or {}
            detectability = item.get("detectability") or {}
            writer.writerow(
                {
                    "sample_id": item.get("sample_id"),
                    "project": item.get("project"),
                    "method": item.get("method"),
                    "mode": item.get("mode"),
                    "model": item.get("model"),
                    "pr_number": item.get("pr_number"),
                    "function_name": item.get("function_name"),
                    "file_path": item.get("file_path"),
                    "post_status": item.get("post_status"),
                    "pre_status": item.get("pre_status"),
                    "post_pass": item.get("post_pass"),
                    "pre_fail": item.get("pre_fail"),
                    "bug_detected": item.get("bug_detected"),
                    "detectable_by_tests": detectability.get("detectable_by_tests"),
                    "mutants_total": mutation.get("total_mutants"),
                    "mutants_killed": mutation.get("killed"),
                    "mutants_survived": mutation.get("survived"),
                    "mutants_error": mutation.get("errors"),
                    "mutation_score_valid": mutation.get("mutation_score_valid"),
                    "attempts": generation.get("attempts"),
                    "submissions": generation.get("submissions"),
                    "assertion_turns": generation.get("assertion_turns"),
                    "total_tokens": (generation.get("token_usage") or {}).get("total_tokens"),
                    "failure_category": item.get("failure_category"),
                }
            )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize a paper_eval run directory.")
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()

    samples = read_jsonl(args.run_dir / "samples.jsonl")
    exclusions = read_jsonl(args.run_dir / "exclusions.jsonl")
    summary = write_summary_files(args.run_dir, samples, exclusions)
    print(f"Samples: {summary['total_samples']}")
    print(f"Post pass: {summary['post_pass_rate']:.2%}")
    print(f"Overall BFC detect: {summary['overall_bfc_detection_rate']:.2%}")
    print(f"Valid micro mutation: {summary['micro_mutation_score_valid']:.2%}")
    print(f"Wrote {args.run_dir / 'summary.json'}")


if __name__ == "__main__":
    main()

