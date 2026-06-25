# RQ2 Runbook: Real-World PR/BFC Evaluation

## Research Question

**RQ2. How well do LLM-generated postconditions generalize to real-world Python libraries and bug-fixing commits?**

This RQ moves from HumanEval+/EvalPlus tasks to real-world bug-fixing commits and PRs. The main unit is an extracted changed Python function with paired pre-commit and post-commit versions.

## Current Scope

Start with Marshmallow because the repository, candidate PR list, checkout cache, extractor, post/pre executor, and function-level AST mutation backend are already wired into `paper_eval.run_pr_eval`.

Primary candidate set:

```text
bug_detector/data/results/marshmallow-code_marshmallow_candidate_prs_50.json
```

Initial benchmark matrix:

| Method | Mode | Model | Purpose |
| --- | --- | --- | --- |
| nl2postcond | single-pass | llama4-scout | Original baseline prompt/parser from bug_detector |
| SpecMind | singlepass | llama4-scout | Same SpecMind prompt/parser without feedback loop |
| SpecMind | multiturn, tau-style feedback via post/mutation evaluation | llama4-scout | Main method |

Keep the first full RQ2 pass to one model. Add more models only after Marshmallow extraction, detectability, and mutation summaries are stable.

## Metrics

Use `paper_eval/runs/<run_id>/summary.json` as the source of truth.

Report these metrics in the RQ2 table:

- `total_samples`
- `post_pass_rate`
- `overall_bfc_detection_rate`
- `post_pass_conditioned_detection_rate`
- `detectable_samples`
- `detectable_bfc_detection_rate`
- `micro_mutation_score_valid`
- `mutant_error_rate`
- `avg_attempts`
- `total_tokens`
- `exclusion_reasons`

Interpretation rule: overall BFC detection and detectable BFC detection must be separated. If `detectable_samples` is low, the test suite may not expose the pre/post behavioral difference for the extracted function, so a low overall BFC score is not automatically a pure generation failure.

## Completed Smoke

This command was run successfully without LLM calls:

```powershell
python -m paper_eval.run_pr_eval `
  --repo marshmallow-code/marshmallow `
  --prs 2799,2909,2903 `
  --method specmind `
  --mode multiturn `
  --mock-postcondition "assert True" `
  --skip-mutation `
  --max-functions 3 `
  --run-id rq2_smoke_mock_pr3_extract_detectability
```

Result:

- Run: `paper_eval/runs/rq2_smoke_mock_pr3_extract_detectability`
- Samples: 3
- Post pass: 3/3
- Bug detected: 0/3
- Detectable samples: 0/3
- Exclusions: 66 (`function_unchanged`, `unsupported_dunder_method`, `docs_or_tests_only_file`)

This confirms checkout, extraction, assertion injection, post/pre execution, summaries, and exclusions are functioning in the RQ2 runner.

## Environment

Set OpenRouter through the OpenAI-compatible client variables:

```powershell
$env:OPENROUTER_API_KEY = "<KEY>"
$env:OPENAI_API_KEY = $env:OPENROUTER_API_KEY
$env:OPENAI_BASE_URL = "https://openrouter.ai/api/v1"
```

Sanity check:

```powershell
Invoke-RestMethod "$env:OPENAI_BASE_URL/models" -Headers @{
  Authorization = "Bearer $env:OPENAI_API_KEY"
}
```

## Pilot LLM Runs

Run a small, comparable pilot before spending on PR50:

```powershell
python -m paper_eval.run_pr_eval `
  --repo marshmallow-code/marshmallow `
  --prs 2799,2909,2903 `
  --method nl2postcond `
  --model-alias llama4-scout `
  --max-mutants 10 `
  --max-functions 3 `
  --run-id rq2_pilot_nl2postcond_llama4_pr3_mu10

python -m paper_eval.run_pr_eval `
  --repo marshmallow-code/marshmallow `
  --prs 2799,2909,2903 `
  --method specmind `
  --mode singlepass `
  --model-alias llama4-scout `
  --max-mutants 10 `
  --max-functions 3 `
  --run-id rq2_pilot_specmind_llama4_singlepass_pr3_mu10

python -m paper_eval.run_pr_eval `
  --repo marshmallow-code/marshmallow `
  --prs 2799,2909,2903 `
  --method specmind `
  --mode multiturn `
  --model-alias llama4-scout `
  --max-turns 12 `
  --max-mutants 10 `
  --max-functions 3 `
  --run-id rq2_pilot_specmind_llama4_multiturn_pr3_mu10
```

Compare the three `summary.json` files before scaling.

## Full Marshmallow PR50 Runs

After the pilot succeeds:

```powershell
python -m paper_eval.run_pr_eval `
  --repo marshmallow-code/marshmallow `
  --max-prs 50 `
  --method nl2postcond `
  --model-alias llama4-scout `
  --max-mutants 20 `
  --run-id rq2_marshmallow_pr50_nl2postcond_llama4_mu20

python -m paper_eval.run_pr_eval `
  --repo marshmallow-code/marshmallow `
  --max-prs 50 `
  --method specmind `
  --mode singlepass `
  --model-alias llama4-scout `
  --max-mutants 20 `
  --run-id rq2_marshmallow_pr50_specmind_llama4_singlepass_mu20

python -m paper_eval.run_pr_eval `
  --repo marshmallow-code/marshmallow `
  --max-prs 50 `
  --method specmind `
  --mode multiturn `
  --model-alias llama4-scout `
  --max-turns 12 `
  --max-mutants 20 `
  --run-id rq2_marshmallow_pr50_specmind_llama4_multiturn_mu20
```

## Next Checks

Before using results in the paper:

- Inspect `exclusions.jsonl` to quantify how many PRs/functions are excluded and why.
- Check `detectable_samples`; if it remains near zero, add a detectability-focused subset or manual BFC observability analysis.
- Sample postconditions with `post_pass=True`, `bug_detected=False`, and low mutation score for RQ3 taxonomy.
- Keep all model ids, aliases, max-mutants, and run ids in the table footnotes.

## Library Dataset Evaluation

For the four extracted library datasets in `datasets/`, use:

```text
paper_eval/run_dataset_eval.py
```

Supported dataset names:

```text
marshmallow, pandas, numpy, keras
```

This runner uses `*_problems.jsonl + *_tests.jsonl`, injects a generated postcondition through a pytest plugin, runs linked pytest nodeids, and writes the same paper-eval artifacts:

```text
manifest.json
samples.jsonl
summary.json
samples.csv
llm_outputs.py
exclusions.jsonl
```

Important: the runner performs a baseline check with `assert True` by default. If a problem's linked tests do not pass even with the neutral assertion, it is excluded as `baseline_tests_fail`. This keeps method correctness from being polluted by broken/incompatible linked tests.

Smoke test Marshmallow without LLM calls:

```powershell
python -m paper_eval.run_dataset_eval `
  --dataset marshmallow `
  --method specmind `
  --mode singlepass `
  --mock-postcondition "assert True" `
  --max-tests-per-problem 1 `
  --skip-mutation `
  --run-id rq2_dataset_smoke_marshmallow_assert_true
```

Small LLM pilot:

```powershell
python -m paper_eval.run_dataset_eval `
  --dataset marshmallow `
  --method nl2postcond `
  --model-alias llama4-scout `
  --limit 3 `
  --max-tests-per-problem 1 `
  --skip-mutation `
  --run-id rq2_dataset_pilot_marshmallow_nl2_llama4

python -m paper_eval.run_dataset_eval `
  --dataset marshmallow `
  --method specmind `
  --mode singlepass `
  --model-alias llama4-scout `
  --limit 3 `
  --max-tests-per-problem 1 `
  --skip-mutation `
  --run-id rq2_dataset_pilot_marshmallow_specmind_single_llama4

python -m paper_eval.run_dataset_eval `
  --dataset marshmallow `
  --method specmind `
  --mode multiturn `
  --model-alias llama4-scout `
  --max-turns 12 `
  --limit 3 `
  --max-tests-per-problem 1 `
  --skip-mutation `
  --run-id rq2_dataset_pilot_marshmallow_specmind_multi_llama4
```

Mutation/completeness pilot:

```powershell
python -m paper_eval.run_dataset_eval `
  --dataset marshmallow `
  --method specmind `
  --mode singlepass `
  --model-alias llama4-scout `
  --limit 3 `
  --max-tests-per-problem 1 `
  --max-mutants 5 `
  --run-id rq2_dataset_pilot_marshmallow_specmind_single_llama4_mu5
```

Mutation backend options:

```text
--mutation-backend ast             # default, native Windows, fast function-level AST mutants
--mutation-backend mutmut-file     # file-level mutmut export, requires Linux/WSL/Docker
--mutation-backend mutmut-function # isolate one function, run mutmut, inject mutated function back
```

The `mutmut-file` backend reuses the bug_detector/Patch-Guru-style exporter:

```text
bug_detector/src/mutator/exporter.py::export_mutants_for_function
```

It copies the repo checkout into `paper_eval/runs/<run_id>/mutmut_exports/.../workspace`, runs:

```text
mutmut run --paths-to-mutate <target_file> --runner "<linked pytest command>"
mutmut show <id>
mutmut apply <id>
```

and exports changed function bodies into:

```text
paper_eval/runs/<run_id>/mutmut_exports/<task>/<problem>/mutants/<id>.json
```

On native Windows, mutmut currently reports that file-level mutation requires WSL. In that case the run records a mutation error instead of silently reporting zero mutants. Use AST backend for native Windows smoke/full runs, or run `mutmut-file` inside WSL/Linux/Docker.

For the RQ2 assertion setting, `mutmut-function` is the closest match: each problem asks for one function-level assertion, so this backend writes only that function plus its import prelude into `target_func.py`, runs mutmut on that isolated file, exports mutated function bodies, then evaluates those bodies by monkeypatching the original repo function during the linked pytest run.

Mutmut smoke command:

```powershell
python -m paper_eval.run_dataset_eval `
  --dataset marshmallow `
  --repo-root C:\specmind\paper_eval\.cache\clones\marshmallow-code\marshmallow\latest `
  --tasks marshmallow.utils.timedelta_to_microseconds `
  --method specmind `
  --mode singlepass `
  --mock-postcondition "assert isinstance(return_value, int)" `
  --max-tests-per-problem 1 `
  --mutation-backend mutmut-file `
  --max-mutants 1 `
  --mutmut-run-timeout 120 `
  --run-id rq2_dataset_smoke_mutmut_timedelta_1
```

Docker mutmut command from Windows:

```powershell
docker build -f C:\specmind\paper_eval\Dockerfile.dataset `
  -t specmind-paper-eval-mutmut `
  C:\specmind

docker run --rm `
  -v C:\specmind:/workspace `
  -w /workspace `
  specmind-paper-eval-mutmut `
  bash -lc "python -m pip install -q simplejson -e /workspace/paper_eval/.cache/clones/marshmallow-code/marshmallow/latest && python -m paper_eval.run_dataset_eval --dataset marshmallow --repo-root /workspace/paper_eval/.cache/clones/marshmallow-code/marshmallow/latest --tasks marshmallow.utils.timedelta_to_microseconds --method specmind --mode singlepass --mock-postcondition 'assert return_value >= 0' --max-tests-per-problem 1 --mutation-backend mutmut-file --max-mutants 5 --mutmut-run-timeout 300 --output-root /workspace/paper_eval/runs --run-id rq2_docker_mutmut_timedelta_specmind_single_mu5"
```

Docker isolated-function mutmut command:

```powershell
docker run --rm `
  -v C:\specmind:/workspace `
  -w /workspace `
  specmind-paper-eval-mutmut `
  bash -lc "python -m pip install -q simplejson -e /workspace/paper_eval/.cache/clones/marshmallow-code/marshmallow/latest && python -m paper_eval.run_dataset_eval --dataset marshmallow --repo-root /workspace/paper_eval/.cache/clones/marshmallow-code/marshmallow/latest --tasks marshmallow.utils.timedelta_to_microseconds --method specmind --mode singlepass --mock-postcondition 'assert return_value >= 0' --max-tests-per-problem 1 --mutation-backend mutmut-function --max-mutants 5 --mutmut-run-timeout 300 --output-root /workspace/paper_eval/runs --run-id rq2_docker_mutmut_function_timedelta_specmind_single_mu5"
```

Reuse existing LLM outputs and only rerun correctness/mutation:

```powershell
docker run --rm `
  -v C:\specmind:/workspace `
  -w /workspace `
  specmind-paper-eval-mutmut `
  bash -lc "python -m pip install -q simplejson -e /workspace/paper_eval/.cache/clones/marshmallow-code/marshmallow/latest && python -m paper_eval.run_dataset_eval --dataset marshmallow --repo-root /workspace/paper_eval/.cache/clones/marshmallow-code/marshmallow/latest --reuse-samples /workspace/paper_eval/runs/rq2_dataset_marshmallow_latest_specmind_single_llama4_maxtest1/samples.jsonl --tasks marshmallow.utils.timedelta_to_microseconds --max-tests-per-problem 1 --mutation-backend mutmut-function --max-mutants 3 --mutmut-run-timeout 300 --output-root /workspace/paper_eval/runs --run-id rq2_reuse_smoke_specmind_single_timedelta_mutmut_function_mu3"
```

Full Marshmallow reuse commands for the three current Llama 4 Scout runs:

```powershell
docker run --rm `
  -v C:\specmind:/workspace `
  -w /workspace `
  specmind-paper-eval-mutmut `
  bash -lc "python -m pip install -q simplejson -e /workspace/paper_eval/.cache/clones/marshmallow-code/marshmallow/latest && python -m paper_eval.run_dataset_eval --dataset marshmallow --repo-root /workspace/paper_eval/.cache/clones/marshmallow-code/marshmallow/latest --reuse-samples /workspace/paper_eval/runs/rq2_dataset_marshmallow_latest_nl2_llama4_maxtest1/samples.jsonl --max-tests-per-problem 1 --mutation-backend mutmut-function --max-mutants 20 --mutmut-run-timeout 300 --output-root /workspace/paper_eval/runs --run-id rq2_marshmallow_latest_nl2_llama4_reuse_mutmut_function_mu20"

docker run --rm `
  -v C:\specmind:/workspace `
  -w /workspace `
  specmind-paper-eval-mutmut `
  bash -lc "python -m pip install -q simplejson -e /workspace/paper_eval/.cache/clones/marshmallow-code/marshmallow/latest && python -m paper_eval.run_dataset_eval --dataset marshmallow --repo-root /workspace/paper_eval/.cache/clones/marshmallow-code/marshmallow/latest --reuse-samples /workspace/paper_eval/runs/rq2_dataset_marshmallow_latest_specmind_single_llama4_maxtest1/samples.jsonl --max-tests-per-problem 1 --mutation-backend mutmut-function --max-mutants 20 --mutmut-run-timeout 300 --output-root /workspace/paper_eval/runs --run-id rq2_marshmallow_latest_specmind_single_llama4_reuse_mutmut_function_mu20"

docker run --rm `
  -v C:\specmind:/workspace `
  -w /workspace `
  specmind-paper-eval-mutmut `
  bash -lc "python -m pip install -q simplejson -e /workspace/paper_eval/.cache/clones/marshmallow-code/marshmallow/latest && python -m paper_eval.run_dataset_eval --dataset marshmallow --repo-root /workspace/paper_eval/.cache/clones/marshmallow-code/marshmallow/latest --reuse-samples /workspace/paper_eval/runs/rq2_dataset_marshmallow_latest_specmind_multi_llama4_maxtest1_clean/samples.jsonl --max-tests-per-problem 1 --mutation-backend mutmut-function --max-mutants 20 --mutmut-run-timeout 300 --output-root /workspace/paper_eval/runs --run-id rq2_marshmallow_latest_specmind_multi_llama4_reuse_mutmut_function_mu20"
```

Confirmed Docker smoke:

```text
Run: paper_eval/runs/rq2_docker_mutmut_timedelta_specmind_single_mu5
mutmut discovered: 71 file-level mutant ids in src/marshmallow/utils.py
function-level exported: 1 mutant for timedelta_to_microseconds
postcondition: assert return_value >= 0
killed/survived/errors: 0/1/0
valid mutation score: 0.00%

Run: paper_eval/runs/rq2_docker_mutmut_function_timedelta_specmind_single_mu5_copyapply
mutmut discovered: 8 isolated-function mutant ids in target_func.py
function-level exported: 5 mutants for timedelta_to_microseconds with --max-mutants 5
postcondition: assert return_value >= 0
killed/survived/errors: 0/5/0
valid mutation score: 0.00%

Run: paper_eval/runs/rq2_reuse_smoke_specmind_single_timedelta_mutmut_function_mu3
reuse-samples: rq2_dataset_marshmallow_latest_specmind_single_llama4_maxtest1/samples.jsonl
function-level exported/evaluated: 3 mutants for timedelta_to_microseconds with --max-mutants 3
LLM calls: 0, original token usage preserved
killed/survived/errors: 0/3/0
valid mutation score: 0.00%
```

Native Windows AST smoke command:

```powershell
python -m paper_eval.run_dataset_eval `
  --dataset marshmallow `
  --repo-root C:\specmind\paper_eval\.cache\clones\marshmallow-code\marshmallow\latest `
  --tasks marshmallow.utils.timedelta_to_microseconds `
  --method specmind `
  --mode singlepass `
  --mock-postcondition "assert isinstance(return_value, int)" `
  --max-tests-per-problem 1 `
  --mutation-backend ast `
  --max-mutants 1 `
  --run-id rq2_dataset_smoke_ast_timedelta_1
```

For Pandas/Numpy, pass `--repo-root` until matching checkouts are added to `paper_eval/.cache/clones`:

```powershell
python -m paper_eval.run_dataset_eval `
  --dataset pandas `
  --repo-root C:\path\to\pandas\checkout `
  --method specmind `
  --mode singlepass `
  --model-alias llama4-scout `
  --limit 3 `
  --max-tests-per-problem 1 `
  --skip-mutation `
  --run-id rq2_dataset_pilot_pandas_specmind_single_llama4
```
