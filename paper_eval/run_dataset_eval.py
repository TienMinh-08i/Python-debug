from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .generators import generate_mock, generate_nl2postcond, generate_specmind
from .io_utils import append_jsonl, read_jsonl, utc_run_id, write_json, write_llm_outputs_py
from .model_config import DEFAULT_MODEL_CONFIG, resolve_model
from .paths import WORKSPACE, ensure_project_paths
from .schemas import GenerationRecord, MutationRecord, SCHEMA_VERSION
from .summarize import write_summary_files

ensure_project_paths()

from specmind_pr_eval.run_specmind_pr_eval import generate_ast_function_mutants  # noqa: E402
from src.injector.code_util import get_function_source  # noqa: E402
from src.models import FunctionInfo  # noqa: E402
from src.mutator.exporter import export_mutants_for_function  # noqa: E402


DATASET_ROOT = WORKSPACE / "datasets"
POSTCONDITION_FAILURE_MARKER = "SPECMIND_DATASET_POSTCONDITION_FAILURE"


@dataclass(frozen=True)
class DatasetSpec:
    name: str
    project: str
    directory: Path
    problems_file: str
    tests_file: str
    curated_file: str | None = None
    default_repo_root: Path | None = None


DATASETS: dict[str, DatasetSpec] = {
    "marshmallow": DatasetSpec(
        name="marshmallow",
        project="marshmallow-code/marshmallow",
        directory=DATASET_ROOT / "out_marshmallow_docfilter",
        problems_file="marshmallow_problems.jsonl",
        tests_file="marshmallow_tests_valid.jsonl",
        curated_file=None,
        default_repo_root=WORKSPACE / "paper_eval/.cache/clones/marshmallow-code/marshmallow/post",
    ),
    "pandas": DatasetSpec(
        name="pandas",
        project="pandas-dev/pandas",
        directory=DATASET_ROOT / "out_pandas",
        problems_file="pandas_problems.jsonl",
        tests_file="pandas_tests.jsonl",
        curated_file="pandas_curated.json",
        default_repo_root=WORKSPACE / "paper_eval/.cache/clones/pandas-dev/pandas/post",
    ),
    "numpy": DatasetSpec(
        name="numpy",
        project="numpy/numpy",
        directory=DATASET_ROOT / "out_numpy",
        problems_file="numpy_problems.jsonl",
        tests_file="numpy_tests.jsonl",
        curated_file="numpy_curated.json",
        default_repo_root=WORKSPACE / "paper_eval/.cache/clones/numpy/numpy/post",
    ),
    "keras": DatasetSpec(
        name="keras",
        project="keras-team/keras",
        directory=DATASET_ROOT / "out_keras",
        problems_file="keras_problems.jsonl",
        tests_file="keras_tests.jsonl",
        curated_file="keras_curated.json",
        default_repo_root=WORKSPACE / "paper_eval/.cache/clones/keras-team/keras/post",
    ),
}


DATASET_PYTEST_PLUGIN = rf'''
from __future__ import annotations

import base64
import functools
import importlib
import inspect
import json
import os
import traceback


MARKER = {POSTCONDITION_FAILURE_MARKER!r}


def _loads_env_json(name):
    return json.loads(base64.b64decode(os.environ[name]).decode("utf-8"))


def _loads_env_text(name):
    value = os.environ.get(name)
    if not value:
        return None
    return base64.b64decode(value).decode("utf-8")


def _module_name(problem):
    module = problem.get("module")
    if module:
        if module.endswith(".__init__"):
            module = module[: -len(".__init__")]
        return module
    problem_id = problem.get("problem_id") or problem.get("qualified_name") or ""
    parts = problem_id.split(".")
    if len(parts) < 2:
        raise RuntimeError(f"Cannot infer module from problem_id={{problem_id!r}}")
    return ".".join(parts[:-1])


def _function_name(problem):
    return (
        problem.get("function_name")
        or problem.get("entry_point")
        or (problem.get("problem_id") or "").split(".")[-1]
    )


def _resolve_owner(problem):
    problem_id = problem.get("problem_id") or problem.get("qualified_name") or ""
    if not problem.get("module") and problem_id:
        parts = problem_id.split(".")
        last_error = None
        for split_at in range(len(parts) - 1, 0, -1):
            module_name = ".".join(parts[:split_at])
            try:
                module = importlib.import_module(module_name)
            except Exception as exc:
                last_error = exc
                continue
            owner = module
            for part in parts[split_at:-1]:
                owner = getattr(owner, part)
            return module, owner, parts[-1]
        raise RuntimeError(f"Cannot resolve problem_id={{problem_id!r}}: {{last_error}}")

    module = importlib.import_module(_module_name(problem))
    owner = module
    parent_name = problem.get("parent_name")
    if parent_name:
        for part in str(parent_name).split("."):
            if part:
                owner = getattr(owner, part)
    return module, owner, _function_name(problem)


def _raw_descriptor(owner, attr_name):
    if inspect.isclass(owner):
        return owner.__dict__.get(attr_name)
    return getattr(owner, attr_name)


def _unwrap_descriptor(raw, resolved):
    if isinstance(raw, staticmethod):
        return raw.__func__, staticmethod
    if isinstance(raw, classmethod):
        return raw.__func__, classmethod
    return resolved, None


def _compile_mutant(mutant_source, original, module, attr_name):
    namespace = dict(getattr(original, "__globals__", module.__dict__))
    namespace.update(module.__dict__)
    exec(mutant_source, namespace, namespace)
    mutant = namespace.get(attr_name)
    if mutant is None:
        raise RuntimeError(f"Mutant source did not define {{attr_name!r}}")
    return mutant


def _run_postcondition(target, module, postcondition, args, kwargs, return_value):
    namespace = dict(getattr(target, "__globals__", module.__dict__))
    try:
        signature = inspect.signature(target)
        bound = signature.bind_partial(*args, **kwargs)
        bound.apply_defaults()
        namespace.update(bound.arguments)
    except Exception:
        namespace["args"] = args
        namespace["kwargs"] = kwargs
    namespace["return_value"] = return_value
    try:
        exec(postcondition, namespace, namespace)
    except BaseException as exc:
        detail = f"{{type(exc).__name__}}: {{exc}}"
        raise AssertionError(f"{{MARKER}}: {{detail}}") from exc


def pytest_sessionstart(session):
    problem = _loads_env_json("SPECMIND_DATASET_PROBLEM_JSON_B64")
    postcondition = _loads_env_text("SPECMIND_DATASET_POSTCONDITION_B64")
    mutant_source = _loads_env_text("SPECMIND_DATASET_MUTANT_SOURCE_B64")
    if not postcondition:
        raise RuntimeError("Missing postcondition")
    compile(postcondition, "<dataset-postcondition>", "exec")

    try:
        module, owner, attr_name = _resolve_owner(problem)
        resolved = getattr(owner, attr_name)
        raw = _raw_descriptor(owner, attr_name)
        original, descriptor_type = _unwrap_descriptor(raw, resolved)
        target = _compile_mutant(mutant_source, original, module, attr_name) if mutant_source else original

        @functools.wraps(target)
        def wrapped(*args, **kwargs):
            return_value = target(*args, **kwargs)
            _run_postcondition(target, module, postcondition, args, kwargs, return_value)
            return return_value

        replacement = descriptor_type(wrapped) if descriptor_type else wrapped
        setattr(owner, attr_name, replacement)
    except Exception as exc:
        traceback.print_exc()
        raise RuntimeError(f"Failed to install dataset postcondition hook: {{type(exc).__name__}}: {{exc}}") from exc
'''


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def b64_json(data: Any) -> str:
    return base64.b64encode(json.dumps(data, ensure_ascii=False).encode("utf-8")).decode("ascii")


def b64_text(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def load_dataset(spec: DatasetSpec) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    problems_path = spec.directory / spec.problems_file
    tests_path = spec.directory / spec.tests_file
    if not problems_path.exists():
        raise FileNotFoundError(f"Missing problems file: {problems_path}")
    if not tests_path.exists():
        raise FileNotFoundError(f"Missing tests file: {tests_path}")

    problems = read_jsonl(problems_path)
    tests_by_problem_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in read_jsonl(tests_path):
        problem_id = item.get("problem_id")
        if problem_id:
            tests_by_problem_id[problem_id].append(item)

    for index, problem in enumerate(problems):
        problem.setdefault("task_id", f"{spec.name}/{index}")
        problem.setdefault("entry_point", problem.get("function_name") or str(problem.get("problem_id", "")).split(".")[-1])
        problem.setdefault("prompt", problem.get("source") or "")
        problem.setdefault("canonical_solution", "")
    return problems, tests_by_problem_id


def normalize_source_for_match(text: str | None) -> str:
    if not text:
        return ""
    return "\n".join(line.rstrip() for line in str(text).strip().splitlines())


def select_curated_problems(spec: DatasetSpec, problems: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not spec.curated_file:
        raise ValueError(f"Dataset {spec.name} does not define a curated file.")
    curated_path = spec.directory / spec.curated_file
    if not curated_path.exists():
        raise FileNotFoundError(f"Missing curated file: {curated_path}")
    curated_data = json.loads(curated_path.read_text(encoding="utf-8"))
    if not isinstance(curated_data, dict):
        raise ValueError(f"Curated file must be a JSON object keyed by task_id: {curated_path}")

    by_entry: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for problem in problems:
        entry = str(problem.get("entry_point") or problem.get("function_name") or "")
        if entry:
            by_entry[entry].append(problem)

    selected: list[dict[str, Any]] = []
    missing: list[str] = []
    for curated_id, curated in curated_data.items():
        entry = str(curated.get("entry_point") or "")
        candidates = by_entry.get(entry, [])
        curated_prompt = normalize_source_for_match(curated.get("prompt"))
        best = None
        if curated_prompt:
            for candidate in candidates:
                candidate_source = normalize_source_for_match(candidate.get("source") or candidate.get("prompt"))
                if curated_prompt and candidate_source.startswith(curated_prompt):
                    best = candidate
                    break
        if best is None and candidates:
            best = candidates[0]
        if best is None:
            missing.append(f"{curated_id}:{entry}")
            continue
        item = dict(best)
        item["curated_task_id"] = curated_id
        item["curated_entry_point"] = entry
        selected.append(item)

    if missing:
        raise ValueError(f"Could not map {len(missing)} curated tasks for {spec.name}: {missing[:10]}")
    return selected


def function_code(problem: dict[str, Any]) -> str:
    source = problem.get("source")
    if source:
        return str(source).strip() + "\n"
    return (str(problem.get("prompt", "")) + str(problem.get("canonical_solution", ""))).strip() + "\n"


def problem_for_prompt(problem: dict[str, Any]) -> dict[str, Any]:
    code = function_code(problem)
    return {
        "task_id": problem.get("task_id"),
        "entry_point": problem.get("entry_point") or problem.get("function_name"),
        "prompt": code,
        "canonical_solution": "",
    }


def problem_reuse_keys(problem: dict[str, Any]) -> list[str]:
    keys = [
        problem.get("problem_id"),
        problem.get("qualified_name"),
        problem.get("task_id"),
    ]
    return [str(key) for key in keys if key]


def sample_reuse_keys(sample: dict[str, Any]) -> list[str]:
    keys = [
        sample.get("problem_id"),
        sample.get("qualified_name"),
        sample.get("task_id"),
    ]
    return [str(key) for key in keys if key]


def load_reused_samples(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    if not path.exists():
        raise FileNotFoundError(f"Missing reuse samples file: {path}")
    samples = read_jsonl(path)
    indexed: dict[str, dict[str, Any]] = {}
    for sample in samples:
        for key in sample_reuse_keys(sample):
            indexed[key] = sample
    return indexed


def reused_sample_for_problem(
    problem: dict[str, Any],
    reused_samples: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    for key in problem_reuse_keys(problem):
        sample = reused_samples.get(key)
        if sample is not None:
            return sample
    return None


def pytest_nodeid(test: dict[str, Any], *, mode: str = "path") -> str:
    if mode == "module":
        test_id = str(test.get("test_id", ""))
        if "::" in test_id:
            module_name, test_name_part = test_id.split("::", 1)
            return module_name + "::" + "::".join(part for part in test_name_part.split(".") if part)

    test_path = test.get("test_path")
    test_name = test.get("test_name")
    class_name = test.get("class_name")
    if test_path and test_name:
        nodeid = str(test_path).replace("\\", "/")
        if class_name:
            nodeid += f"::{class_name}"
        nodeid += f"::{test_name}"
        return nodeid

    test_id = str(test.get("test_id", ""))
    if "::" not in test_id:
        return test_id
    module_name, test_name_part = test_id.split("::", 1)
    path = module_name.replace(".", "/") + ".py"
    return path + "::" + "::".join(part for part in test_name_part.split(".") if part)


def pytest_nodeid_batches(nodeids: list[str], batch_size: int, max_command_chars: int) -> list[list[str]]:
    batches: list[list[str]] = []
    current: list[str] = []
    current_chars = 0
    for nodeid in nodeids:
        nodeid_chars = len(nodeid) + 3
        if current and (len(current) >= batch_size or current_chars + nodeid_chars > max_command_chars):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(nodeid)
        current_chars += nodeid_chars
    if current:
        batches.append(current)
    return batches


def pythonpath_for(repo_root: Path) -> str:
    candidates = [
        repo_root,
        repo_root / "src",
        repo_root / "keras",
    ]
    existing = [str(path) for path in candidates if path.exists()]
    existing.append(os.environ.get("PYTHONPATH", ""))
    return os.pathsep.join(item for item in existing if item)


def pythonpath_for_mode(repo_root: Path, package_mode: str) -> str:
    if package_mode == "installed":
        return os.environ.get("PYTHONPATH", "")
    return pythonpath_for(repo_root)


def run_pytest_postcondition(
    *,
    problem: dict[str, Any],
    tests: list[dict[str, Any]],
    postcondition: str,
    repo_root: Path,
    timeout: int,
    batch_size: int,
    max_command_chars: int,
    mutant_source: str | None = None,
    package_mode: str = "repo",
    test_nodeid_mode: str = "path",
) -> tuple[str, dict[str, Any]]:
    if not tests:
        return "error", {"error": "No linked tests found.", "logs": []}

    nodeids = [pytest_nodeid(item, mode=test_nodeid_mode) for item in tests]
    with tempfile.TemporaryDirectory(prefix="paper_eval_dataset_") as tmp:
        tmp_dir = Path(tmp)
        plugin_path = tmp_dir / "paper_eval_dataset_plugin.py"
        plugin_path.write_text(DATASET_PYTEST_PLUGIN, encoding="utf-8")

        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(
            item for item in [str(tmp_dir), pythonpath_for_mode(repo_root, package_mode)] if item
        )
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["SPECMIND_DATASET_PROBLEM_JSON_B64"] = b64_json(problem)
        env["SPECMIND_DATASET_POSTCONDITION_B64"] = b64_text(postcondition)
        if mutant_source is not None:
            env["SPECMIND_DATASET_MUTANT_SOURCE_B64"] = b64_text(mutant_source)

        logs: list[str] = []
        invalid_nodeids: list[str] = []
        for batch in pytest_nodeid_batches(nodeids, batch_size, max_command_chars):
            command = [sys.executable, "-m", "pytest", "-q", "-p", "paper_eval_dataset_plugin"]
            if test_nodeid_mode == "module":
                command.append("--pyargs")
            command.extend(batch)
            try:
                proc = subprocess.run(
                    command,
                    cwd=repo_root if package_mode == "repo" else WORKSPACE,
                    env=env,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired as exc:
                log = f"TIMEOUT after {timeout}s\n{exc.stdout or ''}"
                logs.append(log)
                return "timeout", {"logs": logs, "postcondition_marker": POSTCONDITION_FAILURE_MARKER in log}

            log = proc.stdout or ""
            logs.append(log[-8000:])
            if "ERROR: not found:" in log or "no match in any of" in log:
                invalid_nodeids.extend(batch)
                continue
            if proc.returncode != 0:
                return "fail", {
                    "logs": logs,
                    "postcondition_marker": POSTCONDITION_FAILURE_MARKER in log,
                    "invalid_nodeids": invalid_nodeids,
                }

        if invalid_nodeids and len(invalid_nodeids) == len(nodeids):
            return "error", {"error": "No valid pytest nodeids found.", "logs": logs, "invalid_nodeids": invalid_nodeids}
        return "pass", {"logs": logs, "invalid_nodeids": invalid_nodeids}


def mutation_record(
    *,
    spec: DatasetSpec,
    problem: dict[str, Any],
    tests: list[dict[str, Any]],
    postcondition: str,
    repo_root: Path,
    args: argparse.Namespace,
) -> MutationRecord:
    export_manifest: dict[str, Any] | None = None
    if args.mutation_backend == "mutmut-file":
        backend, mutant_items, export_manifest = export_mutmut_mutants(
            spec=spec,
            problem=problem,
            tests=tests,
            repo_root=repo_root,
            args=args,
        )
    elif args.mutation_backend == "mutmut-function":
        backend, mutant_items, export_manifest = export_mutmut_function_mutants(
            spec=spec,
            problem=problem,
            repo_root=repo_root,
            args=args,
        )
        if export_manifest is not None:
            export_manifest.setdefault(
                "mutmut_export_dir",
                str(
                    Path(args._run_dir)
                    / "mutmut_function_exports"
                    / safe_name(problem.get("task_id"))
                    / safe_name(problem.get("problem_id") or problem.get("qualified_name") or problem.get("entry_point"))
                ),
            )
    if args.mutation_backend in {"mutmut-file", "mutmut-function"}:
        if not mutant_items:
            return MutationRecord(
                backend=f"{backend}:{args.mutation_kill_mode}",
                total_mutants=0,
                killed=0,
                survived=0,
                errors=1,
                mutation_score_raw=0.0,
                mutation_score_valid=0.0,
                per_assertion_kills={
                    postcondition: [],
                    "_metadata": {
                        "backend": backend,
                        "kill_mode": args.mutation_kill_mode,
                        "mutmut_total_discovered": (export_manifest or {}).get("total_discovered"),
                        "mutmut_total_exported": (export_manifest or {}).get("total_exported"),
                        "mutmut_export_dir": (export_manifest or {}).get("mutmut_export_dir"),
                        "error": "mutmut exported no function mutants. On Windows, run mutmut backends inside WSL/Linux/Docker.",
                    },
                },
                error="mutmut exported no function mutants. On Windows, run mutmut backends inside WSL/Linux/Docker.",
            )
    elif args.mutation_backend == "ast":
        backend = "dataset-pytest-ast"
        mutant_items = [
            {"id": f"ast_mutant_{index}", "function_source": source, "diff": ""}
            for index, source in enumerate(generate_ast_function_mutants(function_code(problem), args.max_mutants))
        ]
    else:
        raise ValueError(f"Unknown mutation backend: {args.mutation_backend}")

    killed = survived = errors = 0
    per_assertion_kills = {postcondition: []}
    for mutant in mutant_items:
        status, detail = run_pytest_postcondition(
            problem=problem,
            tests=tests,
            postcondition=postcondition,
            repo_root=repo_root,
            timeout=args.pytest_timeout,
            batch_size=args.pytest_batch_size,
            max_command_chars=args.pytest_max_command_chars,
            mutant_source=mutant["function_source"],
            package_mode=args.package_mode,
            test_nodeid_mode=args.test_nodeid_mode,
        )
        marker = bool(detail.get("postcondition_marker"))
        if status == "fail" and (marker or args.mutation_kill_mode == "any-failure"):
            killed += 1
            per_assertion_kills[postcondition].append(mutant["id"])
        elif status in {"pass", "fail"}:
            survived += 1
        else:
            errors += 1

    valid = killed + survived
    metadata = {
        "backend": backend,
        "kill_mode": args.mutation_kill_mode,
        "mutant_ids": [item["id"] for item in mutant_items],
    }
    if export_manifest is not None:
        metadata["mutmut_total_discovered"] = export_manifest.get("total_discovered")
        metadata["mutmut_total_exported"] = export_manifest.get("total_exported")
        metadata["mutmut_export_dir"] = export_manifest.get("mutmut_export_dir") or str(
            Path(args._run_dir)
            / "mutmut_exports"
            / safe_name(problem.get("task_id"))
            / safe_name(problem.get("problem_id") or problem.get("qualified_name") or problem.get("entry_point"))
        )
    per_assertion_kills["_metadata"] = metadata
    return MutationRecord(
        backend=f"{backend}:{args.mutation_kill_mode}",
        total_mutants=len(mutant_items),
        killed=killed,
        survived=survived,
        errors=errors,
        mutation_score_raw=killed / len(mutant_items) if mutant_items else 0.0,
        mutation_score_valid=killed / valid if valid else 0.0,
        per_assertion_kills=per_assertion_kills,
    )


def parse_tasks(raw: str | None) -> set[str] | None:
    if not raw:
        return None
    return {item.strip() for item in raw.split(",") if item.strip()}


def select_problems(problems: list[dict[str, Any]], tasks: set[str] | None, limit: int | None) -> list[dict[str, Any]]:
    if tasks is not None:
        problems = [
            item
            for item in problems
            if str(item.get("task_id")) in tasks or str(item.get("problem_id")) in tasks or str(item.get("qualified_name")) in tasks
        ]
    if limit is not None:
        problems = problems[:limit]
    return problems


def resolve_repo_root(spec: DatasetSpec, explicit: Path | None) -> Path:
    repo_root = explicit or spec.default_repo_root
    if repo_root is None or not repo_root.exists():
        raise SystemExit(
            f"No repo root found for dataset {spec.name!r}. "
            "Pass --repo-root pointing at a checkout whose tests match this dataset."
        )
    return repo_root.resolve()


def safe_name(value: Any) -> str:
    text = str(value or "unknown")
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in text)[:180]


def git_head(repo_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return ""


def repo_path_for_problem(problem: dict[str, Any], repo_root: Path) -> str:
    explicit = problem.get("repo_path")
    if explicit:
        return str(explicit).replace("\\", "/")

    candidates: list[str] = []
    module = problem.get("module")
    if module:
        module_path = str(module).replace(".", "/")
        if module_path.endswith("/__init__"):
            module_path = module_path[: -len("/__init__")]
        candidates.extend([f"src/{module_path}.py", f"{module_path}.py", f"{module_path}/__init__.py"])

    problem_id = str(problem.get("problem_id") or problem.get("qualified_name") or "")
    parts = problem_id.split(".") if problem_id else []
    for split_at in range(len(parts) - 1, 0, -1):
        module_path = "/".join(parts[:split_at])
        candidates.extend([f"src/{module_path}.py", f"{module_path}.py", f"{module_path}/__init__.py"])

    for candidate in candidates:
        if (repo_root / candidate).exists():
            return candidate
    return str(explicit or "").replace("\\", "/")


def dataset_function_info(
    *,
    spec: DatasetSpec,
    problem: dict[str, Any],
    tests: list[dict[str, Any]],
    repo_root: Path,
) -> FunctionInfo:
    return FunctionInfo(
        name=str(problem.get("entry_point") or problem.get("function_name")),
        repo=spec.project,
        pr_number=0,
        docstring=str(problem.get("nl") or ""),
        pre_commit_code=function_code(problem),
        post_commit_code=function_code(problem),
        pre_commit_sha=git_head(repo_root),
        post_commit_sha=git_head(repo_root),
        file_path=repo_path_for_problem(problem, repo_root),
        lineno=int(problem["start_lineno"]) if problem.get("start_lineno") else None,  # type: ignore[arg-type]
        test_files=[pytest_nodeid(item) for item in tests],
    )


def mutmut_runner_for_tests(tests: list[dict[str, Any]]) -> str:
    nodeids = [pytest_nodeid(item) for item in tests]
    if not nodeids:
        return "python -m pytest -x -q --tb=no"
    return "python -m pytest " + " ".join(nodeids) + " -x -q --tb=no"


def load_exported_mutants(export_dir: Path, manifest: dict[str, Any]) -> list[dict[str, str]]:
    mutants: list[dict[str, str]] = []
    for item in manifest.get("mutants", []):
        rel = item.get("file")
        if not rel:
            continue
        path = export_dir / rel
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        source = data.get("function_source")
        if source:
            mutants.append(
                {
                    "id": str(data.get("id") or item.get("id") or len(mutants)),
                    "function_source": source,
                    "diff": str(data.get("diff") or ""),
                }
            )
    return mutants


def import_prelude_for_problem(problem: dict[str, Any], repo_root: Path) -> str:
    path_text = repo_path_for_problem(problem, repo_root)
    if not path_text:
        return ""
    path = repo_root / path_text
    if not path.exists():
        return ""
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast_parse_module(source)
    except Exception:
        return ""

    lines = source.splitlines()
    chunks: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast_import(), ast_import_from())):
            start = getattr(node, "lineno", 0)
            end = getattr(node, "end_lineno", start)
            if start and end:
                chunks.append("\n".join(lines[start - 1 : end]))
    return "\n".join(chunks)


def ast_parse_module(source: str):
    import ast

    return ast.parse(source)


def ast_import():
    import ast

    return ast.Import


def ast_import_from():
    import ast

    return ast.ImportFrom


def discover_isolated_mutant_ids(workspace: Path, max_probe: int) -> list[str]:
    ids: list[str] = []
    misses_after_hit = 0
    for index in range(1, max_probe + 1):
        result = subprocess.run(
            ["mutmut", "show", str(index)],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            ids.append(str(index))
            misses_after_hit = 0
            continue
        if ids:
            misses_after_hit += 1
            if misses_after_hit >= 25:
                break
    return ids


def write_isolated_mutant_json(
    *,
    export_dir: Path,
    mutant_id: str,
    function_source: str,
    diff: str,
) -> dict[str, str]:
    mutants_dir = export_dir / "mutants"
    mutants_dir.mkdir(parents=True, exist_ok=True)
    path = mutants_dir / f"{mutant_id}.json"
    data = {"id": mutant_id, "function_source": function_source, "diff": diff}
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"id": mutant_id, "file": f"mutants/{mutant_id}.json"}


def export_mutmut_function_mutants(
    *,
    spec: DatasetSpec,
    problem: dict[str, Any],
    repo_root: Path,
    args: argparse.Namespace,
) -> tuple[str, list[dict[str, str]], dict[str, Any]]:
    if not shutil_which("mutmut"):
        raise RuntimeError("mutmut executable not found on PATH")

    function_source = textwrap.dedent(function_code(problem)).strip() + "\n"
    entrypoint = str(problem.get("entry_point") or problem.get("function_name"))
    run_dir = Path(args._run_dir)
    export_dir = (
        run_dir
        / "mutmut_function_exports"
        / safe_name(problem.get("task_id"))
        / safe_name(problem.get("problem_id") or problem.get("qualified_name") or entrypoint)
    )
    workspace = export_dir / "workspace"
    if workspace.exists():
        shutil.rmtree(workspace)
    (workspace / "tests").mkdir(parents=True, exist_ok=True)

    prelude = import_prelude_for_problem(problem, repo_root)
    target_source = "\n".join(
        item
        for item in [
            "from __future__ import annotations",
            prelude,
            function_source,
        ]
        if item.strip()
    )
    target_path = workspace / "target_func.py"
    target_path.write_text(target_source, encoding="utf-8")
    (workspace / "tests" / "test_import.py").write_text(
        "def test_import_target_func():\n"
        "    import target_func\n"
        f"    assert hasattr(target_func, {entrypoint!r})\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=workspace, capture_output=True, text=True, timeout=30)
    subprocess.run(["git", "add", "target_func.py", "tests/test_import.py"], cwd=workspace, capture_output=True, text=True, timeout=30)
    subprocess.run(
        ["git", "-c", "user.email=paper-eval@example.invalid", "-c", "user.name=paper-eval", "commit", "-q", "-m", "baseline"],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=30,
    )

    command = [
        "mutmut",
        "run",
        "--paths-to-mutate",
        "target_func.py",
        "--runner",
        "python -m pytest tests -q --tb=no",
        "--tests-dir",
        "tests",
    ]
    try:
        run_result = subprocess.run(
            command,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=args.mutmut_run_timeout,
        )
    except subprocess.TimeoutExpired as exc:
        manifest = {
            "engine": "mutmut-function",
            "function_name": entrypoint,
            "file_path": "target_func.py",
            "original_function_source": function_source,
            "total_discovered": 0,
            "total_exported": 0,
            "error": f"mutmut run timed out after {args.mutmut_run_timeout}s",
            "stdout": (exc.stdout or "")[-4000:],
        }
        export_dir.mkdir(parents=True, exist_ok=True)
        (export_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        return "dataset-pytest-mutmut-function", [], manifest

    if run_result.returncode not in (0, 1, 2, 4, 8):
        manifest = {
            "engine": "mutmut-function",
            "function_name": entrypoint,
            "file_path": "target_func.py",
            "original_function_source": function_source,
            "total_discovered": 0,
            "total_exported": 0,
            "error": f"mutmut run failed with exit={run_result.returncode}",
            "stdout": (run_result.stdout or "")[-4000:],
            "stderr": (run_result.stderr or "")[-4000:],
        }
        export_dir.mkdir(parents=True, exist_ok=True)
        (export_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        return "dataset-pytest-mutmut-function", [], manifest

    max_probe = max(args.max_mutants * 20, 200)
    mutant_ids = discover_isolated_mutant_ids(workspace, max_probe=max_probe)
    pristine_workspace = export_dir / "workspace_pristine"
    if pristine_workspace.exists():
        shutil.rmtree(pristine_workspace)
    shutil.copytree(workspace, pristine_workspace)

    apply_root = export_dir / "apply_workspaces"
    if apply_root.exists():
        shutil.rmtree(apply_root)
    apply_root.mkdir(parents=True, exist_ok=True)

    exported: list[dict[str, str]] = []
    for mutant_id in mutant_ids:
        if len(exported) >= args.max_mutants:
            break
        apply_workspace = apply_root / safe_name(mutant_id)
        if apply_workspace.exists():
            shutil.rmtree(apply_workspace)
        shutil.copytree(pristine_workspace, apply_workspace)
        apply_result = subprocess.run(
            ["mutmut", "apply", mutant_id],
            cwd=apply_workspace,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if apply_result.returncode != 0:
            shutil.rmtree(apply_workspace, ignore_errors=True)
            continue
        mutated_target_source = (apply_workspace / "target_func.py").read_text(encoding="utf-8")
        shutil.rmtree(apply_workspace, ignore_errors=True)
        try:
            mutated_function_source = get_function_source(mutated_target_source, entrypoint, lineno=None)
        except ValueError:
            continue
        if mutated_function_source.strip() == function_source.strip():
            continue
        show_result = subprocess.run(
            ["mutmut", "show", mutant_id],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=30,
        )
        exported.append(
            write_isolated_mutant_json(
                export_dir=export_dir,
                mutant_id=mutant_id,
                function_source=mutated_function_source,
                diff=show_result.stdout if show_result.returncode == 0 else "",
            )
        )

    manifest = {
        "engine": "mutmut-function",
        "repo": spec.project,
        "function_name": entrypoint,
        "file_path": "target_func.py",
        "original_repo_path": repo_path_for_problem(problem, repo_root),
        "original_function_source": function_source,
        "total_discovered": len(mutant_ids),
        "total_exported": len(exported),
        "mutants": exported,
        "stdout": (run_result.stdout or "")[-4000:],
        "stderr": (run_result.stderr or "")[-4000:],
    }
    export_dir.mkdir(parents=True, exist_ok=True)
    (export_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    mutants = load_exported_mutants(export_dir, manifest)
    return "dataset-pytest-mutmut-function", mutants, manifest


def export_mutmut_mutants(
    *,
    spec: DatasetSpec,
    problem: dict[str, Any],
    tests: list[dict[str, Any]],
    repo_root: Path,
    args: argparse.Namespace,
) -> tuple[str, list[dict[str, str]], dict[str, Any]]:
    if not shutil_which("mutmut"):
        raise RuntimeError("mutmut executable not found on PATH")

    func = dataset_function_info(spec=spec, problem=problem, tests=tests, repo_root=repo_root)
    run_dir = Path(args._run_dir)
    export_dir = (
        run_dir
        / "mutmut_exports"
        / safe_name(problem.get("task_id"))
        / safe_name(problem.get("problem_id") or problem.get("qualified_name") or func.name)
    )
    config = {
        "mutation": {
            "engine": "mutmut-file",
            "max_mutants_per_function": args.max_mutants,
            "use_pr_patch": False,
            "mutmut_run_timeout": args.mutmut_run_timeout,
            "mutmut_runner": mutmut_runner_for_tests(tests),
        }
    }
    manifest = export_mutants_for_function(
        func=func,
        post_commit_dir=str(repo_root),
        export_dir=export_dir,
        config=config,
    )
    mutants = load_exported_mutants(export_dir, manifest)
    return "dataset-pytest-mutmut-file", mutants, manifest


def shutil_which(command: str) -> str | None:
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(directory) / command
        if candidate.exists():
            return str(candidate)
        candidate_exe = Path(directory) / f"{command}.exe"
        if candidate_exe.exists():
            return str(candidate_exe)
    return None


def cli_option_present(option: str) -> bool:
    return any(arg == option or arg.startswith(f"{option}=") for arg in sys.argv[1:])


def evaluate_problem(
    *,
    run_id: str,
    spec: DatasetSpec,
    problem: dict[str, Any],
    tests: list[dict[str, Any]],
    repo_root: Path,
    args: argparse.Namespace,
    reused_sample: dict[str, Any] | None = None,
) -> dict[str, Any]:
    selected_tests = tests[: args.max_tests_per_problem] if args.max_tests_per_problem else tests
    code = function_code(problem)
    entrypoint = str(problem.get("entry_point") or problem.get("function_name"))

    def post_evaluator(postcondition: str) -> str:
        status, _detail = run_pytest_postcondition(
            problem=problem,
            tests=selected_tests,
            postcondition=postcondition,
            repo_root=repo_root,
            timeout=args.pytest_timeout,
            batch_size=args.pytest_batch_size,
            max_command_chars=args.pytest_max_command_chars,
            package_mode=args.package_mode,
            test_nodeid_mode=args.test_nodeid_mode,
        )
        return status

    if reused_sample is not None:
        previous_generation = reused_sample.get("generation") or {}
        postcondition = reused_sample.get("postcondition")
        if postcondition is None:
            postcondition = previous_generation.get("postcondition")
        if postcondition:
            post_status = post_evaluator(str(postcondition))
            error = None if post_status == "pass" else f"Reused postcondition status: {post_status}"
        else:
            post_status = str(reused_sample.get("post_status") or previous_generation.get("post_status") or "error")
            error = str(previous_generation.get("error") or "Reused sample has no parsed postcondition.")
        generation = GenerationRecord(
            method=str(previous_generation.get("method") or reused_sample.get("method") or args.method),
            mode=str(previous_generation.get("mode") or reused_sample.get("mode") or args.mode),
            model=str(previous_generation.get("model") or reused_sample.get("model") or args.model),
            postcondition=str(postcondition) if postcondition is not None else None,
            post_status=post_status,
            success=post_status == "pass",
            attempts=int(previous_generation.get("attempts") or 0),
            submissions=int(previous_generation.get("submissions") or 0),
            assertion_turns=int(previous_generation.get("assertion_turns") or 0),
            raw_responses=list(previous_generation.get("raw_responses") or []),
            conversation_history=list(previous_generation.get("conversation_history") or []),
            turns=list(previous_generation.get("turns") or []),
            token_usage=dict(previous_generation.get("token_usage") or {}),
            usage_events=list(previous_generation.get("usage_events") or []),
            error=error,
        )
    elif args.mock_postcondition is not None:
        generation = generate_mock(
            args.mock_postcondition,
            method=args.method,
            mode=args.mode,
            model=args.model,
            evaluate_postcondition=post_evaluator,
        )
    elif args.method == "specmind":
        generation = generate_specmind(
            function_code=code,
            entrypoint=entrypoint,
            evaluate_postcondition=post_evaluator,
            mode=args.mode,
            max_turns=args.max_turns,
            model=args.model,
        )
    else:
        generation = generate_nl2postcond(
            function_code=code,
            entrypoint=entrypoint,
            evaluate_postcondition=post_evaluator,
            model=args.model,
            context=args.nl2postcond_context,
            prompt_v=args.nl2postcond_prompt_v,
            original_problem=problem_for_prompt(problem) if args.nl2postcond_context == "full" else None,
        )

    mutation = None
    if generation.postcondition and generation.post_status == "pass" and not args.skip_mutation:
        try:
            mutation = mutation_record(
                spec=spec,
                problem=problem,
                tests=selected_tests,
                postcondition=generation.postcondition,
                repo_root=repo_root,
                args=args,
            )
        except Exception as exc:
            mutation = MutationRecord(
                backend=f"dataset-pytest-{args.mutation_backend}:{args.mutation_kill_mode}",
                total_mutants=0,
                killed=0,
                survived=0,
                errors=1,
                mutation_score_raw=0.0,
                mutation_score_valid=0.0,
                error=f"{type(exc).__name__}: {exc}",
            )

    sample_id = f"{spec.name}__{problem.get('task_id')}__{problem.get('problem_id')}".replace("/", "_")
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "dataset": spec.name,
        "project": spec.project,
        "method": generation.method,
        "mode": generation.mode if generation.method == "specmind" else "singlepass",
        "model": generation.model,
        "sample_id": sample_id,
        "task_id": problem.get("task_id"),
        "problem_id": problem.get("problem_id"),
        "qualified_name": problem.get("qualified_name"),
        "pr_number": 0,
        "pr_title": "",
        "pre_commit": "",
        "post_commit": "",
        "function_name": entrypoint,
        "file_path": repo_path_for_problem(problem, repo_root),
        "lineno": int(problem.get("start_lineno") or 0),
        "test_files": sorted({str(item.get("test_path") or item.get("test_id") or "") for item in selected_tests}),
        "linked_test_count": len(tests),
        "evaluated_test_count": len(selected_tests),
        "docstring": problem.get("nl") or "",
        "postcondition": generation.postcondition,
        "post_status": generation.post_status,
        "pre_status": "not_applicable",
        "post_pass": generation.post_status == "pass",
        "pre_fail": False,
        "bug_detected": False,
        "detectability": None,
        "mutation": mutation.to_dict() if mutation else None,
        "generation": generation.to_dict(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate postconditions on real-world library datasets.")
    parser.add_argument("--dataset", choices=sorted(DATASETS), required=True)
    parser.add_argument("--repo-root", type=Path, help="Checkout root used to run linked pytest tests.")
    parser.add_argument("--tasks", help="Comma-separated task_id/problem_id/qualified_name filter.")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--curated", action="store_true", help="Run the dataset's curated subset when available.")
    parser.add_argument("--method", choices=["specmind", "nl2postcond"], default="specmind")
    parser.add_argument("--mode", choices=["singlepass", "retry", "multiturn"], default="multiturn")
    parser.add_argument("--model", default=None, help="Raw OpenAI-compatible model id. Overrides model alias.")
    parser.add_argument("--model-alias", help="Model alias from paper_eval/configs/models.json.")
    parser.add_argument("--model-config", type=Path, default=DEFAULT_MODEL_CONFIG)
    parser.add_argument("--max-turns", type=int, default=12)
    parser.add_argument("--nl2postcond-context", choices=["stub", "full"], default="full")
    parser.add_argument("--nl2postcond-prompt-v", choices=["base", "simple"], default="simple")
    parser.add_argument("--mock-postcondition", help="Skip LLM and use this assertion.")
    parser.add_argument(
        "--reuse-samples",
        type=Path,
        help="Reuse parsed postconditions and generation metadata from an existing samples.jsonl instead of calling the LLM.",
    )
    parser.add_argument(
        "--skip-baseline-check",
        action="store_true",
        help="Do not exclude tasks whose linked tests fail with the neutral postcondition 'assert True'.",
    )
    parser.add_argument("--skip-mutation", action="store_true")
    parser.add_argument("--max-mutants", type=int, default=20)
    parser.add_argument(
        "--mutation-backend",
        choices=["ast", "mutmut-file", "mutmut-function"],
        default="ast",
        help="Use fast AST mutants, mutmut on the original file, or mutmut on an isolated function file.",
    )
    parser.add_argument("--mutation-kill-mode", choices=["postcondition-marker", "any-failure"], default="postcondition-marker")
    parser.add_argument("--mutmut-run-timeout", type=int, default=1800)
    parser.add_argument("--max-tests-per-problem", type=int, help="Limit linked pytest nodeids per problem.")
    parser.add_argument("--pytest-timeout", type=int, default=120)
    parser.add_argument("--pytest-batch-size", type=int, default=25)
    parser.add_argument("--pytest-max-command-chars", type=int, default=24000)
    parser.add_argument(
        "--package-mode",
        choices=["repo", "installed"],
        default="repo",
        help="Import target package from the repo checkout or from the installed Python environment.",
    )
    parser.add_argument(
        "--test-nodeid-mode",
        choices=["path", "module"],
        default="path",
        help="Run linked tests as file paths or as installed-package module ids with pytest --pyargs.",
    )
    parser.add_argument("--output-root", type=Path, default=Path("paper_eval/runs"))
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()

    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")

    spec = DATASETS[args.dataset]
    repo_root = resolve_repo_root(spec, args.repo_root)
    reused_samples = load_reused_samples(args.reuse_samples)
    if args.reuse_samples and args.mock_postcondition is not None:
        raise ValueError("--reuse-samples and --mock-postcondition cannot be used together")
    if reused_samples:
        first_reused_sample = next(iter(reused_samples.values()))
        first_generation = first_reused_sample.get("generation") or {}
        if not cli_option_present("--method") and (first_generation.get("method") or first_reused_sample.get("method")):
            args.method = str(first_generation.get("method") or first_reused_sample.get("method"))
        if not cli_option_present("--mode") and (first_generation.get("mode") or first_reused_sample.get("mode")):
            args.mode = str(first_generation.get("mode") or first_reused_sample.get("mode"))
        if (
            not cli_option_present("--model")
            and not cli_option_present("--model-alias")
            and (first_generation.get("model") or first_reused_sample.get("model"))
        ):
            args.model = str(first_generation.get("model") or first_reused_sample.get("model"))
    selection = resolve_model(
        model=args.model,
        model_alias=args.model_alias,
        config_path=args.model_config,
        default_model=os.getenv("SPECMIND_MODEL", "gpt-5.5"),
    )
    args.model = selection.model_id
    if args.method == "nl2postcond":
        args.mode = "singlepass"

    problems, tests_by_problem_id = load_dataset(spec)
    selected_pool = select_curated_problems(spec, problems) if args.curated else problems
    selected = select_problems(selected_pool, parse_tasks(args.tasks), args.limit)
    if reused_samples:
        selected = [problem for problem in selected if reused_sample_for_problem(problem, reused_samples) is not None]

    run_id = args.run_id or utc_run_id(f"dataset_{spec.name}_{args.method}_{args.mode}")
    run_dir = args.output_root / run_id
    args._run_dir = str(run_dir)
    samples_path = run_dir / "samples.jsonl"
    exclusions_path = run_dir / "exclusions.jsonl"

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "dataset": spec.name,
        "project": spec.project,
        "repo_root": str(repo_root),
        "method": args.method,
        "mode": args.mode,
        "output_processing": "specmind_original" if args.method == "specmind" else "nl2postcond_original",
        "model": selection.model_id,
        "model_alias": selection.alias,
        "model_display_name": selection.display_name,
        "model_provider": selection.provider,
        "model_config_path": str(args.model_config),
        "model_config_entry": selection.config_entry,
        "task_count_planned": len(selected),
        "curated": args.curated,
        "curated_file": spec.curated_file if args.curated else None,
        "problems_file": str(spec.directory / spec.problems_file),
        "tests_file": str(spec.directory / spec.tests_file),
        "max_turns": args.max_turns,
        "max_tests_per_problem": args.max_tests_per_problem,
        "skip_mutation": args.skip_mutation,
        "max_mutants": args.max_mutants,
        "mutation_backend": args.mutation_backend,
        "mutation_kill_mode": args.mutation_kill_mode,
        "mutmut_run_timeout": args.mutmut_run_timeout,
        "pytest_timeout": args.pytest_timeout,
        "pytest_batch_size": args.pytest_batch_size,
        "package_mode": args.package_mode,
        "test_nodeid_mode": args.test_nodeid_mode,
        "nl2postcond_context": args.nl2postcond_context,
        "nl2postcond_prompt_v": args.nl2postcond_prompt_v,
        "mock_postcondition": bool(args.mock_postcondition),
        "reuse_samples": str(args.reuse_samples) if args.reuse_samples else None,
        "reuse_sample_count": len({id(sample) for sample in reused_samples.values()}) if reused_samples else 0,
        "skip_baseline_check": args.skip_baseline_check,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json(run_dir / "manifest.json", manifest)

    samples: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = read_jsonl(exclusions_path)
    for index, problem in enumerate(selected, start=1):
        problem_id = str(problem.get("problem_id") or problem.get("task_id"))
        tests = tests_by_problem_id.get(problem_id, [])
        if not tests:
            exclusion = {
                "run_id": run_id,
                "project": spec.project,
                "pr_number": 0,
                "pr_title": "",
                "file_path": problem.get("repo_path"),
                "function_name": problem.get("entry_point") or problem.get("function_name"),
                "reason": "no_linked_tests",
                "detail": problem_id,
            }
            exclusions.append(exclusion)
            append_jsonl(exclusions_path, [exclusion])
            continue

        selected_tests = tests[: args.max_tests_per_problem] if args.max_tests_per_problem else tests
        if not args.skip_baseline_check:
            baseline_status, baseline_detail = run_pytest_postcondition(
                problem=problem,
                tests=selected_tests,
                postcondition="assert True",
                repo_root=repo_root,
                timeout=args.pytest_timeout,
                batch_size=args.pytest_batch_size,
                max_command_chars=args.pytest_max_command_chars,
                package_mode=args.package_mode,
                test_nodeid_mode=args.test_nodeid_mode,
            )
            if baseline_status != "pass":
                logs = baseline_detail.get("logs") or []
                exclusion = {
                    "run_id": run_id,
                    "project": spec.project,
                    "pr_number": 0,
                    "pr_title": "",
                    "file_path": problem.get("repo_path"),
                    "function_name": problem.get("entry_point") or problem.get("function_name"),
                    "reason": "baseline_tests_fail",
                    "detail": json.dumps(
                        {
                            "problem_id": problem_id,
                            "status": baseline_status,
                            "evaluated_test_count": len(selected_tests),
                            "log_tail": "\n".join(str(item) for item in logs)[-2000:],
                        },
                        ensure_ascii=False,
                    ),
                }
                exclusions.append(exclusion)
                append_jsonl(exclusions_path, [exclusion])
                print(f"[{index}/{len(selected)}] excluded {problem_id}: baseline_tests_fail")
                continue

        print(f"[{index}/{len(selected)}] {spec.name} {problem_id} ({len(tests)} linked tests)")
        sample = evaluate_problem(
            run_id=run_id,
            spec=spec,
            problem=problem,
            tests=tests,
            repo_root=repo_root,
            args=args,
            reused_sample=reused_sample_for_problem(problem, reused_samples),
        )
        samples.append(sample)
        append_jsonl(samples_path, [sample])

    summary = write_summary_files(run_dir, samples, exclusions)
    llm_outputs_path = write_llm_outputs_py(run_dir, samples)
    print(json.dumps(summary, indent=2))
    print(f"LLM outputs: {llm_outputs_path}")
    print(f"Run directory: {run_dir.resolve()}")


if __name__ == "__main__":
    main()
