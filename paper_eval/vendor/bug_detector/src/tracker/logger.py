"""Colored console logger + file log."""

import os
import sys
import datetime
from pathlib import Path

# Ensure stdout can handle Unicode on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ANSI color codes
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_RED = "\033[91m"
_GRAY = "\033[90m"
_RESET = "\033[0m"

# Log file setup
_LOG_DIR = Path("logs")
_LOG_DIR.mkdir(exist_ok=True)
_LOG_FILE = _LOG_DIR / "pipeline.log"


def _write_to_file(level: str, msg: str) -> None:
    """Append a log line to the log file."""
    timestamp = datetime.datetime.now().isoformat(timespec="seconds")
    line = f"[{timestamp}] [{level}] {msg}\n"
    with open(_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line)


def log_info(msg: str) -> None:
    """Log info message in green."""
    print(f"{_GREEN}[INFO] {msg}{_RESET}")
    _write_to_file("INFO", msg)


def log_warn(msg: str) -> None:
    """Log warning message in yellow."""
    print(f"{_YELLOW}[WARN] {msg}{_RESET}")
    _write_to_file("WARN", msg)


def log_error(msg: str) -> None:
    """Log error message in red."""
    print(f"{_RED}[ERROR] {msg}{_RESET}")
    _write_to_file("ERROR", msg)


def log_debug(msg: str) -> None:
    """Log debug message in gray. Only shown when DEBUG=true in env."""
    if os.environ.get("DEBUG", "").lower() == "true":
        print(f"{_GRAY}[DEBUG] {msg}{_RESET}")
    _write_to_file("DEBUG", msg)
