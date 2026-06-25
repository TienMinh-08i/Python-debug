"""Export mutmut mutants as function source for offline assertion testing."""

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from src.injector.code_util import get_function_source
from src.mutator.function_mutator import generate_function_mutants
from src.mutator.repo_setup import install_repo_test_deps
from src.models import FunctionInfo
from src.tracker.logger import log_info, log_warn, log_error, log_debug


def ensure_clean_git_clone(clone_dir: Path) -> None:
    """Reset tracked files in a git clone (e.g. after manual mutmut apply)."""
    if not (clone_dir / ".git").is_dir():
        return
    result = subprocess.run(
        ["git", "checkout", "--", "."],
        cwd=str(clone_dir),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log_warn(f"git checkout failed in {clone_dir}: {result.stderr[:300]}")
    else:
        log_debug(f"Restored clean tree in {clone_dir}")


def discover_mutant_ids(workspace_dir: str, max_probe: int = 5000) -> list[str]:
    """
    mutmut 2.5 often prints nothing useful from `mutmut results`.
    Probe sequential IDs via `mutmut show <id>` until the first failure.
    """
    workspace = Path(workspace_dir)
    ids: list[str] = []

    for i in range(1, max_probe + 1):
        mutant_id = str(i)
        result = subprocess.run(
            ["mutmut", "show", mutant_id],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            break
        ids.append(mutant_id)

    return ids


def write_pr_patch(
    post_clone_dir: Path,
    pre_sha: str,
    post_sha: str,
    target_file: str,
    patch_path: Path,
) -> bool:
    """Write a unified diff for target_file between pre/post SHAs (PR scope)."""
    result = subprocess.run(
        ["git", "diff", pre_sha, post_sha, "--", target_file],
        cwd=str(post_clone_dir),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        log_warn(
            f"No git diff for {target_file} ({pre_sha[:8]}..{post_sha[:8]})"
        )
        return False

    patch_path.parent.mkdir(parents=True, exist_ok=True)
    patch_path.write_text(result.stdout, encoding="utf-8")
    log_info(f"Wrote PR patch ({len(result.stdout)} bytes) → {patch_path}")
    return True


def run_mutmut_generate(
    workspace_dir: str,
    target_file: str,
    config: dict,
    patch_file: Path | None = None,
) -> bool:
    """Run mutmut on target_file; return False if baseline tests fail."""
    workspace = Path(workspace_dir)
    tests_dir = None
    if (workspace / "tests").exists():
        tests_dir = "tests"
    elif (workspace / "test").exists():
        tests_dir = "test"

    timeout = int(config.get("mutation", {}).get("mutmut_run_timeout", 1800))

    # mutmut defaults to `pytest -x --assert=plain`, which breaks some projects.
    runner = config.get("mutation", {}).get(
        "mutmut_runner",
        "python -m pytest tests/ -x -q --tb=no",
    )
    setup_cfg = workspace / "setup.cfg"
    if not setup_cfg.exists():
        setup_cfg.write_text(
            "[mutmut]\n"
            f"runner={runner}\n"
            f"tests_dir={tests_dir or 'tests'}\n",
            encoding="utf-8",
        )

    cmd = ["mutmut", "run", "--paths-to-mutate", target_file, "--runner", runner]
    if tests_dir:
        cmd.extend(["--tests-dir", tests_dir])
    if patch_file and patch_file.exists():
        cmd.extend(["--use-patch-file", patch_file.name])
        log_info(f"Limiting mutations to PR diff via {patch_file.name}")

    log_info(f"Running {' '.join(cmd)} in {workspace} (timeout={timeout}s)")
    try:
        result = subprocess.run(
            cmd,
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        log_error(f"mutmut run timed out after {timeout}s")
        return False

    if result.returncode not in (0, 2, 4, 8):
        log_error(
            "mutmut run failed (baseline tests or internal error). "
            f"exit={result.returncode}"
        )
        if result.stderr:
            log_error(result.stderr[-2000:])
        elif result.stdout:
            log_error(result.stdout[-2000:])
        return False

    return True


def export_mutants_for_function(
    func: FunctionInfo,
    post_commit_dir: str,
    export_dir: Path,
    config: dict,
) -> dict:
    """
    Copy post clone → workspace, run mutmut, export mutants whose target function changed.

    Writes manifest.json under export_dir and returns the manifest dict.
    """
    mutation_cfg = config.get("mutation", {})
    max_mutants = int(mutation_cfg.get("max_mutants_per_function", 50))
    post_path = Path(post_commit_dir)
    ensure_clean_git_clone(post_path)
    target_file = func.file_path.replace("\\", "/")
    target_path = post_path / target_file
    try:
        original_file = target_path.read_text(encoding="utf-8")
        original_func = get_function_source(
            original_file, func.name, lineno=func.lineno
        )
    except (OSError, ValueError) as e:
        log_error(f"Cannot extract function {func.name}: {e}")
        return _empty_manifest(func, post_commit_dir, export_dir)

    if mutation_cfg.get("engine", "function-ast") != "mutmut-file":
        return _export_function_ast_mutants(
            func=func,
            post_commit_dir=post_commit_dir,
            export_dir=export_dir,
            target_file=target_file,
            original_func=original_func,
            max_mutants=max_mutants,
        )

    workspace_parent = export_dir / "workspace"
    if workspace_parent.exists():
        shutil.rmtree(workspace_parent)
    workspace_parent.mkdir(parents=True, exist_ok=True)

    workspace = workspace_parent

    log_info(f"Copying {post_path} → {workspace}")
    ignore_names = {".git", ".mutmut-cache", "__pycache__", ".pytest_cache"}
    for item in post_path.rglob("*"):
        if any(part in ignore_names for part in item.parts):
            continue
        if item.name.endswith(".bak"):
            continue
        if item.is_dir():
            continue
        if not item.is_file():
            continue
        try:
            rel = item.relative_to(post_path)
            dest = workspace / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest)
        except OSError as e:
            log_warn(f"Skip copy {item}: {e}")

    if not install_repo_test_deps(str(workspace)):
        log_warn("Test dependency install failed; continuing anyway")

    target_file = func.file_path.replace("\\", "/")
    workspace_patch: Path | None = None
    if mutation_cfg.get("use_pr_patch", True):
        patch_path = export_dir / "pr_mutation.patch"
        write_pr_patch(
            post_path,
            func.pre_commit_sha,
            func.post_commit_sha,
            target_file,
            patch_path,
        )

        if patch_path.exists():
            workspace_patch = workspace / patch_path.name
            shutil.copy2(patch_path, workspace_patch)
    else:
        log_info("Not limiting mutations to PR diff (--no-pr-patch)")

    target_path = workspace / target_file
    original_file = target_path.read_text(encoding="utf-8")
    try:
        original_func = get_function_source(
            original_file, func.name, lineno=func.lineno
        )
    except ValueError as e:
        log_error(f"Cannot extract function {func.name}: {e}")
        return _empty_manifest(func, post_commit_dir, export_dir)

    if not run_mutmut_generate(
        str(workspace),
        target_file,
        config,
        patch_file=workspace_patch,
    ):
        return _empty_manifest(func, post_commit_dir, export_dir)

    # mutmut may leave the target file mutated — restore before apply/export
    target_path.write_text(original_file, encoding="utf-8")

    all_ids = discover_mutant_ids(str(workspace))
    log_info(f"mutmut discovered {len(all_ids)} mutant id(s) in {target_file}")

    mutants_dir = export_dir / "mutants"
    mutants_dir.mkdir(parents=True, exist_ok=True)

    exported: list[dict] = []
    for mutant_id in all_ids:
        if len(exported) >= max_mutants:
            log_info(f"Reached max_mutants_per_function={max_mutants}")
            break

        target_path.write_text(original_file, encoding="utf-8")
        apply_result = subprocess.run(
            ["mutmut", "apply", mutant_id],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if apply_result.returncode != 0:
            log_warn(f"mutmut apply {mutant_id} failed")
            continue

        mutated_file = target_path.read_text(encoding="utf-8")
        try:
            mutated_func = get_function_source(
                mutated_file, func.name, lineno=func.lineno
            )
        except ValueError:
            continue

        if mutated_func.strip() == original_func.strip():
            log_debug(f"Skipping mutant {mutant_id} — {func.name} unchanged")
            continue

        show_result = subprocess.run(
            ["mutmut", "show", mutant_id],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=30,
        )
        diff = show_result.stdout if show_result.returncode == 0 else ""

        entry = {
            "id": mutant_id,
            "function_source": mutated_func,
            "diff": diff,
        }
        out_file = mutants_dir / f"{mutant_id}.json"
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(entry, f, indent=2, ensure_ascii=False)
        exported.append({"id": mutant_id, "file": f"mutants/{mutant_id}.json"})

    manifest = {
        "repo": func.repo,
        "pr_number": func.pr_number,
        "function_name": func.name,
        "file_path": target_file,
        "repo_dir": post_commit_dir.replace("\\", "/"),
        "original_function_source": original_func,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "total_discovered": len(all_ids),
        "total_exported": len(exported),
        "mutants": exported,
    }

    manifest_path = export_dir / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    log_info(
        f"Exported {len(exported)} mutant(s) for {func.name} → {manifest_path}"
    )
    return manifest


def _export_function_ast_mutants(
    func: FunctionInfo,
    post_commit_dir: str,
    export_dir: Path,
    target_file: str,
    original_func: str,
    max_mutants: int,
) -> dict:
    """Export mutants generated directly from the target function source."""
    log_info(
        f"Generating function-level mutants for {func.name} "
        f"(max={max_mutants})"
    )
    try:
        generated = generate_function_mutants(original_func, max_mutants)
    except SyntaxError as e:
        log_error(f"Cannot parse function {func.name} for AST mutation: {e}")
        return _empty_manifest(func, post_commit_dir, export_dir)

    mutants_dir = export_dir / "mutants"
    mutants_dir.mkdir(parents=True, exist_ok=True)

    exported: list[dict] = []
    for mutant in generated:
        entry = {
            "id": mutant.id,
            "function_source": mutant.function_source,
            "description": mutant.description,
            "diff": mutant.diff,
        }
        out_file = mutants_dir / f"{mutant.id}.json"
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(entry, f, indent=2, ensure_ascii=False)
        exported.append({"id": mutant.id, "file": f"mutants/{mutant.id}.json"})

    manifest = {
        "repo": func.repo,
        "pr_number": func.pr_number,
        "function_name": func.name,
        "file_path": target_file,
        "repo_dir": post_commit_dir.replace("\\", "/"),
        "original_function_source": original_func,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "engine": "function-ast",
        "total_discovered": len(generated),
        "total_exported": len(exported),
        "mutants": exported,
    }

    export_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = export_dir / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    log_info(
        f"Exported {len(exported)} function-level mutant(s) for "
        f"{func.name} -> {manifest_path}"
    )
    return manifest


def _empty_manifest(
    func: FunctionInfo,
    post_commit_dir: str,
    export_dir: Path,
) -> dict:
    manifest = {
        "repo": func.repo,
        "pr_number": func.pr_number,
        "function_name": func.name,
        "file_path": func.file_path.replace("\\", "/"),
        "repo_dir": post_commit_dir.replace("\\", "/"),
        "original_function_source": func.post_commit_code,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "total_discovered": 0,
        "total_exported": 0,
        "mutants": [],
    }
    export_dir.mkdir(parents=True, exist_ok=True)
    with open(export_dir / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    return manifest
