"""Parse subprocess output into ExecResult objects."""

from src.models import ExecResult


def parse_exec_result(
    exit_code: int,
    stdout: str,
    stderr: str,
    timed_out: bool = False,
) -> ExecResult:
    """Tạo ExecResult từ subprocess output."""
    return ExecResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
    )


def classify_error(stderr: str) -> str:
    """
    Trả về: 'assertion_error' | 'syntax_error' | 'runtime_error' | 'none'
    """
    if not stderr:
        return "none"

    # Check in order of specificity
    if "AssertionError" in stderr:
        return "assertion_error"
    if "SyntaxError" in stderr or "IndentationError" in stderr:
        return "syntax_error"
    # Any other exception means runtime error
    # Look for the canonical "Traceback" pattern
    if "Traceback (most recent call last)" in stderr or "Error" in stderr:
        return "runtime_error"
    return "none"
