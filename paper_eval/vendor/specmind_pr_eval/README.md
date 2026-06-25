# SpecMind PR Evaluation

Workspace rieng de tich hop SpecMind vao pipeline crawl pull request cua
`bug_detector` ma khong sua source goc.

## Chay voi LLM

Can co `OPENAI_BASE_URL` va `OPENAI_API_KEY` hop le. Neu dung CLIProxyAPI:

```powershell
$env:OPENAI_BASE_URL = "http://127.0.0.1:8317/v1"
$env:OPENAI_API_KEY = "specmind-local-key"
$env:SPECMIND_MODEL = "gpt-5.5"
```

Chay mot PR:

```powershell
cd C:\specmind
python .\specmind_pr_eval\run_specmind_pr_eval.py `
  --repo marshmallow-code/marshmallow `
  --prs 2799 `
  --mode multiturn `
  --max-turns 12 `
  --max-mutants 20
```

## Smoke test khong can LLM

Dung `--mock-postcondition` de test crawler/extractor/evaluator:

```powershell
python .\specmind_pr_eval\run_specmind_pr_eval.py `
  --repo marshmallow-code/marshmallow `
  --prs 2799 `
  --mock-postcondition "assert True" `
  --skip-mutation
```

## Output

- `specmind_pr_eval/output/<owner_repo>/pr_<number>.json`
- `specmind_pr_eval/output/<owner_repo>/summary.json`

Metric chinh:

- `post_correct_rate`: postcondition pass tren post/fixed commit.
- `bug_detection_rate`: postcondition pass tren post commit va fail tren pre commit.
- `avg_mutation_score`: completeness bang mutation testing.
- `avg_attempts`, `avg_submissions`, `avg_assertion_turns`: metric SpecMind.

