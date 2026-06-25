# Code reading và roadmap cho hướng bài nghiên cứu

## 1. Kết luận ngắn

Workspace hiện tại đã đủ nền để viết paper theo hướng:

> Controlled empirical study of LLM-generated postconditions for real-world Python bug detection.

Nhưng chưa nên scale ngay sang Pandas/SciPy/Keras. Việc nên làm trước là chuẩn hóa lại pipeline, schema, baseline và metric để kết quả Marshmallow hiện tại trở thành một experiment có thể defend trước reviewer.

## 2. Các khối code hiện có

### `bug_detector`

Vai trò: pipeline gốc cho PR crawling, function extraction, nl2postcond baseline, assertion injection, pre/post execution, mutation evaluation.

Điểm mạnh:

- Có crawler GitHub PR trong `src/crawler/pr_crawler.py`.
- Có extractor changed functions trong `src/crawler/function_extractor.py`.
- Có model dữ liệu `FunctionInfo`, `AssertionResult`, `MutationResult`.
- Có evaluator pre/post commit trong `src/pipeline.py`.
- Có function-level AST mutants trong `src/mutator/function_mutator.py`.
- Có nl2postcond wrapper trong `src/nl2postcond/interface.py`.

Vấn đề:

- `src/evaluator/metrics.py` gần như rỗng.
- nl2postcond wrapper hiện gắn với Gemini API gốc, trong khi SpecMind dùng OpenAI-compatible client.
- Schema output chưa thống nhất với SpecMind PR eval.
- `AssertionResult.is_correct` đang định nghĩa correct = fail pre + pass post. Với paper, cần tách rõ:
  - post pass / soundness
  - pre fail / real bug detection
  - strict detection = post pass AND pre fail
- Extractor lọc khá mạnh: cần function có docstring đủ dài và test file reference function name. Đây là hợp lý nhưng phải log lý do loại mẫu.

### `specmind_pr_eval`

Vai trò: harness tốt nhất hiện tại cho RQ2 real-world PR/BFC evaluation.

Entry points:

- `run_specmind_pr_eval.py`
- `specmind_adapter.py`

Điểm mạnh:

- Chạy được SpecMind trên PR Marshmallow.
- Có post/pre evaluation.
- Có mutation backend `function-ast`.
- Có output per-PR và summary.
- Summary PR50 retry3 đã có số liệu dùng được:
  - total functions: 35
  - post correct: 31/35 = 88.57%
  - bug detected: 1/35 = 2.86%
  - total mutants: 171
  - killed: 58
  - survived: 81
  - errors: 32
  - valid micro mutation score: 58 / (58 + 81) = 41.73%
  - avg attempts: 3.2

Vấn đề:

- Chưa có baseline nl2postcond chạy cùng harness.
- Chưa có SpecMind singlepass/retry/multiturn matrix đầy đủ.
- Chưa log sample exclusion reasons.
- Chưa có detectable subset.
- Chưa có failure taxonomy labels.
- `bug_detection_rate` đang tính trên total functions, nhưng paper cần cả:
  - overall BFC detection
  - post-pass-conditioned detection
  - detectable BFC detection
- `specmind_adapter.py` prompt đơn giản hơn bản `specmind_marshmallow_eval/specmind_core`. Cần quyết định dùng prompt nào làm canonical.

### `specmind_marshmallow_eval`

Vai trò: harness tốt nhất hiện tại cho Marshmallow backfill/full metric.

Entry points:

- `run_marshmallow_specmind.py`
- `run_marshmallow_all.ps1`
- `summarize_marshmallow_metrics.py`
- `specmind_core/marshmallow_power.py`
- `specmind_core/metrics.py`

Điểm mạnh:

- Có paper-style metrics tốt nhất:
  - corr
  - comp raw
  - comp valid
  - micro comp raw/valid
  - attempts
  - submissions
  - assertion turns
  - efficiency
  - token usage
- Có AST mutation evaluator riêng cho Marshmallow.
- Có completeness threshold loop.

Vấn đề:

- Đây là harness cho dataset backfill, không phải PR/BFC trực tiếp.
- Một số run chưa hoàn chỉnh:
  - `rerun_tokens_20260603_tau90_mu12_mut40`: 12 tasks, corr 100%, comp valid 6.67%, micro valid 19.05%.
  - `baseonly_mu12_fullmetrics` và `fresh_tokens...` cần xem lại vì summary có thể không cùng schema hoặc incomplete.
- Cần gom kết quả theo run manifest để tránh nhầm run lỗi với run chính.

### `PKA` và `specmind_test`

Vai trò: bản copy/biến thể của SpecMind và dataset extraction scripts.

Điểm mạnh:

- `PKA/SpecMind` giữ nhiều metric hơn bản `specmind_test/SpecMind`.
- `specmind_test` chứa repo Marshmallow và dataset files đang dùng.

Vấn đề:

- Không nên tiếp tục phát triển song song nhiều bản SpecMind.
- Nên chọn một nơi làm canonical code path, còn các bản kia làm reference/archive.

## 3. Vấn đề nghiên cứu cần sửa bằng code trước khi chạy thêm

### Vấn đề 1: Thiếu schema thống nhất

Cần một schema chung cho mọi sample:

```json
{
  "run_id": "...",
  "dataset": "...",
  "project": "...",
  "method": "...",
  "mode": "...",
  "model": "...",
  "sample_id": "...",
  "pr_or_bfc_id": "...",
  "function_name": "...",
  "file_path": "...",
  "postcondition": "...",
  "post_status": "pass/fail/error/timeout",
  "pre_status": "pass/fail/error/timeout/not_run",
  "post_pass": true,
  "pre_fail": false,
  "bug_detected": false,
  "detectable": null,
  "mutants_total": 0,
  "mutants_killed": 0,
  "mutants_survived": 0,
  "mutants_error": 0,
  "attempts": 0,
  "submissions": 0,
  "assertion_turns": 0,
  "prompt_tokens": 0,
  "completion_tokens": 0,
  "total_tokens": 0,
  "latency_sec": 0,
  "exclusion_reason": null,
  "failure_category": null
}
```

### Vấn đề 2: Baseline chưa fair

Hiện SpecMind và nl2postcond không chạy cùng adapter/model path.

Nên làm:

- Tạo interface chung `generate_postcondition(method, mode, model, sample)`.
- Cho nl2postcond chạy qua OpenAI-compatible client giống SpecMind, hoặc tạo adapter chung cho Gemini/OpenAI.
- Cùng sample set, cùng timeout, cùng post/pre evaluator, cùng mutation backend.

### Vấn đề 3: PR filtering chưa được báo cáo

Hiện nhiều PR có 0 functions. Đây không sai, nhưng paper cần bảng:

| Stage | Count |
| --- | ---: |
| crawled PRs | N |
| merged Python PRs | N |
| non-doc/test PRs | N |
| PRs with changed functions | N |
| functions with docstring | N |
| functions with linked tests | N |
| evaluated functions | N |

Và lý do loại:

- no Python source file
- docs/tests only
- no changed function
- docstring too short
- no linked tests
- checkout/setup error
- unsupported function type

### Vấn đề 4: Detectable subset chưa có

Với PR/BFC, bug detection thấp là bình thường, nhưng phải biết có bao nhiêu case thật sự function-level detectable.

Nên thêm bước `detectability_check`:

- Chạy linked tests trên post commit không có postcondition.
- Chạy linked tests trên pre commit không có postcondition.
- Nếu post pass và pre fail, PR/function có behavioral difference do tests quan sát được.
- Nếu post pass và pre pass, đánh dấu `detectable=false/unknown`, không nên tính chung như failure tuyệt đối.

Metric cần báo:

- overall BFC detection = detected / all evaluated functions
- detectable BFC detection = detected / detectable functions
- post-pass-conditioned detection = detected / post_pass functions

### Vấn đề 5: Mutation score cần tách raw/valid/macro/micro

Hiện PR eval có:

- mutation_score = killed / total
- micro_mutation_score = killed / total_mutants
- micro_mutation_score_valid = killed / (killed + survived)

Nên thêm rõ:

- macro_raw = mean(killed / total)
- macro_valid = mean(killed / (killed + survived))
- micro_raw = sum(killed) / sum(total)
- micro_valid = sum(killed) / sum(killed + survived)
- mutant_error_rate = errors / total

### Vấn đề 6: Failure analysis chưa có hooks

Nên thêm script tạo CSV để manual label:

```csv
sample_id,project,method,model,function,post_status,pre_status,mutation_score_valid,postcondition,failure_category,notes
```

Nhóm ưu tiên label:

- post_pass + pre_pass + low mutation
- post_fail
- post_error
- high survived mutants
- detected bug cases

## 4. Nên chọn code path nào làm canonical?

Nên chọn:

```text
specmind_pr_eval/
```

làm nơi phát triển chính cho paper RQ2, nhưng trích các phần metric tốt từ:

```text
specmind_marshmallow_eval/specmind_core/metrics.py
specmind_marshmallow_eval/summarize_marshmallow_metrics.py
bug_detector/src/mutator/function_mutator.py
bug_detector/src/crawler/*
bug_detector/src/pipeline.py
```

Không nên phát triển trực tiếp trong `PKA` hoặc `specmind_test` nữa, vì sẽ tiếp tục phân mảnh.

## 5. Thứ tự nên làm

### Step 1: Chuẩn hóa result schema và run manifest

Tạo trong `specmind_pr_eval`:

- `schemas.py`
- `summarize_results.py`
- `runs/<run_id>/manifest.json`

Mỗi run lưu:

- model
- method
- mode
- sample source
- max turns
- mutation backend
- max mutants
- timeout
- git commit/hash nếu có
- timestamp

### Step 2: Thêm baseline nl2postcond vào `specmind_pr_eval`

CLI nên có:

```powershell
python specmind_pr_eval/run_eval.py `
  --repo marshmallow-code/marshmallow `
  --prs 2799 `
  --method nl2postcond `
  --mode singlepass `
  --model gpt-5.5
```

và:

```powershell
python specmind_pr_eval/run_eval.py `
  --method specmind `
  --mode multiturn
```

### Step 3: Thêm detectability check

Trước khi sinh assertion, chạy:

- tests on post version without assertion
- tests on pre version without assertion

Lưu:

- `pre_tests_status`
- `post_tests_status`
- `detectable_by_tests`

### Step 4: Làm bảng summary paper-style cho PR eval

Output summary cần có:

- evaluated_functions
- post_pass_rate
- pre_fail_rate
- overall_bfc_detection
- detectable_bfc_detection
- executable_rate
- invalid_rate
- macro/micro raw/valid mutation score
- avg attempts/submissions/assertion turns
- total tokens

### Step 5: Rerun Marshmallow nhỏ nhưng fair

Chạy cùng 35 functions hiện tại:

- nl2postcond singlepass
- SpecMind singlepass
- SpecMind retry
- SpecMind multiturn

Trước mắt chỉ một model để sanity check, sau đó mới thêm model khác.

### Step 6: Chỉ sau đó mới mở rộng dataset

Thứ tự:

1. Marshmallow stable rerun
2. Pandas 10 BFC
3. SciPy 10 BFC
4. Keras 10 BFC
5. Scale 30-50/project

## 6. Việc không nên làm ngay

- Không nên chạy ngay 3 model x 4 projects x 50 BFC khi schema chưa thống nhất.
- Không nên dùng `bug_detector` exported mutation như baseline chính nếu assertion chưa sound.
- Không nên trộn kết quả từ các run lỗi/incomplete vào bảng paper.
- Không nên tiếp tục sửa song song cả `PKA`, `specmind_test`, `specmind_marshmallow_eval`, `specmind_pr_eval`.

## 7. Paper story nên dùng

Kết quả hiện tại đã cho thấy câu chuyện rất tốt:

- Postconditions có thể sound trên fixed implementation.
- Nhưng real bug detection thấp hơn nhiều.
- Mutation score cao hơn BFC detection, chứng tỏ mutation completeness không thay thế được real bug detection.
- Multi-turn giúp tạo assertion pass được, nhưng feedback hiện tại chủ yếu feedback soundness, chưa đủ semantic bug-specific feedback.
- Cần detectability subset và failure taxonomy để giải thích.

Đây là một paper empirical tốt hơn là một paper “tool chạy được”.

