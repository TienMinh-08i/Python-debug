from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
BUG_DETECTOR_ROOT = REPO_ROOT / "bug_detector"
if str(BUG_DETECTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(BUG_DETECTOR_ROOT))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from specmind_adapter import SpecMindResult, generate_specmind_postcondition

from src.crawler.function_extractor import extract_modified_functions
from src.crawler.pr_crawler import get_candidate_prs, get_pr_by_number
from src.crawler.repo_manager import RepoManager
from src.models import FunctionInfo, MutationResult
from src.pipeline import _run_assertion_on_version
from src.tracker.logger import log_error, log_info, log_warn


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def parse_prs(raw: str | None) -> list[int]:
    if not raw:
        return []
    return [int(item.strip()) for item in raw.split(",") if item.strip()]


def resolve_pr_infos(repo: str, pr_numbers: list[int], config: dict[str, Any]) -> list[dict[str, Any]]:
    if pr_numbers:
        infos = []
        for pr in pr_numbers:
            info = get_pr_by_number(repo, pr, config)
            if info:
                infos.append(info)
            else:
                log_warn(f"PR #{pr} is not a valid candidate or could not be fetched")
        return infos
    return get_candidate_prs(repo, config)


def extract_functions_for_pr(
    repo: str,
    pr_info: dict[str, Any],
    repo_manager: RepoManager,
    config: dict[str, Any],
) -> tuple[list[FunctionInfo], str, str]:
    pre_dir = repo_manager.get_pre_commit_dir(pr_info["pre_sha"])
    post_dir = repo_manager.get_post_commit_dir(pr_info["post_sha"])
    funcs: list[FunctionInfo] = []
    for changed_file in pr_info.get("changed_files", []):
        if not changed_file.endswith(".py"):
            continue
        pr_info_with_repo = {**pr_info, "repo": repo}
        funcs.extend(
            extract_modified_functions(
                pre_commit_dir=pre_dir,
                post_commit_dir=post_dir,
                changed_file=changed_file,
                pr_info=pr_info_with_repo,
                config=config,
            )
        )
    return funcs, pre_dir, post_dir


def run_mutation_for_function(
    func: FunctionInfo,
    post_dir: str,
    postcondition: str,
    config: dict[str, Any],
) -> MutationResult:
    from src.mutator.workspace import MutantWorkspace
    from src.mutator.mutmut_runner import run_mutmut
    from src.mutation_eval.kill_checker import check_kills_for_function

    workspace = MutantWorkspace(
        post_commit_dir=post_dir,
        cache_dir=config.get("cache_dir", ".cache"),
        repo=func.repo,
        pr=func.pr_number,
    )
    with workspace as workspace_dir:
        mutant_ids = run_mutmut(workspace_dir, func.file_path, config)
        limit = config.get("mutation", {}).get("max_mutants_per_function")
        if limit:
            mutant_ids = mutant_ids[: int(limit)]
        if not mutant_ids:
            return MutationResult(func.name, 0, 0, 0, 0)
        return check_kills_for_function(
            fn=func,
            correct_assertions=[postcondition],
            workspace_dir=workspace_dir,
            mutant_ids=mutant_ids,
            config=config,
        )


class _FunctionMutantCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.in_function = False
        self.candidates: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
        if self.in_function:
            return
        self.in_function = True
        self.generic_visit(node)
        self.in_function = False

    visit_AsyncFunctionDef = visit_FunctionDef

    def _add(self, kind: str) -> None:
        if self.in_function:
            self.candidates.append(kind)

    def visit_Compare(self, node: ast.Compare) -> Any:
        if any(isinstance(op, (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Is, ast.IsNot, ast.In, ast.NotIn)) for op in node.ops):
            self._add("compare")
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> Any:
        if isinstance(node.op, (ast.And, ast.Or)):
            self._add("boolop")
        self.generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp) -> Any:
        if isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.FloorDiv, ast.Mod)):
            self._add("binop")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> Any:
        if isinstance(node.value, (bool, int, float, str)) or node.value is None:
            self._add("constant")


class _FunctionMutantApplier(ast.NodeTransformer):
    def __init__(self, target_index: int) -> None:
        self.target_index = target_index
        self.current_index = -1
        self.in_function = False
        self.mutated = False

    def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
        if self.in_function:
            return node
        self.in_function = True
        node = self.generic_visit(node)
        self.in_function = False
        return node

    visit_AsyncFunctionDef = visit_FunctionDef

    def _should_mutate(self) -> bool:
        if not self.in_function:
            return False
        self.current_index += 1
        if self.current_index == self.target_index:
            self.mutated = True
            return True
        return False

    def visit_Compare(self, node: ast.Compare) -> Any:
        node = self.generic_visit(node)
        if any(isinstance(op, (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Is, ast.IsNot, ast.In, ast.NotIn)) for op in node.ops):
            if self._should_mutate():
                node.ops = [_flip_compare(op) for op in node.ops]
        return node

    def visit_BoolOp(self, node: ast.BoolOp) -> Any:
        node = self.generic_visit(node)
        if isinstance(node.op, (ast.And, ast.Or)) and self._should_mutate():
            node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
        return node

    def visit_BinOp(self, node: ast.BinOp) -> Any:
        node = self.generic_visit(node)
        if isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.FloorDiv, ast.Mod)) and self._should_mutate():
            node.op = _flip_binop(node.op)
        return node

    def visit_Constant(self, node: ast.Constant) -> Any:
        if isinstance(node.value, (bool, int, float, str)) or node.value is None:
            if self._should_mutate():
                return ast.copy_location(ast.Constant(value=_flip_constant(node.value)), node)
        return node


def _flip_compare(op: ast.cmpop) -> ast.cmpop:
    if isinstance(op, ast.Eq):
        return ast.NotEq()
    if isinstance(op, ast.NotEq):
        return ast.Eq()
    if isinstance(op, ast.Lt):
        return ast.GtE()
    if isinstance(op, ast.LtE):
        return ast.Gt()
    if isinstance(op, ast.Gt):
        return ast.LtE()
    if isinstance(op, ast.GtE):
        return ast.Lt()
    if isinstance(op, ast.Is):
        return ast.IsNot()
    if isinstance(op, ast.IsNot):
        return ast.Is()
    if isinstance(op, ast.In):
        return ast.NotIn()
    if isinstance(op, ast.NotIn):
        return ast.In()
    return op


def _flip_binop(op: ast.operator) -> ast.operator:
    if isinstance(op, ast.Add):
        return ast.Sub()
    if isinstance(op, ast.Sub):
        return ast.Add()
    if isinstance(op, ast.Mult):
        return ast.FloorDiv()
    if isinstance(op, ast.FloorDiv):
        return ast.Mult()
    if isinstance(op, ast.Mod):
        return ast.FloorDiv()
    return op


def _flip_constant(value: Any) -> Any:
    if isinstance(value, bool):
        return not value
    if value is None:
        return False
    if isinstance(value, int):
        return value + 1 if value != 0 else 1
    if isinstance(value, float):
        return value + 1.0
    if isinstance(value, str):
        return "" if value else "__mutated__"
    return value


def generate_ast_function_mutants(function_code: str, limit: int) -> list[str]:
    tree = ast.parse(function_code)
    collector = _FunctionMutantCollector()
    collector.visit(tree)
    mutants: list[str] = []
    seen: set[str] = {function_code}
    for index, _kind in enumerate(collector.candidates):
        if len(mutants) >= limit:
            break
        tree = ast.parse(function_code)
        applier = _FunctionMutantApplier(index)
        mutated = applier.visit(tree)
        ast.fix_missing_locations(mutated)
        if not applier.mutated:
            continue
        source = ast.unparse(mutated) + "\n"
        if source in seen:
            continue
        try:
            compile(source, "<ast-mutant>", "exec")
        except SyntaxError:
            continue
        seen.add(source)
        mutants.append(source)
    return mutants


def run_ast_mutation_for_function(
    func: FunctionInfo,
    post_dir: str,
    postcondition: str,
    config: dict[str, Any],
) -> MutationResult:
    from src.mutator.function_mutator import generate_function_mutants

    limit = int(config.get("mutation", {}).get("max_mutants_per_function", 20) or 20)
    mutants = generate_function_mutants(func.post_commit_code, limit)
    killed = survived = errors = 0
    per_assertion_kills = {postcondition: []}
    for mutant in mutants:
        status = _run_assertion_on_version(func, mutant.function_source, postcondition, post_dir, config)
        if status == "fail":
            killed += 1
            per_assertion_kills[postcondition].append(mutant.id)
        elif status == "pass":
            survived += 1
        else:
            errors += 1
    return MutationResult(
        function_name=func.name,
        total_mutants=len(mutants),
        killed=killed,
        survived=survived,
        errors=errors,
        per_assertion_kills=per_assertion_kills,
    )


def specmind_result_dict(result: SpecMindResult) -> dict[str, Any]:
    return asdict(result)


def evaluate_function(
    func: FunctionInfo,
    pre_dir: str,
    post_dir: str,
    config: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    log_info(f"SpecMind PR eval: PR #{func.pr_number} {func.name}")

    def post_evaluator(postcondition: str) -> str:
        return _run_assertion_on_version(
            func=func,
            func_code=func.post_commit_code,
            assertion=postcondition,
            repo_dir=post_dir,
            config=config,
        )

    if args.mock_postcondition:
        postcondition = args.mock_postcondition
        post_status = post_evaluator(postcondition)
        specmind = SpecMindResult(
            postcondition=postcondition if post_status == "pass" else None,
            post_status=post_status,
            success=post_status == "pass",
            attempts=0,
            submissions=0,
            assertion_turns=0,
            raw_responses=[],
            token_usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            usage_events=[],
            conversation_history=[],
            turns=[],
        )
    else:
        specmind = generate_specmind_postcondition(
            function_code=func.post_commit_code,
            entrypoint=func.name,
            evaluate_postcondition=post_evaluator,
            mode=args.mode,
            max_turns=args.max_turns,
            model=args.model,
        )
        postcondition = specmind.postcondition
        post_status = specmind.post_status

    pre_status = "not_run"
    bug_detected = False
    mutation: dict[str, Any] | None = None
    if postcondition and post_status == "pass":
        pre_status = _run_assertion_on_version(
            func=func,
            func_code=func.pre_commit_code,
            assertion=postcondition,
            repo_dir=pre_dir,
            config=config,
        )
        bug_detected = pre_status == "fail"
        if not args.skip_mutation:
            try:
                mutation_backend = args.mutation_backend
                if args.mutation_backend in {"ast", "function-ast"}:
                    mutation_result = run_ast_mutation_for_function(func, post_dir, postcondition, config)
                    mutation_backend = "function-ast"
                else:
                    mutation_result = run_mutation_for_function(func, post_dir, postcondition, config)
                    if args.mutation_backend == "auto" and mutation_result.total_mutants == 0:
                        log_warn(f"mutmut produced no mutants for {func.name}; falling back to function-level mutants")
                        mutation_result = run_ast_mutation_for_function(func, post_dir, postcondition, config)
                        mutation_backend = "function-ast"
                mutation = {
                    "function_name": mutation_result.function_name,
                    "backend": mutation_backend,
                    "total_mutants": mutation_result.total_mutants,
                    "killed": mutation_result.killed,
                    "survived": mutation_result.survived,
                    "errors": mutation_result.errors,
                    "mutation_score": mutation_result.mutation_score,
                    "per_assertion_kills": mutation_result.per_assertion_kills,
                }
            except Exception as exc:
                log_error(f"Mutation failed for {func.name}: {exc}")
                mutation = {
                    "function_name": func.name,
                    "total_mutants": 0,
                    "killed": 0,
                    "survived": 0,
                    "errors": 1,
                    "mutation_score": 0.0,
                    "error": str(exc),
                }

    return {
        "repo": func.repo,
        "pr_number": func.pr_number,
        "function_name": func.name,
        "file_path": func.file_path,
        "lineno": func.lineno,
        "test_files": func.test_files,
        "postcondition": postcondition,
        "post_status": post_status,
        "pre_status": pre_status,
        "post_correct": post_status == "pass",
        "bug_detected": bug_detected,
        "specmind": specmind_result_dict(specmind),
        "mutation": mutation,
    }


def summarize(function_results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(function_results)
    post_correct = sum(1 for item in function_results if item.get("post_correct"))
    bug_detected = sum(1 for item in function_results if item.get("bug_detected"))
    mutation_items = [item["mutation"] for item in function_results if item.get("mutation")]
    total_mutants = sum(item.get("total_mutants", 0) for item in mutation_items)
    killed = sum(item.get("killed", 0) for item in mutation_items)
    survived = sum(item.get("survived", 0) for item in mutation_items)
    errors = sum(item.get("errors", 0) for item in mutation_items)
    attempts = [item.get("specmind", {}).get("attempts", 0) for item in function_results]
    submissions = [item.get("specmind", {}).get("submissions", 0) for item in function_results]
    assertion_turns = [item.get("specmind", {}).get("assertion_turns", 0) for item in function_results]
    prompt_tokens = sum(item.get("specmind", {}).get("token_usage", {}).get("prompt_tokens", 0) for item in function_results)
    completion_tokens = sum(
        item.get("specmind", {}).get("token_usage", {}).get("completion_tokens", 0) for item in function_results
    )
    total_tokens = sum(item.get("specmind", {}).get("token_usage", {}).get("total_tokens", 0) for item in function_results)
    avg_mutation = (
        sum(item.get("mutation_score", 0.0) for item in mutation_items) / len(mutation_items)
        if mutation_items
        else 0.0
    )
    return {
        "total_functions": total,
        "post_correct": post_correct,
        "post_correct_rate": post_correct / total if total else 0.0,
        "bug_detected": bug_detected,
        "bug_detection_rate": bug_detected / total if total else 0.0,
        "avg_attempts": sum(attempts) / total if total else 0.0,
        "avg_submissions": sum(submissions) / total if total else 0.0,
        "avg_assertion_turns": sum(assertion_turns) / total if total else 0.0,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "avg_mutation_score": avg_mutation,
        "total_mutants": total_mutants,
        "killed": killed,
        "survived": survived,
        "errors": errors,
        "micro_mutation_score": killed / total_mutants if total_mutants else 0.0,
        "micro_mutation_score_valid": killed / (killed + survived) if (killed + survived) else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate GitHub PR bug-fix functions with SpecMind postconditions.")
    parser.add_argument("--repo", required=True, help="OWNER/REPO, e.g. marshmallow-code/marshmallow")
    parser.add_argument("--prs", help="Comma-separated PR numbers. If omitted, crawl candidates.")
    parser.add_argument("--max-prs", type=int, default=None)
    parser.add_argument("--config", type=Path, default=BUG_DETECTOR_ROOT / "configs" / "config.json")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / ".cache")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output")
    parser.add_argument("--mode", choices=["singlepass", "retry", "multiturn"], default="multiturn")
    parser.add_argument("--max-turns", type=int, default=12)
    parser.add_argument("--model", default=os.getenv("SPECMIND_MODEL", "gpt-5.5"))
    parser.add_argument("--skip-mutation", action="store_true")
    parser.add_argument("--mutation-backend", choices=["auto", "mutmut", "function-ast", "ast"], default="auto")
    parser.add_argument("--mock-postcondition", help="Skip LLM and use this postcondition; useful for smoke tests.")
    parser.add_argument("--max-mutants", type=int, default=None)
    args = parser.parse_args()

    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")

    config = load_config(args.config)
    config["cache_dir"] = str(args.cache_dir.resolve())
    if args.max_prs is not None:
        config.setdefault("crawler", {})["max_prs"] = args.max_prs
    if args.max_mutants is not None:
        config.setdefault("mutation", {})["max_mutants_per_function"] = args.max_mutants

    pr_infos = resolve_pr_infos(args.repo, parse_prs(args.prs), config)
    repo_manager = RepoManager(args.repo, config["cache_dir"])
    all_results: list[dict[str, Any]] = []
    pr_reports: list[dict[str, Any]] = []

    for index, pr_info in enumerate(pr_infos, start=1):
        log_info(f"[{index}/{len(pr_infos)}] PR #{pr_info['pr_number']}: {pr_info.get('title', '')}")
        try:
            funcs, pre_dir, post_dir = extract_functions_for_pr(args.repo, pr_info, repo_manager, config)
        except Exception as exc:
            log_error(f"Failed to extract PR #{pr_info.get('pr_number')}: {exc}")
            continue

        function_results = []
        for func in funcs:
            try:
                result = evaluate_function(func, pre_dir, post_dir, config, args)
                function_results.append(result)
                all_results.append(result)
            except Exception as exc:
                log_error(f"Failed to evaluate {func.name} in PR #{func.pr_number}: {exc}")

        pr_report = {
            "pr_info": pr_info,
            "summary": summarize(function_results),
            "functions": function_results,
        }
        pr_reports.append(pr_report)
        pr_out = args.output_dir / args.repo.replace("/", "_") / f"pr_{pr_info['pr_number']}.json"
        pr_out.parent.mkdir(parents=True, exist_ok=True)
        with pr_out.open("w", encoding="utf-8") as handle:
            json.dump(pr_report, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        log_info(f"Saved PR report to {pr_out}")

    final_report = {
        "repo": args.repo,
        "mode": args.mode,
        "model": args.model,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summarize(all_results),
        "prs": pr_reports,
    }
    final_path = args.output_dir / args.repo.replace("/", "_") / "summary.json"
    final_path.parent.mkdir(parents=True, exist_ok=True)
    with final_path.open("w", encoding="utf-8") as handle:
        json.dump(final_report, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    print(json.dumps(final_report["summary"], indent=2))
    print(f"Saved summary to {final_path}")


if __name__ == "__main__":
    main()
