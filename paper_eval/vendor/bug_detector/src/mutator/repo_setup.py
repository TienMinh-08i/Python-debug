"""Install cloned-repo test dependencies so mutmut baseline pytest can run."""

import subprocess
import sys
from pathlib import Path

from src.tracker.logger import log_info, log_warn, log_debug


def install_repo_test_deps(workspace_dir: str) -> bool:
    """
    Install minimal test deps for a cloned repo workspace (editable install + simplejson).

    Returns True if pytest can import the project test suite afterward.
    """
    workspace = Path(workspace_dir)
    if not workspace.is_dir():
        log_warn(f"Workspace not found: {workspace}")
        return False

    log_info(f"Installing test dependencies in {workspace}")

    steps = [
        [sys.executable, "-m", "pip", "install", "-q", "simplejson"],
        [sys.executable, "-m", "pip", "install", "-q", "-e", "."],
    ]

    for cmd in steps:
        try:
            result = subprocess.run(
                cmd,
                cwd=str(workspace),
                capture_output=True,
                text=True,
                timeout=300,
            )
            if result.returncode != 0:
                log_warn(
                    f"pip step failed ({' '.join(cmd[-2:])}): "
                    f"{(result.stderr or result.stdout)[:400]}"
                )
        except subprocess.TimeoutExpired:
            log_warn(f"pip timed out: {' '.join(cmd)}")
            return False

    verify = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "tests"],
        cwd=str(workspace),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if verify.returncode != 0:
        log_warn(
            "pytest collect failed after install — mutmut may not generate mutants. "
            f"{(verify.stderr or verify.stdout)[:400]}"
        )
        return False

    log_debug("pytest collect OK")
    return True
