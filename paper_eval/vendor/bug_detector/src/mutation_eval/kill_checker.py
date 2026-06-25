"""Mutation kill checker — test if assertions detect mutants."""

from pathlib import Path

from src.models import FunctionInfo, MutationResult
from src.mutation_eval.assertion_runner import run_assertions_on_mutant
from src.mutator.mutant_loader import apply_mutant, restore_original
from src.tracker.logger import log_info, log_warn, log_debug, log_error


def check_kills_for_function(
    fn: FunctionInfo,
    correct_assertions: list[str],
    workspace_dir: str,
    mutant_ids: list[int | str],
    config: dict,
) -> MutationResult:
    """
    Với mỗi mutant_id:
        1. apply_mutant(mutant_id)
        2. run_assertions_on_mutant(...)
        3. killed = result.status == 'fail'
        4. restore_original() ← luôn trong finally
    Tổng hợp thành MutationResult.
    """
    log_info(f"Checking kills for {fn.name} ({len(mutant_ids)} mutants)")
    
    killed = 0
    survived = 0
    errors = 0
    per_assertion_kills = {assertion: [] for assertion in correct_assertions}
    
    # Store original source for restoration
    original_source = fn.post_commit_code
    
    for mutant_id in mutant_ids:
        try:
            log_debug(f"Processing mutant {mutant_id} of {fn.name}")
            
            # Apply mutant
            try:
                mutant_source = apply_mutant(mutant_id, workspace_dir, fn.file_path)
            except Exception as e:
                log_warn(f"Failed to apply mutant {mutant_id}: {e}")
                errors += 1
                continue
            
            try:
                # Run assertions on mutant
                result = run_assertions_on_mutant(
                    mutant_source=mutant_source,
                    func_name=fn.name,
                    correct_assertions=correct_assertions,
                    test_files=fn.test_files,
                    workspace_dir=workspace_dir,
                    config=config,
                    target_file=fn.file_path,
                )
                
                # Check if killed (assertion failed)
                if result.status == "fail":
                    killed += 1
                    # Track which assertions killed this mutant
                    for assertion in correct_assertions:
                        per_assertion_kills[assertion].append(mutant_id)
                elif result.status == "error" or result.timed_out:
                    errors += 1
                else:
                    survived += 1
                
                log_debug(f"Mutant {mutant_id}: {result.status}")
            
            finally:
                # Always restore original source
                try:
                    restore_original(workspace_dir, fn.file_path, original_source)
                except Exception as e:
                    log_error(f"Failed to restore original after mutant {mutant_id}: {e}")
        
        except Exception as e:
            log_error(f"Unexpected error processing mutant {mutant_id}: {e}")
            errors += 1
    
    # Create MutationResult
    result = MutationResult(
        function_name=fn.name,
        total_mutants=len(mutant_ids),
        killed=killed,
        survived=survived,
        errors=errors,
        per_assertion_kills=per_assertion_kills,
    )
    
    log_info(f"✓ {fn.name}: killed={killed}, survived={survived}, errors={errors}, score={result.mutation_score:.2f}")
    
    return result
