from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


SCHEMA_VERSION = "paper-eval-v1"


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class GenerationRecord:
    method: str
    mode: str
    model: str
    postcondition: str | None
    post_status: str
    success: bool
    attempts: int = 0
    submissions: int = 0
    assertion_turns: int = 0
    raw_responses: list[str] = field(default_factory=list)
    conversation_history: list[dict[str, str]] = field(default_factory=list)
    turns: list[dict[str, Any]] = field(default_factory=list)
    token_usage: dict[str, int] = field(default_factory=dict)
    usage_events: list[dict[str, int | None]] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DetectabilityRecord:
    post_tests_status: str
    pre_tests_status: str
    detectable_by_tests: bool | None
    post_tests_stdout: str = ""
    pre_tests_stdout: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MutationRecord:
    backend: str
    total_mutants: int
    killed: int
    survived: int
    errors: int
    mutation_score_raw: float
    mutation_score_valid: float
    per_assertion_kills: dict[str, list[str]] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SampleRecord:
    schema_version: str
    run_id: str
    dataset: str
    project: str
    method: str
    mode: str
    model: str
    sample_id: str
    pr_number: int
    pr_title: str
    pre_commit: str
    post_commit: str
    function_name: str
    file_path: str
    lineno: int
    test_files: list[str]
    docstring: str
    postcondition: str | None
    post_status: str
    pre_status: str
    post_pass: bool
    pre_fail: bool
    bug_detected: bool
    detectability: dict[str, Any] | None
    mutation: dict[str, Any] | None
    generation: dict[str, Any]
    failure_category: str | None = None
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExclusionRecord:
    run_id: str
    project: str
    pr_number: int
    pr_title: str
    file_path: str | None
    function_name: str | None
    reason: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

