from __future__ import annotations

import ast
import os
import re
import textwrap
from typing import Any, Callable, Literal

import openai

from .paths import ensure_project_paths
from .nl2postcond_original_adapter import original_system_prompt, prepare_original_prompt
from .schemas import GenerationRecord
from .specmind_adapter import generate_specmind_postcondition

ensure_project_paths()

from paper_eval.vendor.nl2postcond_original.response_preprocessing import code_sanitize as nl2_code_sanitize  # noqa: E402
from paper_eval.vendor.nl2postcond_original.response_preprocessing import extract_code as nl2_extract_code  # noqa: E402


DEFAULT_TEMPERATURE = float(os.getenv("PAPER_EVAL_TEMPERATURE", "0"))
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def prepare_openai_compatible_env() -> None:
    """Allow OpenRouter credentials while using the OpenAI-compatible SDK."""
    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    if openrouter_key and not os.getenv("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = openrouter_key
    if openrouter_key and not os.getenv("OPENAI_BASE_URL"):
        os.environ["OPENAI_BASE_URL"] = os.getenv("OPENROUTER_BASE_URL", OPENROUTER_BASE_URL)


def _usage_dict(response) -> dict[str, int | None]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }


def _sum_usage(events: list[dict[str, int | None]]) -> dict[str, int]:
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for event in events:
        for key in totals:
            value = event.get(key)
            if isinstance(value, int):
                totals[key] += value
    return totals


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def extract_assertion(text: str) -> str | None:
    """Extract the first syntactically valid assert block from an LLM response."""
    text = _strip_code_fences(text)
    for tag in ("solution", "assert"):
        match = re.search(rf"<{tag}>(.*?)</{tag}>", text, flags=re.DOTALL)
        if match:
            text = _strip_code_fences(match.group(1))
            break

    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#") or stripped.startswith("import ") or stripped.startswith("from "):
            lines.append(line)
            continue
        if stripped.startswith("assert "):
            lines.append(line)
            candidate = "\n".join(lines).strip()
            try:
                ast.parse(candidate)
                return candidate
            except SyntaxError:
                pass

    if text.strip().startswith("assert "):
        try:
            ast.parse(text.strip())
            return text.strip()
        except SyntaxError:
            return None
    return None


def extract_nl2postcond_assertion(raw_response: str) -> str | None:
    """Parse an nl2postcond response using the original nl2postcond preprocessing path."""
    code = nl2_extract_code(raw_response)
    if not code or not code.strip():
        return None

    sanitized = nl2_code_sanitize(code.strip())
    if sanitized is None:
        return None

    for line in sanitized.splitlines():
        stripped = line.strip()
        if stripped.startswith("assert "):
            try:
                ast.parse(stripped)
                return stripped
            except SyntaxError:
                continue
    return None


def make_docstring_stub(function_code: str) -> tuple[str, str]:
    """Return (entrypoint, stub) with signature/docstring only."""
    source = textwrap.dedent(function_code)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body = []
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                body.append(node.body[0])
            body.append(ast.Pass())
            stub = type(node)(
                name=node.name,
                args=node.args,
                body=body,
                decorator_list=[],
                returns=node.returns,
                type_comment=getattr(node, "type_comment", None),
                lineno=node.lineno,
                col_offset=node.col_offset,
            )
            ast.fix_missing_locations(stub)
            mod = ast.Module(body=[stub], type_ignores=[])
            ast.fix_missing_locations(mod)
            return node.name, ast.unparse(mod)
    raise ValueError("No function definition found.")


def nl2postcond_problem(function_code: str, context: Literal["stub", "full"] = "full") -> dict[str, str]:
    entrypoint, stub = make_docstring_stub(function_code)
    prompt = stub if context == "stub" else textwrap.dedent(function_code).strip()
    return {
        "entry_point": entrypoint,
        "prompt": prompt,
        "canonical_solution": "",
    }


def generate_nl2postcond(
    function_code: str,
    entrypoint: str,
    evaluate_postcondition: Callable[[str], str],
    model: str,
    context: Literal["stub", "full"] = "full",
    prompt_v: Literal["base", "simple"] = "simple",
    original_problem: dict[str, Any] | None = None,
) -> GenerationRecord:
    usage_events: list[dict[str, int | None]] = []
    raw_responses: list[str] = []
    try:
        prepare_openai_compatible_env()
        problem = (
            {
                "entry_point": original_problem["entry_point"],
                "prompt": original_problem["prompt"],
                "canonical_solution": original_problem.get("canonical_solution", ""),
            }
            if original_problem is not None
            else nl2postcond_problem(function_code, context=context)
        )
        prompt = prepare_original_prompt(
            problem,
            prompt_v=prompt_v,
            has_reference_code=context == "full",
            model=model,
        )
        response = openai.OpenAI().chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": original_system_prompt(),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=DEFAULT_TEMPERATURE,
            max_tokens=512,
        )
        usage_events.append(_usage_dict(response))
        raw = (response.choices[0].message.content or "").strip()
        raw_responses.append(raw)
        postcondition = extract_nl2postcond_assertion(raw)
        if not postcondition:
            return GenerationRecord(
                method="nl2postcond",
                mode="singlepass",
                model=model,
                postcondition=None,
                post_status="error",
                success=False,
                attempts=1,
                raw_responses=raw_responses,
                token_usage=_sum_usage(usage_events),
                usage_events=usage_events,
                error="No valid assert statement parsed.",
            )
        post_status = evaluate_postcondition(postcondition)
        return GenerationRecord(
            method="nl2postcond",
            mode="singlepass",
            model=model,
            postcondition=postcondition if post_status == "pass" else postcondition,
            post_status=post_status,
            success=post_status == "pass",
            attempts=1,
            submissions=1,
            assertion_turns=0,
            raw_responses=raw_responses,
            token_usage=_sum_usage(usage_events),
            usage_events=usage_events,
        )
    except Exception as exc:
        return GenerationRecord(
            method="nl2postcond",
            mode="singlepass",
            model=model,
            postcondition=None,
            post_status="error",
            success=False,
            attempts=len(raw_responses),
            raw_responses=raw_responses,
            token_usage=_sum_usage(usage_events),
            usage_events=usage_events,
            error=f"{type(exc).__name__}: {exc}",
        )


def generate_specmind(
    function_code: str,
    entrypoint: str,
    evaluate_postcondition: Callable[[str], str],
    mode: Literal["singlepass", "retry", "multiturn"],
    max_turns: int,
    model: str,
    evaluate_completeness: Callable[[str], dict[str, Any] | None] | None = None,
    completeness_threshold: float | None = None,
) -> GenerationRecord:
    try:
        prepare_openai_compatible_env()
        result = generate_specmind_postcondition(
            function_code=function_code,
            entrypoint=entrypoint,
            evaluate_postcondition=evaluate_postcondition,
            evaluate_completeness=evaluate_completeness,
            completeness_threshold=completeness_threshold,
            mode=mode,
            max_turns=max_turns,
            model=model,
        )
        return GenerationRecord(
            method="specmind",
            mode=mode,
            model=model,
            postcondition=result.postcondition,
            post_status=result.post_status,
            success=result.success,
            attempts=result.attempts,
            submissions=result.submissions,
            assertion_turns=result.assertion_turns,
            raw_responses=result.raw_responses,
            token_usage=result.token_usage,
            usage_events=result.usage_events,
            conversation_history=result.conversation_history,
            turns=result.turns,
        )
    except Exception as exc:
        return GenerationRecord(
            method="specmind",
            mode=mode,
            model=model,
            postcondition=None,
            post_status="error",
            success=False,
            error=f"{type(exc).__name__}: {exc}",
        )


def generate_mock(
    postcondition: str,
    method: str,
    mode: str,
    model: str,
    evaluate_postcondition: Callable[[str], str],
) -> GenerationRecord:
    status = evaluate_postcondition(postcondition)
    return GenerationRecord(
        method=method,
        mode=mode,
        model=model,
        postcondition=postcondition,
        post_status=status,
        success=status == "pass",
        attempts=0,
        submissions=0,
        assertion_turns=0,
        raw_responses=[postcondition],
        token_usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        usage_events=[],
    )
