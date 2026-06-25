from __future__ import annotations

import importlib.util
import os
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable, Literal, Any

import openai


MODEL_NAME = os.getenv("SPECMIND_MODEL", "gpt-5.5")
TEMPERATURE = float(os.getenv("SPECMIND_TEMPERATURE", "0"))
ORIGINAL_SPECMIND_PATH = (
    Path(__file__).resolve().parents[1]
    / "paper_eval"
    / "vendor"
    / "specmind_original"
    / "generate_and_test_postconditions_general.py"
)


def _load_original_specmind_module():
    if not ORIGINAL_SPECMIND_PATH.exists():
        return None
    spec = importlib.util.spec_from_file_location("specmind_original_general", ORIGINAL_SPECMIND_PATH)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_ORIGINAL_SPECMIND = _load_original_specmind_module()


@dataclass
class SpecMindTurn:
    turn: int
    raw_response: str
    usage: dict[str, int | None]
    postcondition: str | None
    is_submission: bool
    is_assertion: bool
    post_status: str | None
    observation: str
    completeness_score: float | None = None
    completeness_threshold: float | None = None


@dataclass
class SpecMindResult:
    postcondition: str | None
    post_status: str
    success: bool
    attempts: int
    submissions: int
    assertion_turns: int
    raw_responses: list[str]
    token_usage: dict[str, int]
    usage_events: list[dict[str, int | None]]
    conversation_history: list[dict[str, str]]
    turns: list[dict]
    best_completeness_score: float | None = None


PROMPTS = {
    "multiturn": """
### Objective

You are an AI assistant tasked with verifying the correctness of a Python function based solely on its **docstring** and **implementation**.

Your goal is to write **symbolic postconditions** - Python `assert` statements that validate specific behavioral properties of the function's return value, assuming the function has been implemented correctly.

These symbolic postconditions must not reimplement the function, but instead express **concise, meaningful, and checkable properties** of the output.

---

### Exploration Process

You are allowed to iteratively reason and refine symbolic postconditions using the following tools:

#### Turn Types

-   `<think>`: Reflect on the function's specification and infer its intended behavior.

<think>
...reasoning about the function's purpose, structure, expected output constraints, edge cases, etc...
</think>

-   `<assert>`: Propose one symbolic postconditions in Python. Must be a valid `assert` statement, preceded by a brief comment.

<assert>
# Checks that no output element exceeds input elements
assert all(x <= max(data) for x in return_value)
</assert>

-   `<observation>`: Receive feedback from the system about your assertions.

<observation>
Assertions are valid.
<reminder>You has {max_turns} turns remaining.</reminder>
</observation>

-   `<solution>`: When confident (or you only have 0 turn remaining) you must submit a solution, provide your finalized symbolic postcondition.\
Your final `<solution>` should ideally be submitted only when you ensure that it is the most refined and reliable postcondition to be deployed for bug detection in production.

<solution>...</solution>

---

### Interaction Limit

You have a maximum of {max_turns} total turns remaining (any mix of <think>, <assert>, or <observation>).
    - You may submit a `<solution>` at any time when you believe you have a strong postcondition. Multiple submissions are allowed, and each will be treated as a potential candidate for the final solution.
    - If you submit one or more `<solution>` blocks but are still required to continue, this indicates that your current postcondition is not yet fully correct or complete. You must then continue exploring and refining through additional reasoning and `<assert>` checks before submitting another `<solution>`.
    - Use the early rounds to carefully reason about the function and to issue `<assert>` checks that validate your understanding of its behavior.
    - In later rounds, refine your postconditions based on your reasoning and observations.
    - However, avoid submitting <solution> blocks too frequently. Use most of your turns for exploration (<think>, <assert>, <observation>) and only propose a new <solution> after substantial refinement or new insights.\
In particular, do not repeatedly submit <solution> blocks in the final turns. If turns remain after a submission, you are expected to keep exploring and strengthening your reasoning until the very end.
    - Before submitting any <solution>, you must test it internally to ensure: It has no syntax errors; Executing it will not raise an `AssertionError`; It faithfully reflects your reasoning so far. Only then should you submit it as a valid candidate.
    - If no `<solution>` has been submitted by the final turn (0 turns left), you must submit one at that point.
---

### Postcondition Rules

Your task is to write a symbolic postcondition for {entrypoint}. The postcondition should be in Python, and consist of exactly one assert statement. A Python comment explaining the postcondition's meaning should precede it. For variables, the postcondition should only use the input parameters defined in the function stub and a hypothetical return value of the function, which we'll assume is stored in a variable `return_value`.

For string manipulation, Python's `re` (regular expressions) library can be used. If other Python standard library functions are required, include the necessary imports. However, refrain from using external libraries or calling the function itself (in this case, {entrypoint}) within the postcondition.

If the postcondition calls any functions, they should only be those from the functional subset of Python. By this, we mean functions that are pure (i.e., no side effects, depends only on input values) such as `all()`, `len()`, `map()`, `filter()`, etc.

---

### Your Task

You will now be given a Python function `{entrypoint}`:

```python
{function_code}
```

Begin by analyzing it with `<think>`, then proceed to propose assertions using `<assert>`, review feedback with `<observation>`, and finalize using `<solution>` -- **all within a maximum of {max_turns} total turns.**

Let's begin.
""",
    "retry": """
### Objective

You are an AI assistant tasked with verifying the correctness of a Python function based solely on its **docstring** and **implementation**.

Your goal is to write **symbolic postconditions** - Python `assert` statements that validate specific behavioral properties of the function's return value, assuming the function has been implemented correctly.

These symbolic postconditions must not reimplement the function, but instead express **concise, meaningful, and checkable properties** of the output.

---

### The format of your response should be:

-   `<think>`: Reflect on the function's specification and infer its intended behavior.

<think>
...reasoning about the function's purpose, structure, expected output constraints, edge cases, etc...
</think>

-   `<assert>`: Propose one symbolic postconditions in Python. Must be a valid `assert` statement, preceded by a brief comment.

<assert>
# Checks that no output element exceeds input elements
assert all(x <= max(data) for x in return_value)
</assert>
---

### Postcondition Rules

Your task is to write a symbolic postcondition for {entrypoint}. The postcondition should be in Python, and consist of exactly one assert statement. A Python comment explaining the postcondition's meaning should precede it. For variables, the postcondition should only use the input parameters defined in the function stub and a hypothetical return value of the function, which we'll assume is stored in a variable `return_value`.

For string manipulation, Python's `re` (regular expressions) library can be used. If other Python standard library functions are required, include the necessary imports. However, refrain from using external libraries or calling the function itself (in this case, {entrypoint}) within the postcondition.

If the postcondition calls any functions, they should only be those from the functional subset of Python. By this, we mean functions that are pure (i.e., no side effects, depends only on input values) such as `all()`, `len()`, `map()`, `filter()`, etc.

---

### Your Task

You will now be given a Python function `{entrypoint}`:

```python
{function_code}
```

Begin by analyzing it with `<think>`, then proceed to propose assertions using `<assert>`.

Let's begin.
""",
}
PROMPTS["singlepass"] = PROMPTS["retry"]
if _ORIGINAL_SPECMIND is not None:
    PROMPTS = dict(_ORIGINAL_SPECMIND.PROMPTS)


def _client() -> openai.OpenAI:
    return openai.OpenAI()


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


def _extract_tag(text: str, tag: str) -> str | None:
    match = re.search(rf"<{tag}>(.*?)</{tag}>", text, flags=re.DOTALL)
    if match:
        return match.group(1).strip()
    if text.strip().endswith(f"<{tag}>"):
        match = re.search(rf"<{tag}>(.*)", text, flags=re.DOTALL)
        return match.group(1).strip() if match else None
    return None


def parse_response(text: str) -> tuple[str | None, str | None, str | None]:
    if _ORIGINAL_SPECMIND is not None:
        return _ORIGINAL_SPECMIND.parse_model_response(text)
    think = _extract_tag(text, "think")
    assertion = _extract_tag(text, "assert")
    solution = _extract_tag(text, "solution")
    if assertion is None and solution is None and "assert " in text:
        assertion = text.strip()
    return think, assertion, solution


def clean_postcondition(text: str, entrypoint: str) -> str:
    if _ORIGINAL_SPECMIND is not None:
        return _ORIGINAL_SPECMIND.double_check_postcondition(text, entrypoint)
    text = text.strip().strip("`")
    lines = text.splitlines()
    result: list[str] = []
    assignment_index = None
    for index, line in enumerate(lines):
        if re.match(rf"\s*return_value\s*=\s*{re.escape(entrypoint)}\s*\(.*\)", line):
            assignment_index = index
            break
    if assignment_index is None:
        return text
    for line in lines[:assignment_index]:
        if re.match(r"\s*(import|from)\s+\S+", line):
            result.append(line)
    result.extend(lines[assignment_index + 1 :])
    return "\n".join(result).strip()


def count_submissions(raw_responses: list[str], mode: str) -> int:
    tagged = sum(1 for item in raw_responses if _extract_tag(item, "solution"))
    if tagged:
        return tagged
    if mode == "multiturn":
        return 1 if raw_responses else 0
    return len(raw_responses)


def format_prompt(template: str, entrypoint: str, function_code: str, max_turns: int) -> str:
    return template.format(
        entrypoint=entrypoint,
        function_code=function_code,
        codeStubAndDocstring="",
        canonical_solution=function_code,
        max_turns=max_turns,
    )


def generate_specmind_postcondition(
    function_code: str,
    entrypoint: str,
    evaluate_postcondition: Callable[[str], str],
    evaluate_completeness: Callable[[str], dict[str, Any] | None] | None = None,
    completeness_threshold: float | None = None,
    mode: Literal["singlepass", "retry", "multiturn"] = "multiturn",
    max_turns: int = 12,
    model: str | None = None,
) -> SpecMindResult:
    model = model or MODEL_NAME
    prompt = format_prompt(PROMPTS[mode], entrypoint, function_code, max_turns)
    history: list[dict[str, str]] = [
        {
            "role": "system",
            "content": "You are a programming assistant that generates executable python only. You generate correct code, so you only generate code you are sure of. You have Python comments explaining your intent when possible.",
        },
        {"role": "user", "content": prompt},
    ]
    raw_responses: list[str] = []
    usage_events: list[dict[str, int | None]] = []
    turns: list[SpecMindTurn] = []
    best_postcondition: str | None = None
    best_status = "error"
    best_turn_result: dict | None = None
    best_completeness_score: float | None = None

    for turn in range(1, max_turns + 1):
        model_history = [] if mode == "singlepass" else history[2:]
        response = _client().chat.completions.create(
            model=model,
            messages=history[:2] + model_history,
            temperature=TEMPERATURE,
            max_tokens=1024,
            stop=["</assert>", "</solution>"],
        )
        usage = _usage_dict(response)
        usage_events.append(usage)
        raw = (response.choices[0].message.content or "").strip()
        raw_responses.append(raw)
        response_for_parsing = raw
        if "<assert>\n" in response_for_parsing and not response_for_parsing.strip().endswith("</assert>"):
            response_for_parsing += "\n</assert>"
        if "<solution>\n" in response_for_parsing and not response_for_parsing.strip().endswith("</solution>"):
            response_for_parsing += "\n</solution>"

        think, assertion, solution = parse_response(response_for_parsing)
        candidate = solution or assertion
        is_submission = bool(solution)
        is_assertion = bool(assertion and not solution)
        if mode in {"singlepass", "retry"}:
            is_submission = True

        if mode == "multiturn" and turn == max_turns and candidate and not is_submission:
            is_submission = True
            is_assertion = False

        if not candidate:
            observation = "No postcondition was generated. Please provide an assert statement."
            status = None
            history.append({"role": "assistant", "content": response_for_parsing})
            remaining = max_turns - turn
            reminder = f"<reminder>You have {remaining} turns remaining.</reminder>" if mode == "multiturn" else ""
            history.append({"role": "user", "content": f"<observation>\n{observation}\n{reminder}\n</observation>"})
            turns.append(SpecMindTurn(turn, raw, usage, None, is_submission, is_assertion, status, observation))
            if mode == "singlepass":
                best_postcondition = None
                best_status = "error"
                break
            continue

        cleaned = clean_postcondition(candidate, entrypoint)
        status = evaluate_postcondition(cleaned)
        completeness_score: float | None = None
        if status == "pass":
            observation = "Assertions are valid."
            completeness_result = None
            if evaluate_completeness is not None:
                completeness_result = evaluate_completeness(cleaned)
                if completeness_result is not None:
                    completeness_score = float(completeness_result.get("mutation_score_raw", 0.0) or 0.0)
                    score_pct = completeness_score * 100
                    threshold_text = (
                        f"{completeness_threshold:.1f}%"
                        if completeness_threshold is not None
                        else "the target threshold"
                    )
                    if completeness_threshold is None or score_pct >= completeness_threshold:
                        observation = (
                            "Assertions are valid and meet the completeness requirement. "
                            f"Completeness score: {score_pct:.1f}%."
                        )
                    else:
                        observation = (
                            "Assertions are valid, but they are incomplete for bug detection. "
                            f"Completeness score: {score_pct:.1f}%, below the required {threshold_text}. "
                            "Please refine the postcondition so it rejects more buggy implementations while still "
                            "holding for the correct implementation."
                        )

                    if (
                        best_postcondition is None
                        or best_status != "pass"
                        or best_completeness_score is None
                        or completeness_score > best_completeness_score
                    ):
                        best_postcondition = cleaned
                        best_status = status
                        best_turn_result = {"status": status, "completeness": completeness_result}
                        best_completeness_score = completeness_score

            meets_completeness = (
                completeness_threshold is None
                or completeness_score is None
                or completeness_score * 100 >= completeness_threshold
            )
            if is_submission and meets_completeness:
                best_postcondition = cleaned
                best_status = status
                best_turn_result = {"status": status, "completeness": completeness_result}
                best_completeness_score = completeness_score
                history.append({"role": "assistant", "content": response_for_parsing})
                turns.append(
                    SpecMindTurn(
                        turn,
                        raw,
                        usage,
                        cleaned,
                        is_submission,
                        is_assertion,
                        status,
                        observation,
                        completeness_score,
                        completeness_threshold,
                    )
                )
                break
        else:
            observation = (
                "Assertions failed.\n"
                f"Traceback log:\nstatus={status}"
            )
            if is_submission and mode == "multiturn":
                observation += (
                    "\nThe postconditions are unsound. They are overly strict and raise an AssertionError even when "
                    "the code is implemented correctly. This indicates a flaw in the postconditions themselves, "
                    "making them unreliable for verifying code correctness."
                )

        history.append({"role": "assistant", "content": response_for_parsing})
        reminder = ""
        if mode == "multiturn":
            remaining = max_turns - turn
            reminder = f"<reminder>You have {remaining} turns remaining."
            if remaining == 1:
                reminder += " This is your final turn. You must provide a solution now in the `<solution>` block."
            reminder += "</reminder>"
        history.append({"role": "user", "content": f"<observation>\n{observation}{reminder}\n</observation>"})
        turns.append(
            SpecMindTurn(
                turn,
                raw,
                usage,
                cleaned,
                is_submission,
                is_assertion,
                status,
                observation,
                completeness_score,
                completeness_threshold,
            )
        )

        if mode == "singlepass":
            best_postcondition = cleaned
            best_status = status or "error"
            best_turn_result = {"status": status}
            best_completeness_score = completeness_score
            break

        if mode == "retry" and status == "pass":
            best_postcondition = cleaned
            best_status = status
            best_turn_result = {"status": status}
            best_completeness_score = completeness_score
            break

        if best_postcondition is None and turn == max_turns:
            best_postcondition = cleaned
            best_status = status or "error"
            best_turn_result = {"status": status}

    submissions = sum(1 for turn in turns if turn.is_submission)
    if submissions == 0:
        submissions = count_submissions(raw_responses, mode)
    assertion_turns = sum(1 for turn in turns if turn.is_assertion) if mode == "multiturn" else 0
    return SpecMindResult(
        postcondition=best_postcondition,
        post_status=best_status,
        success=best_postcondition is not None and best_status == "pass",
        attempts=len(raw_responses),
        submissions=submissions,
        assertion_turns=assertion_turns,
        raw_responses=raw_responses,
        token_usage=_sum_usage(usage_events),
        usage_events=usage_events,
        conversation_history=history,
        turns=[asdict(turn) for turn in turns],
        best_completeness_score=best_completeness_score,
    )
