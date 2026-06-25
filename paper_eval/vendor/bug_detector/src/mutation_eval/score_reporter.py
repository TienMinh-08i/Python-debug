"""Mutation evaluation report generation."""

from src.models import MutationResult
from src.tracker.logger import log_info


def generate_tier2_report(mutation_results: list[MutationResult]) -> dict:
    """
    Tạo report tầng 2:
    {
        "total_functions": int,
        "avg_mutation_score": float,
        "per_function": [
            {
                "function_name": str,
                "total_mutants": int,
                "killed": int,
                "mutation_score": float,
                "best_assertions": [str]  # assertion kill được nhiều nhất
            }
        ]
    }
    """
    if not mutation_results:
        return {
            "total_functions": 0,
            "avg_mutation_score": 0.0,
            "per_function": [],
        }
    
    total_functions = len(mutation_results)
    total_score = sum(r.mutation_score for r in mutation_results)
    avg_score = total_score / total_functions if total_functions > 0 else 0.0
    
    per_function = []
    
    for result in mutation_results:
        # Find best assertions (those that killed the most mutants)
        best_assertions = []
        if result.per_assertion_kills:
            kills_per_assertion = [
                (assertion, len(ids))
                for assertion, ids in result.per_assertion_kills.items()
            ]
            # Sort by number of kills (descending)
            kills_per_assertion.sort(key=lambda x: x[1], reverse=True)
            best_assertions = [assertion for assertion, _ in kills_per_assertion]
        
        per_function.append({
            "function_name": result.function_name,
            "total_mutants": result.total_mutants,
            "killed": result.killed,
            "survived": result.survived,
            "errors": result.errors,
            "mutation_score": result.mutation_score,
            "best_assertions": best_assertions,
        })
    
    report = {
        "total_functions": total_functions,
        "avg_mutation_score": avg_score,
        "per_function": per_function,
    }
    
    log_info(f"Tier 2 report: {total_functions} functions, avg score: {avg_score:.2f}")
    
    return report

