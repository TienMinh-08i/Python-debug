from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parent
VENDOR_ROOT = ROOT / "vendor"
BUG_DETECTOR_ROOT = VENDOR_ROOT / "bug_detector"
SPECMIND_PR_EVAL_ROOT = VENDOR_ROOT / "specmind_pr_eval"
SPECMIND_CORE_ROOT = VENDOR_ROOT / "specmind_core"
SPECMIND_ORIGINAL_ROOT = VENDOR_ROOT / "specmind_original"
NL2POSTCOND_ROOT = BUG_DETECTOR_ROOT / "src" / "nl2postcond"
NL2POSTCOND_ORIGINAL_ROOT = VENDOR_ROOT / "nl2postcond_original"


def ensure_project_paths() -> None:
    """Make vendored project modules importable without external worktree deps."""
    for path in (
        WORKSPACE,
        VENDOR_ROOT,
        BUG_DETECTOR_ROOT,
        SPECMIND_PR_EVAL_ROOT,
        SPECMIND_CORE_ROOT,
        SPECMIND_ORIGINAL_ROOT,
        NL2POSTCOND_ROOT,
        NL2POSTCOND_ORIGINAL_ROOT,
        ROOT,
    ):
        text = str(path)
        if text not in sys.path:
            sys.path.insert(0, text)
