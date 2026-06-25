from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

from .paths import NL2POSTCOND_ORIGINAL_ROOT


_ORIGINAL_MODULE = None


def _install_import_stubs() -> None:
    """Let the original generator module import without installing unused API deps."""
    if "decouple" not in sys.modules:
        decouple = types.ModuleType("decouple")
        decouple.config = lambda key, default=None: default
        sys.modules["decouple"] = decouple

    try:
        import google.genai  # type: ignore  # noqa: F401
    except Exception:
        google = sys.modules.get("google") or types.ModuleType("google")
        genai = types.ModuleType("google.genai")
        genai.Client = object
        genai_types = types.ModuleType("google.genai.types")
        genai_types.GenerateContentConfig = object
        genai.types = genai_types
        google.genai = genai
        sys.modules["google"] = google
        sys.modules["google.genai"] = genai
        sys.modules["google.genai.types"] = genai_types


def _load_original_module():
    global _ORIGINAL_MODULE
    if _ORIGINAL_MODULE is not None:
        return _ORIGINAL_MODULE

    _install_import_stubs()
    vendor = str(NL2POSTCOND_ORIGINAL_ROOT)
    if vendor not in sys.path:
        sys.path.insert(0, vendor)

    module_path = NL2POSTCOND_ORIGINAL_ROOT / "llm_sample_generator.py"
    spec = importlib.util.spec_from_file_location("_paper_eval_nl2postcond_original_llm_sample_generator", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load original nl2postcond module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _ORIGINAL_MODULE = module
    return module


def make_original_experiment_config(
    *,
    prompt_v: Literal["base", "simple"] = "simple",
    has_reference_code: bool = True,
    temperature: float = 0.7,
    model: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        model=model,
        temperature=temperature,
        n_model_responses=1,
        n_per_model_call=1,
        to_generate="postcondition",
        has_reference_code=has_reference_code,
        prompt_v=prompt_v,
        system_prompt=original_system_prompt(),
    )


def original_system_prompt() -> str:
    module = _load_original_module()
    return module.prompts.systemMessage


def prepare_original_prompt(
    problem: dict[str, Any],
    *,
    prompt_v: Literal["base", "simple"] = "simple",
    has_reference_code: bool = True,
    model: str = "",
) -> str:
    module = _load_original_module()
    cfg = make_original_experiment_config(
        prompt_v=prompt_v,
        has_reference_code=has_reference_code,
        model=model,
    )
    return module.prepare_prompt(cfg, problem)
