"""Run assertions on mutants during mutation evaluation."""

from pathlib import Path

from src.executor.runner import run_code, run_pytest
from src.injector.ast_injector import inject_assertions
from src.injector.code_util import replace_function
from src.nl2postcond.interface import wrap_assertion
from src.models import ExecResult
from src.tracker.logger import log_info, log_warn, log_debug, log_error


def run_assertions_on_mutant(
    mutant_source: str,
    func_name: str,
    correct_assertions: list[str],
    test_files: list[str],
    workspace_dir: str,
    config: dict,
    target_file: str | None = None,
) -> ExecResult:
    """
    Inject correct_assertions vào mutant_source.
    Chạy trong subprocess.
    Trả về ExecResult.
    """
    timeout = config.get("executor", {}).get("timeout_seconds", 30)
    
    try:
        log_debug(f"Injecting {len(correct_assertions)} assertions into mutant {func_name}")

        assertion = correct_assertions[0] if correct_assertions else "assert return_value is not None"
        wrapped = wrap_assertion(mutant_source, func_name, assertion)

        if target_file:
            target_path = Path(workspace_dir) / target_file
            original_source = target_path.read_text(encoding="utf-8", errors="ignore")
            try:
                patched_source = replace_function(original_source, func_name, wrapped)
                target_path.write_text(patched_source, encoding="utf-8")
                return run_pytest(test_files, workspace_dir, timeout=timeout)
            finally:
                target_path.write_text(original_source, encoding="utf-8")

        return run_code(wrapped, test_files, workspace_dir, timeout=timeout)
    
    except Exception as e:
        log_error(f"Failed to run assertions on mutant {func_name}: {e}")
        from src.executor.result_parser import parse_exec_result
        return parse_exec_result(
            exit_code=1,
            stdout="",
            stderr=f"Error: {type(e).__name__}: {e}",
            timed_out=False,
        )
