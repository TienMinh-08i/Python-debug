from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import ROOT


DEFAULT_MODEL_CONFIG = ROOT / "configs" / "models.json"


@dataclass(frozen=True)
class ModelSelection:
    alias: str | None
    display_name: str
    model_id: str
    provider: str
    config_entry: dict[str, Any]


def _normalize(value: str) -> str:
    return value.strip().lower()


def load_model_config(path: Path = DEFAULT_MODEL_CONFIG) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def resolve_model(
    *,
    model: str | None,
    model_alias: str | None,
    config_path: Path = DEFAULT_MODEL_CONFIG,
    default_model: str,
) -> ModelSelection:
    """Resolve a CLI model/model-alias pair to an OpenAI-compatible model id."""
    config = load_model_config(config_path)
    models = config.get("models", {})

    if model_alias:
        wanted = _normalize(model_alias)
        for key, entry in models.items():
            candidates = [key, entry.get("display_name", ""), entry.get("model_id", "")]
            candidates.extend(entry.get("aliases", []))
            if wanted in {_normalize(str(item)) for item in candidates if item}:
                return ModelSelection(
                    alias=key,
                    display_name=str(entry.get("display_name", key)),
                    model_id=str(entry["model_id"]),
                    provider=str(entry.get("provider", "openai-compatible")),
                    config_entry=entry,
                )
        available = ", ".join(sorted(models))
        raise ValueError(f"Unknown model alias {model_alias!r}. Available aliases: {available}")

    model_id = model or default_model
    wanted_id = _normalize(model_id)
    for key, entry in models.items():
        if wanted_id == _normalize(str(entry.get("model_id", ""))):
            return ModelSelection(
                alias=key,
                display_name=str(entry.get("display_name", key)),
                model_id=str(entry["model_id"]),
                provider=str(entry.get("provider", "openai-compatible")),
                config_entry=entry,
            )

    return ModelSelection(
        alias=None,
        display_name=model_id,
        model_id=model_id,
        provider="openai-compatible",
        config_entry={},
    )

