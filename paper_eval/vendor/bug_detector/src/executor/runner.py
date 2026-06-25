"""Subprocess-based code runner — executes injected code with pytest."""

import os
import sys
import subprocess
import tempfile
import textwrap
from pathlib import Path

from src.models import ExecResult
from src.executor.result_parser import parse_exec_result
from src.nl2postcond.interface import wrap_assertion
from src.tracker.logger import log_info, log_debug, log_warn, log_error


def run_pytest(
    test_files: list[str],
    repo_dir: str,
    timeout: int = 30,
) -> ExecResult:
    """Run pytest in repo_dir against the provided test files."""
    repo_path = Path(repo_dir).resolve()
    abs_test_files = [
        str(repo_path / tf) if not Path(tf).is_absolute() else tf
        for tf in test_files
    ]
    existing = [f for f in abs_test_files if Path(f).exists()]
    if not existing:
        return parse_exec_result(
            exit_code=1,
            stdout="",
            stderr=f"No test files found in {repo_dir}: {test_files}",
            timed_out=False,
        )

    cmd = [
        sys.executable, "-m", "pytest",
        *existing,
        "-x",
        "--tb=short",
        "-q",
        "--no-header",
    ]

    log_debug(f"Running: {' '.join(cmd)}")
    env = os.environ.copy()
    pythonpath_parts = [str(repo_path), str(repo_path / "src")]
    if env.get("PYTHONPATH"):
        pythonpath_parts.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(repo_path),
            env=env,
        )
        return parse_exec_result(
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            timed_out=False,
        )
    except subprocess.TimeoutExpired:
        log_warn(f"Execution timed out after {timeout}s")
        return parse_exec_result(
            exit_code=1,
            stdout="",
            stderr=f"TimeoutExpired: execution exceeded {timeout} seconds",
            timed_out=True,
        )


def run_code(
    code: str,
    test_files: list[str],
    repo_dir: str,
    timeout: int = 30,
) -> ExecResult:
    """
    Viết code ra file tạm, chạy pytest trong subprocess.
    Đảm bảo cleanup file tạm trong finally block.

    Nếu test_files rỗng: chạy code trực tiếp bằng `python <tmp_file>`.
    Nếu có test_files: patch file tương ứng trong repo_dir rồi chạy pytest.
    """
    tmp_path = None
    try:
        # Write code to a temp file inside repo_dir so imports resolve correctly
        repo_path = Path(repo_dir)
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            dir=str(repo_path),
            delete=False,
            encoding="utf-8",
        ) as tmp:
            tmp.write(code)
            tmp_path = tmp.name

        log_debug(f"Temp file: {tmp_path}")

        if test_files:
            return run_pytest(test_files, repo_dir, timeout=timeout)
        else:
            # No test files: just run the code as a plain Python script
            cmd = [sys.executable, tmp_path]

        log_debug(f"Running: {' '.join(cmd)}")

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(repo_path),
            )
            result = parse_exec_result(
                exit_code=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                timed_out=False,
            )
        except subprocess.TimeoutExpired:
            log_warn(f"Execution timed out after {timeout}s")
            result = parse_exec_result(
                exit_code=1,
                stdout="",
                stderr=f"TimeoutExpired: execution exceeded {timeout} seconds",
                timed_out=True,
            )

        log_debug(f"Exit code: {result.exit_code}, status: {result.status}")
        return result

    finally:
        # Always clean up temp file
        if tmp_path and Path(tmp_path).exists():
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def run_assertion_on_function(
    func_source: str,
    func_name: str,
    assertions: list[str],
    test_files: list[str],
    repo_dir: str,
    config: dict,
) -> ExecResult:
    """
    Wrap func_source với assertion theo pattern nl2postcond rồi gọi run_code().
    High-level API dùng trong pipeline.

    Dùng wrap_assertion() để tạo wrapper function với `return_value`.
    Chỉ dùng assertion đầu tiên trong list (mỗi hàm sinh 1 assertion).
    """
    timeout = config.get("executor", {}).get("timeout_seconds", 30)

    if not assertions:
        return parse_exec_result(
            exit_code=1,
            stdout="",
            stderr="No assertions provided",
            timed_out=False,
        )

    # Dùng assertion đầu tiên (mỗi hàm chỉ có 1 assertion theo thiết kế mới)
    assertion = assertions[0]
    try:
        wrapped = wrap_assertion(func_source, func_name, assertion)
    except (RuntimeError, ValueError) as e:
        log_error(f"Failed to wrap assertion: {e}")
        return parse_exec_result(
            exit_code=1,
            stdout="",
            stderr=f"WrapError: {e}",
            timed_out=False,
        )

    log_debug(f"Wrapped code:\n{wrapped}")

    return run_code(wrapped, test_files, repo_dir, timeout=timeout)
