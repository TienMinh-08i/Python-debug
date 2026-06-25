"""Final report generation combining tier 1 and tier 2 results."""

import json
from datetime import datetime, timezone
from pathlib import Path

from src.models import AssertionResult, MutationResult
from src.tracker.logger import log_info, log_warn


def generate_final_report(
    tier1_results: list[AssertionResult],
    tier2_results: list[MutationResult],
    output_path: str,
) -> dict:
    """
    Tổng hợp cả 2 tầng thành report cuối.
    
    Output JSON:
    {
        "summary": {
            "total_functions": int,
            "total_assertions_generated": int,
            "correct_assertions": int,
            "correctness_rate": float,
            "avg_mutation_score": float
        },
        "per_function": [...],
        "generated_at": "ISO timestamp"
    }
    
    Lưu ra output_path.
    In summary ra console.
    """
    
    # Calculate tier 1 statistics
    total_assertions = len(tier1_results)
    correct_assertions = sum(1 for r in tier1_results if r.is_correct)
    correctness_rate = (correct_assertions / total_assertions 
                       if total_assertions > 0 else 0.0)
    
    # Get unique functions from tier 1
    tier1_by_func = {}
    for result in tier1_results:
        if result.function_name not in tier1_by_func:
            tier1_by_func[result.function_name] = []
        tier1_by_func[result.function_name].append(result)
    
    total_functions = len(tier1_by_func)
    
    # Calculate tier 2 statistics
    tier2_by_func = {r.function_name: r for r in tier2_results}
    
    # Average mutation score (only for functions that went through tier 2)
    if tier2_results:
        avg_mutation_score = sum(r.mutation_score for r in tier2_results) / len(tier2_results)
    else:
        avg_mutation_score = 0.0
    
    # Build per-function details
    per_function = []
    
    for func_name, assertions in tier1_by_func.items():
        func_correct_assertions = sum(1 for a in assertions if a.is_correct)
        func_total_assertions = len(assertions)
        
        func_detail = {
            "function_name": func_name,
            "tier1": {
                "total_assertions": func_total_assertions,
                "correct_assertions": func_correct_assertions,
                "correctness_rate": (func_correct_assertions / func_total_assertions 
                                    if func_total_assertions > 0 else 0.0),
                "assertions": [
                    {
                        "code": a.assertion_code,
                        "pre_status": a.pre_status,
                        "post_status": a.post_status,
                        "is_correct": a.is_correct,
                    }
                    for a in assertions
                ],
            },
        }
        
        # Add tier 2 results if available
        if func_name in tier2_by_func:
            tier2 = tier2_by_func[func_name]
            func_detail["tier2"] = {
                "total_mutants": tier2.total_mutants,
                "killed": tier2.killed,
                "survived": tier2.survived,
                "errors": tier2.errors,
                "mutation_score": tier2.mutation_score,
            }
        
        per_function.append(func_detail)
    
    # Create final report
    report = {
        "summary": {
            "total_functions": total_functions,
            "total_assertions_generated": total_assertions,
            "correct_assertions": correct_assertions,
            "correctness_rate": correctness_rate,
            "avg_mutation_score": avg_mutation_score,
        },
        "per_function": per_function,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    
    # Save to file
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        log_info(f"Final report saved to {output_file}")
    except Exception as e:
        log_warn(f"Failed to save report to {output_file}: {e}")
    
    # Print summary to console
    print("\n" + "=" * 60)
    print("FINAL REPORT SUMMARY")
    print("=" * 60)
    print(f"Total Functions: {total_functions}")
    print(f"Total Assertions Generated: {total_assertions}")
    print(f"Correct Assertions: {correct_assertions}")
    print(f"Correctness Rate: {correctness_rate:.2%}")
    print(f"Average Mutation Score: {avg_mutation_score:.2f}")
    print("=" * 60 + "\n")
    
    return report

