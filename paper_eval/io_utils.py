from __future__ import annotations

import json
from pprint import pformat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def utc_run_id(prefix: str = "run") -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}_{stamp}"


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def append_jsonl(path: Path, items: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(item, ensure_ascii=False))
            handle.write("\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def parse_prs(raw: str | None) -> list[int]:
    if not raw:
        return []
    return [int(item.strip()) for item in raw.split(",") if item.strip()]


def compact_llm_output(sample: dict[str, Any]) -> dict[str, Any]:
    generation = sample.get("generation") or {}
    return {
        "sample_id": sample.get("sample_id"),
        "dataset": sample.get("dataset"),
        "project": sample.get("project"),
        "method": sample.get("method"),
        "mode": sample.get("mode"),
        "model": sample.get("model"),
        "function_name": sample.get("function_name"),
        "file_path": sample.get("file_path"),
        "post_status": sample.get("post_status"),
        "post_pass": sample.get("post_pass"),
        "postcondition": sample.get("postcondition"),
        "raw_responses": generation.get("raw_responses", []),
        "turns": generation.get("turns", []),
        "conversation_history": generation.get("conversation_history", []),
        "token_usage": generation.get("token_usage", {}),
        "usage_events": generation.get("usage_events", []),
        "generation_error": generation.get("error"),
        "notes": sample.get("notes"),
    }


def write_llm_outputs_py(run_dir: Path, samples: list[dict[str, Any]], filename: str = "llm_outputs.py") -> Path:
    outputs = [compact_llm_output(sample) for sample in samples]
    path = run_dir / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    body = pformat(outputs, width=120, sort_dicts=False)
    content = f'''"""Generated LLM outputs for this paper_eval run.

Open this file to inspect raw LLM responses, parsed postconditions, statuses,
and token usage. It is valid Python, so you can also import LLM_OUTPUTS.
"""

from __future__ import annotations

from pprint import pprint


RUN_DIR = {str(run_dir.resolve())!r}
LLM_OUTPUTS = {body}


def passed_outputs():
    return [item for item in LLM_OUTPUTS if item.get("post_pass")]


def failed_outputs():
    return [item for item in LLM_OUTPUTS if not item.get("post_pass")]


if __name__ == "__main__":
    for item in LLM_OUTPUTS:
        print("=" * 88)
        print(f"{{item.get('sample_id')}} | {{item.get('method')}} | {{item.get('post_status')}}")
        print("- postcondition:")
        print(item.get("postcondition"))
        print("- raw_responses:")
        pprint(item.get("raw_responses"))
        if item.get("generation_error"):
            print("- generation_error:")
            print(item.get("generation_error"))
'''
    path.write_text(content, encoding="utf-8")
    return path
