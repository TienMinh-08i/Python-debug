# paper_eval

Unified experiment harness for turning the current SpecMind work into a paper-ready controlled study.

This folder is intentionally separate from the exploratory folders. The code needed from earlier experiments has been vendored under `paper_eval/vendor/`, so the main paper runners no longer require sibling worktree folders such as `bug_detector/` or `specmind_pr_eval/` to be present.

Vendored components:

- `paper_eval/vendor/bug_detector/src/*` for PR crawling, checkout, assertion execution, and mutation helpers.
- `paper_eval/vendor/specmind_pr_eval/*` for the SpecMind PR adapter and AST mutation helper.
- `paper_eval/vendor/specmind_original/*` for the original SpecMind prompt/parser logic used by RQ1.
- `paper_eval/vendor/nl2postcond_original/*` for the original nl2postcond prompt/parser logic.

Generated outputs, cloned target repositories, and caches are intentionally not part of the source artifact.

## Setup

Create an environment and install the paper harness dependencies:

```powershell
cd C:\specmind
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -r paper_eval\requirements.txt
```

For RQ2 datasets, the repository contains JSON/JSONL dataset files under `paper_eval/datasets/`. Large `.jsonl` files should be tracked with Git LFS when publishing to GitHub; see the root `.gitattributes`.

For LLM calls through OpenRouter or another OpenAI-compatible endpoint:

```powershell
$env:OPENAI_BASE_URL = "https://openrouter.ai/api/v1"
$env:OPENAI_API_KEY = "<OPENROUTER_OR_COMPATIBLE_API_KEY>"
```

## Outputs

Each run writes to:

```text
paper_eval/runs/<run_id>/
  manifest.json
  samples.jsonl
  exclusions.jsonl
  samples.csv
  summary.json
```

Important metrics in `summary.json`:

- `post_pass_rate`
- `overall_bfc_detection_rate`
- `post_pass_conditioned_detection_rate`
- `detectable_bfc_detection_rate`
- `macro_mutation_score_raw`
- `macro_mutation_score_valid`
- `micro_mutation_score_raw`
- `micro_mutation_score_valid`
- `exclusion_reasons`
- `avg_attempts`, `avg_submissions`, `avg_assertion_turns`
- token usage

## Smoke test without LLM

Run one PR and one extracted function with a mock assertion:

```powershell
cd C:\specmind
python -m paper_eval.run_pr_eval `
  --repo marshmallow-code/marshmallow `
  --prs 2799 `
  --method specmind `
  --mode multiturn `
  --mock-postcondition "assert True" `
  --skip-mutation `
  --skip-detectability `
  --max-functions 1 `
  --run-id smoke_mock
```

## SpecMind run

Use an OpenAI-compatible endpoint, for example CLIProxyAPI:

```powershell
$env:OPENAI_BASE_URL = "http://127.0.0.1:8317/v1"
$env:OPENAI_API_KEY = "specmind-local-key"
$env:SPECMIND_MODEL = "gpt-5.5"

python -m paper_eval.run_pr_eval `
  --repo marshmallow-code/marshmallow `
  --prs 2799 `
  --method specmind `
  --mode multiturn `
  --model $env:SPECMIND_MODEL `
  --max-turns 12 `
  --max-mutants 20
```

## Model aliases

### OpenRouter / Llama 4 Scout

For OpenRouter, keep the model alias `llama4-scout`. Set the OpenRouter key and base URL:

```powershell
$env:OPENROUTER_API_KEY = "<OPENROUTER_API_KEY>"
$env:OPENAI_BASE_URL = "https://openrouter.ai/api/v1"
$env:OPENAI_API_KEY = $env:OPENROUTER_API_KEY
```

`OPENAI_*` here means "OpenAI-compatible API client", not an OpenAI-hosted model. The actual model id remains:

```text
meta-llama/llama-4-scout
```

Check the connection:

```powershell
Invoke-RestMethod "$env:OPENAI_BASE_URL/models" -Headers @{
  Authorization = "Bearer $env:OPENAI_API_KEY"
}
```

Paper model aliases live in:

```text
paper_eval/configs/models.json
```

Currently configured aliases:

| Alias | Display name | Default model id |
| --- | --- | --- |
| `llama4-scout` | `LLama4-Scout` | `meta-llama/llama-4-scout` |
| `gpt-5.4-mini` | `GPT-5.4-mini` | `gpt-5.4-mini` |
| `deepseek-v4-flash` | `DeepSeek-V4-Flash` | `deepseek-v4-flash` |

Use an alias instead of a raw model id:

```powershell
python -m paper_eval.run_pr_eval `
  --repo marshmallow-code/marshmallow `
  --prs 2799 `
  --method specmind `
  --mode multiturn `
  --model-alias llama4-scout `
  --max-turns 12 `
  --max-mutants 20
```

The resolved `model_id`, alias, display name, provider, and full config entry are saved in each run's `manifest.json`.

If your proxy exposes a different model id, edit only `paper_eval/configs/models.json`.

## nl2postcond baseline on the same harness

This is the fair-comparison baseline: same extracted functions, same pre/post evaluator, same mutation backend, same model endpoint.

```powershell
python -m paper_eval.run_pr_eval `
  --repo marshmallow-code/marshmallow `
  --prs 2799 `
  --method nl2postcond `
  --model $env:SPECMIND_MODEL `
  --max-mutants 20
```

By default, nl2postcond now uses the vendored original prompt builder from `paper_eval/vendor/nl2postcond_original/llm_sample_generator.py` with `has_reference_code=True` via `--nl2postcond-context full` and `prompt_v=simple`. Use `--nl2postcond-context stub` or `--nl2postcond-prompt-v base` only as ablations. The chosen values are written to each run's `manifest.json`.

## Suggested first paper run

Use the current Marshmallow PR set first, before scaling to more projects:

```powershell
python -m paper_eval.run_pr_eval `
  --repo marshmallow-code/marshmallow `
  --max-prs 50 `
  --method specmind `
  --mode multiturn `
  --model gpt-5.5 `
  --max-turns 12 `
  --max-mutants 20 `
  --run-id marshmallow_pr50_specmind_multiturn_gpt55

python -m paper_eval.run_pr_eval `
  --repo marshmallow-code/marshmallow `
  --max-prs 50 `
  --method nl2postcond `
  --model gpt-5.5 `
  --max-mutants 20 `
  --run-id marshmallow_pr50_nl2postcond_gpt55
```

Then compare the two `summary.json` files.

## RQ1: EvalPlus/HumanEval+ with Llama4-Scout

RQ1 uses a separate runner:

```text
paper_eval/run_evalplus.py
```

Install the benchmark dependency first:

```powershell
pip install evalplus
```

Set your OpenAI-compatible proxy/API:

```powershell
$env:OPENAI_BASE_URL = "http://127.0.0.1:8317/v1"
$env:OPENAI_API_KEY = "specmind-local-key"
```

Smoke test two tasks without mutation/power evaluation:

```powershell
python -m paper_eval.run_evalplus `
  --method specmind `
  --mode multiturn `
  --model-alias llama4-scout `
  --tasks HumanEval/0,HumanEval/1 `
  --base-only `
  --skip-power-eval `
  --run-id rq1_smoke_specmind_llama4

python -m paper_eval.run_evalplus `
  --method nl2postcond `
  --model-alias llama4-scout `
  --tasks HumanEval/0,HumanEval/1 `
  --base-only `
  --skip-power-eval `
  --run-id rq1_smoke_nl2postcond_llama4
```

Pilot run on the first 20 non-problematic HumanEval+ tasks:

```powershell
python -m paper_eval.run_evalplus `
  --method specmind `
  --mode multiturn `
  --model-alias llama4-scout `
  --limit 20 `
  --base-only `
  --skip-power-eval `
  --run-id rq1_evalplus20_specmind_multiturn_llama4

python -m paper_eval.run_evalplus `
  --method nl2postcond `
  --model-alias llama4-scout `
  --limit 20 `
  --base-only `
  --skip-power-eval `
  --run-id rq1_evalplus20_nl2postcond_llama4
```

Full run:

```powershell
python -m paper_eval.run_evalplus `
  --method specmind `
  --mode multiturn `
  --model-alias llama4-scout `
  --max-turns 12 `
  --completeness-threshold 90 `
  --run-id rq1_evalplus_specmind_multiturn_llama4

python -m paper_eval.run_evalplus `
  --method nl2postcond `
  --model-alias llama4-scout `
  --base-only `
  --skip-power-eval `
  --run-id rq1_evalplus_nl2postcond_llama4
```

Each run writes:

```text
paper_eval/runs/<run_id>/
  manifest.json
  samples.jsonl
  samples.csv
  summary.json
  rq1_summary.json
```

Use `rq1_summary.json` for the RQ1 table. Start with `--skip-power-eval`; only enable mutation completeness after correctness runs are stable and the EvalPlus mutant data is available.

For the SpecMind paper's best RQ1 setting, use `--mode multiturn --max-turns 12 --completeness-threshold 90` without `--skip-power-eval`. This feeds completeness back into the generation loop. Adding `--skip-power-eval` makes the run correctness-only.

## Why this exists

The previous folders already contain useful experiments, but they use different schemas and sometimes different model adapters. This folder makes the evaluation defensible:

- one sample schema,
- one summary schema,
- one pre/post evaluator,
- one mutation backend,
- structured exclusion reasons,
- detectability subset,
- method/model/run manifest.
