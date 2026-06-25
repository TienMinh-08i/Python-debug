# Draft bài nghiên cứu: đánh giá LLM-generated postconditions cho phát hiện bug Python thực tế

## Tên bài đề xuất

**SpecMind for Real-World Postcondition Generation: Mutation-Based and Bug-Fix Evaluation across Python Libraries**

Tên thay thế:

**Evaluating LLM-Generated Postconditions for Real-World Python Bug Detection: A Comparative Study of SpecMind and nl2postcond**

## Abstract nháp

Postconditions do not merely check whether a program runs; they encode behavioral properties that should hold for correct implementations. Recent work uses large language models to generate postconditions from code and natural-language specifications, but most evaluations remain concentrated on benchmark-style tasks and mutation-based completeness. In this work, we conduct a controlled empirical study of LLM-generated postconditions for real-world Python bug detection. We compare nl2postcond and SpecMind under a shared execution harness, shared prompts, shared mutant sets, and multiple LLM backends. Our evaluation combines standard programming benchmarks with real-world bug-fixing commits and pull requests from Python libraries, starting with Marshmallow and extending to Pandas, SciPy, and Keras. Preliminary Marshmallow results show that generated postconditions can achieve high soundness on fixed implementations, but bug detection remains challenging: in a PR50 evaluation, SpecMind reached 88.57% post-correctness over 35 functions, while detecting 1/35 real pre/post bugs and achieving 41.73% valid micro mutation score. A separate Marshmallow backfill over 28 tasks reached 100% correctness but only 20.34% valid micro completeness. These results motivate a deeper analysis of when generated postconditions are too weak, too strict, missing context, or unable to observe non-local behavioral changes. The study contributes a unified real-world evaluation protocol, a metric suite for postcondition soundness and bug detection, and a failure taxonomy for LLM-generated assertions.

## 1. Motivation

Các kết quả hiện tại cho thấy pipeline đã chạy được, nhưng câu hỏi nghiên cứu cần nâng lên một mức chặt hơn:

- SpecMind có cải thiện chất lượng generation postcondition so với nl2postcond không?
- Cải thiện đó có xuất hiện trên benchmark chuẩn và real-world bug-fix datasets không?
- Vì sao assertion đúng trên post-commit nhưng vẫn không bắt được bug ở pre-commit hoặc mutant?

Bối cảnh hiện tại:

- Đã có pipeline `specmind_pr_eval` để chạy trên Marshmallow PR.
- Đã có LLM adapter và mutation backend `function-ast`.
- Đã có kết quả Marshmallow PR50: 35 functions, post-correct rate 88.57%, bug detection 1/35 = 2.86%, valid micro mutation score 41.73%.
- Đã có Marshmallow backfill: 28 tasks, correctness 100%, valid micro completeness 20.34%.
- Đã có các thư mục code liên quan: `PKA`, `specmind_test`, `specmind_marshmallow_eval`, `specmind_pr_eval`, `bug_detector`.

Điểm mới của bài không nên chỉ là “chạy thêm model”, mà là biến hệ thống hiện tại thành một controlled study.

## 2. Contributions

**C1. Controlled comparison.**  
Tái lập và so sánh nl2postcond, SpecMind single-pass, và SpecMind multiturn trên cùng input, cùng execution harness, cùng mutant set, cùng model budget.

**C2. Real-world evaluation.**  
Mở rộng đánh giá từ HumanEval/EvalPlus sang real-world Python libraries thông qua PR/BFC pairs: Marshmallow trước, sau đó Pandas, SciPy, Keras.

**C3. Metric suite.**  
Đề xuất bộ metric cho real-world postcondition evaluation: post pass rate, pre-fail detection rate, BFC detection rate, executable rate, invalid assertion rate, valid mutation score, attempts, token/cost/latency.

**C4. Failure taxonomy.**  
Phân tích thủ công một tập mẫu assertion thất bại để phân loại nguyên nhân: weak oracle, missing semantic property, missing context, non-local bug, equivalent mutant, setup error, numerical tolerance issue, stateful behavior.

## 3. Academic Positioning and Related Work

### 3.1 Base Papers

**Base paper 1: nl2postcond.**  
Paper nền tảng là *Can Large Language Models Transform Natural Language Intent into Formal Method Postconditions?* của Endres et al. Paper này định nghĩa bài toán chuyển natural-language intent, docstring, comment, và code context thành executable postconditions. Vai trò trong bài của mình là baseline trực tiếp: single-pass LLM postcondition generation.

Các thành phần kế thừa:

| Thành phần | Kế thừa trong bài |
| --- | --- |
| Bài toán | Sinh postcondition từ natural language/code |
| Baseline | nl2postcond single-pass |
| Dataset chuẩn | HumanEval/EvalPlus |
| Metric | accept@k, correctness, bug-completeness |
| Bug detection setting | So sánh fixed version và buggy version |
| Ý tưởng chính | Assertion sinh ra có thể dùng như oracle để bắt bug |

**Base paper 2: SpecMind.**  
Paper method chính là *SPECMIND: Cognitively Inspired, Interactive Multi-Turn Framework for Postcondition Inference* của Le et al. SpecMind chỉ ra hạn chế của single-pass prompting: postconditions có thể hợp cú pháp nhưng sai hoặc yếu về ngữ nghĩa. Framework này dùng feedback-driven multi-turn refinement, trong đó LLM sinh assertion, nhận feedback correctness/completeness, refine, rồi quyết định khi nào submit final solution.

Các thành phần kế thừa:

| Thành phần | Kế thừa trong bài |
| --- | --- |
| Method | Greedy/retry multi-turn, exploratory/multiturn |
| Feedback | correctness + completeness/mutant feedback |
| Metric | correctness, completeness, attempts, submissions |
| Dataset chuẩn | EvalPlus, FixEval |
| Ý tưởng mở rộng | LLM như interactive reasoner thay vì one-shot generator |
| Khoảng trống | Chưa đánh giá sâu trên real-world Python libraries như Marshmallow/Pandas/SciPy/Keras |

### 3.2 Related Work Groups

**A. LLM-based postcondition generation.**  
Nhóm này là gần nhất với bài. nl2postcond là baseline trực tiếp; SpecMind là method trực tiếp. Các hướng như TiCoder, Speculyzer, và LLM-based property generation liên quan vì cùng formalize user intent hoặc sinh properties/oracles từ context.

Điểm khác biệt của bài:

> Unlike prior single-pass nl2postcond approaches, this work evaluates whether feedback-driven multi-turn postcondition generation remains effective across newer LLMs and real-world Python bug-fixing datasets.

**B. Classical specification and invariant inference.**  
Daikon, EvoSpex, static specification inference, predicate mining, contract inference, và abstract interpretation đặt bài vào dòng nghiên cứu specification mining lâu đời. Các hướng này thường suy luận specification từ executions, implementation, hoặc static properties; bài này khác ở chỗ dùng LLM để suy luận desired behavior từ natural language, code context, và feedback execution.

**C. Test oracle generation.**  
TOGA, AthenaTest, và các replication/evaluation về neural test oracle generation rất gần vì assertion/postcondition đều đóng vai trò oracle. Khác biệt chính:

| Test oracle generation | Postcondition generation |
| --- | --- |
| Assertion gắn với một test prefix cụ thể | Assertion mô tả quan hệ input-output tổng quát |
| Dùng chủ yếu trong unit testing | Có thể dùng như runtime oracle/specification |
| Mạnh ở expected exception hoặc concrete test behavior | Mạnh hơn ở semantic behavior nếu postcondition đủ tổng quát |

**D. Patch oracle and PR intent validation.**  
PatchGuru và Testora là nhóm quan trọng cho RQ2 vì cùng đánh giá trên PR/BFC và patch intent. PatchGuru sinh executable patch specifications từ PR artifacts, còn bài này tập trung vào function-level postcondition generation và so sánh nl2postcond với SpecMind.

| PatchGuru/Testora-style patch oracle | Bài của mình |
| --- | --- |
| Sinh patch oracle từ PR description/artifacts | Sinh function-level postcondition từ code/docstring/test context |
| Tập trung validate patch intent/regression | Tập trung so sánh postcondition generation methods |
| Metric theo warnings, precision, true positives | Metric theo post pass, BFC detection, mutation score |
| Cross-version comparison program | Function-level pre/post execution + mutants |

**E. Benchmarks and evaluation.**  
HumanEval/EvalPlus phục vụ RQ1; FixEval và Defects4J là nền đánh giá bug/fix trong các paper gốc; Marshmallow/Pandas/SciPy/Keras là phần mở rộng real-world Python của bài này.

### 3.3 Research Gap

Khoảng trống có thể định vị như sau:

| Existing work | Hạn chế |
| --- | --- |
| nl2postcond | Chủ yếu single-pass, dễ sinh postcondition đúng cú pháp nhưng yếu hoặc sai |
| SpecMind | Cải thiện bằng multi-turn, nhưng chủ yếu đánh giá trên EvalPlus/FixEval |
| PatchGuru/Testora | Real PR Python nhưng tập trung patch oracle, không so sánh postcondition generation methods |
| TOGA/AthenaTest | Sinh test oracle cho test prefix, không sinh function-level abstract postcondition |
| Daikon/EvoSpex | Không tận dụng LLM reasoning và natural-language intent |

Định vị bài:

> This work systematically evaluates whether feedback-driven LLM postcondition generation generalizes beyond benchmark-style tasks to real-world Python bug-fixing changes, and compares modern LLMs under both mutation-based and BFC-based metrics.

### 3.4 Candidate References

Các DOI/arXiv cần kiểm tra lại trước khi nộp paper, nhưng có thể dùng làm danh sách BibTeX ban đầu:

| Nhóm | Reference | DOI/arXiv |
| --- | --- | --- |
| Core base | Endres et al., *Can Large Language Models Transform Natural Language Intent into Formal Method Postconditions?* | 10.1145/3660791 |
| Core base | Le et al., *SpecMind: Cognitively Inspired, Interactive Multi-Turn Framework for Postcondition Inference* | 10.48550/arXiv.2602.20610 |
| Specification inference | Daikon, *Dynamically Discovering Likely Program Invariants* | 10.1109/32.908957 |
| Specification inference | EvoSpex, *An Evolutionary Algorithm for Learning Postconditions* | 10.1109/ICSE43902.2021.00112 |
| Static specification | *Static Specification Inference Using Predicate Mining* | 10.1145/1250734.1250749 |
| Contract inference | *Inferring Better Contracts* | 10.1145/1985793.1985820 |
| Test oracle | TOGA, *A Neural Method for Test Oracle Generation* | 10.1145/3510003.3510141 |
| Test generation | AthenaTest, *Unit Test Case Generation with Transformers and Focal Context* | 10.48550/arXiv.2009.05617 |
| Oracle evaluation | *Neural-Based Test Oracle Generation: A Large-Scale Evaluation and Lessons Learned* | 10.1145/3611643.3616265 |
| Patch oracle | PatchGuru, *Patch Oracle Inference from Natural Language Artifacts with Large Language Models* | 10.48550/arXiv.2602.05270 |
| Patch validation | Testora / natural-language oracle regression testing | 10.48550/arXiv.2503.18597 |
| Benchmark | HumanEval / Codex paper | 10.48550/arXiv.2107.03374 |
| Benchmark | EvalPlus | 10.48550/arXiv.2305.01210 |
| Benchmark | FixEval | 10.1109/APR59189.2023.00009 |
| Benchmark | Defects4J | 10.1145/2610384.2628055 |

## 4. Research Questions

**RQ1. How do SpecMind and nl2postcond compare on standard programming benchmark tasks under original postcondition-generation metrics and mutation-based completeness?**

Mục tiêu: kiểm tra tính tái lập và so sánh method trên benchmark chuẩn như HumanEval/HumanEval+.

Metrics:

- pass@1 / accept@1
- correctness
- executable rate
- syntax/runtime error rate
- mutation score
- bug-complete rate
- attempts
- token/cost/latency

**RQ2. How well do LLM-generated postconditions generalize to real-world Python libraries and bug-fixing commits?**

Mục tiêu: đánh giá trên code thực tế, nơi bug có thể phụ thuộc context, state, exception behavior hoặc interaction giữa nhiều function.

Datasets:

- Phase 1: Marshmallow
- Phase 2: Pandas
- Phase 3: SciPy
- Phase 4: Keras

Metrics:

- post pass rate
- pre-fail rate
- BFC detection rate
- detectable BFC detection rate
- valid mutation score
- mutant error rate
- cost per detected bug

**RQ3. Why do generated postconditions fail to detect real bugs or survive mutants?**

Mục tiêu: giải thích sâu thay vì chỉ báo cáo số thấp.

Sampling:

- post_pass nhưng pre_pass
- post_pass nhưng mutation score = 0
- post_error
- post_fail
- high survived mutants
- detected bug cases

## 5. Experimental Design

### 4.1 Methods

| Method | Mô tả | Vai trò |
| --- | --- | --- |
| nl2postcond single-pass | Sinh assertion một lượt | Baseline chính |
| SpecMind single-pass | SpecMind không feedback loop | Ablation |
| SpecMind multiturn | Sinh, chạy, nhận feedback, refine | Method chính |
| bug_detector exported assertion | Assertion/mutation pipeline hiện có | Reference baseline, không dùng làm sound baseline nếu assertion lẫn sai |

### 4.2 Models

Model IDs cần kiểm tra lại trước khi chạy final experiment. Trong paper nên mô tả theo vai trò thay vì phụ thuộc tên marketing:

- Strong coding/reasoning proprietary model
- Fast/low-cost proprietary model
- Open-weight or locally deployable model

Nếu vẫn giữ naming hiện tại, bảng chạy có thể là:

| Model slot | Candidate |
| --- | --- |
| Strong coding model | GPT-5.5 hoặc model OpenAI tương ứng |
| Fast reasoning model | Gemini 3 Flash hoặc model Gemini tương ứng |
| Open-weight model | MiMo 2.5 nếu deploy/API ổn định |

### 4.3 Fair Comparison Controls

| Thành phần | Cố định |
| --- | --- |
| sample set | giống nhau |
| prompt template | giống nhau theo method |
| max turns | giống nhau |
| temperature | 0 hoặc 0.2 |
| top_p | 1.0 |
| mutant seed | cố định |
| max mutants/function | 10 hoặc 20 |
| timeout | giống nhau |
| retry policy | giống nhau |
| context budget | giống nhau |
| logging schema | giống nhau |

## 6. Unified Logging Schema

Mỗi run nên lưu theo schema thống nhất:

```json
{
  "run_id": "...",
  "dataset": "HumanEvalPlus / Marshmallow / Pandas / Scipy / Keras",
  "method": "nl2postcond / specmind_single / specmind_multiturn",
  "model": "...",
  "sample_id": "...",
  "project": "...",
  "pr_or_bfc_id": "...",
  "pre_commit": "...",
  "post_commit": "...",
  "file_path": "...",
  "function_name": "...",
  "function_signature": "...",
  "postcondition": "...",
  "post_status": "pass/fail/error",
  "pre_status": "pass/fail/not_run/error",
  "bug_detected": true,
  "mutants_total": 0,
  "mutants_killed": 0,
  "mutants_survived": 0,
  "mutants_error": 0,
  "attempts": 0,
  "assertion_turns": 0,
  "input_tokens": 0,
  "output_tokens": 0,
  "latency_sec": 0,
  "error_type": null
}
```

## 7. Tables for Paper

### Table 1: RQ1 benchmark comparison

| Method | Model | Correctness | Executable | Mutation score valid | pass@1 | Bug-complete | Attempts | Cost |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| nl2postcond | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| SpecMind single-pass | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| SpecMind multiturn | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |

### Table 2: RQ2 real-world project results

| Project | Method | Model | Functions | Post pass | Overall BFC detect | Detectable BFC detect | Valid mutation | Cost |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Marshmallow | SpecMind multiturn | TBD | 35 | 88.57% | 2.86% | TBD | 41.73% | TBD |
| Marshmallow backfill | SpecMind multiturn | TBD | 28 | 100% | N/A | N/A | 20.34% | TBD |
| Pandas | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| SciPy | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| Keras | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |

### Table 3: Failure taxonomy

| Category | Count | Percent | Example project | Implication |
| --- | ---: | ---: | --- | --- |
| Weak oracle | TBD | TBD | Marshmallow | Need stronger semantic prompting |
| Missing semantic property | TBD | TBD | TBD | Need better changed-line/test context |
| Context missing | TBD | TBD | Pandas | Need retrieval/context expansion |
| Non-local bug | TBD | TBD | TBD | Function-level postconditions are insufficient |
| Equivalent mutant | TBD | TBD | SciPy | Need mutant filtering |
| API/setup issue | TBD | TBD | Keras | Need better harness |
| Numerical tolerance issue | TBD | TBD | SciPy | Need tolerance-aware assertions |
| Exception behavior missed | TBD | TBD | Marshmallow | Need exception/warning-specific prompting |

## 8. Failure Analysis Taxonomy

| Error category | Mô tả |
| --- | --- |
| Weak oracle | Assertion chỉ kiểm tra type, not None, length chung chung |
| Missing semantic property | Không kiểm tra invariant liên quan bug |
| Context missing | Cần object state/config/global context nhưng prompt không có |
| Input under-specification | Test input không kích hoạt bug |
| Equivalent mutant | Mutant không đổi hành vi quan sát được |
| Incompetent mutant | Mutant gây crash/setup error, không đánh giá được |
| Overfitting to docstring | Assertion bám docstring nhưng không bám behavior |
| API/dependency issue | Không import/setup được project |
| Non-local bug | Bug nằm ở interaction nhiều function/class |
| Numerical tolerance issue | Sai do floating point/tolerance |
| Stateful behavior | Bug phụ thuộc trạng thái object/cache/config |
| Exception behavior missed | Assertion không kiểm tra exception/warning |

## 9. Kế hoạch thực nghiệm

### Phase 0: Chuẩn hóa pipeline

- Hợp nhất logging schema giữa `specmind_pr_eval`, `specmind_marshmallow_eval`, và baseline nl2postcond.
- Lưu đầy đủ attempts, raw responses, token/cost, latency, post/pre status, mutant status.
- Chuẩn hóa metric calculation để summary có thể gom nhiều dataset.

### Phase 1: Rerun Marshmallow

Mục tiêu: xác nhận kết quả cũ có tái lập được không.

| Run | Method | Model |
| --- | --- | --- |
| M1 | SpecMind multiturn | strong coding model |
| M2 | nl2postcond single-pass | strong coding model |
| M3 | SpecMind single-pass | strong coding model |

Sanity check từ kết quả hiện tại:

- post-correct rate khoảng 88.57%
- bug detection khoảng 2.86%
- valid micro mutation score khoảng 41.73%

### Phase 2: HumanEval/HumanEval+

Mục tiêu: trả lời RQ1.

Chạy:

- nl2postcond single-pass
- SpecMind single-pass
- SpecMind multiturn

Trên cùng model set, cùng mutant set.

### Phase 3: Real-world BFC expansion

Mục tiêu: trả lời RQ2.

Mở rộng theo tầng:

| Project | Initial target | Final target |
| --- | ---: | ---: |
| Marshmallow | 50 PR/BFC đã có | 50-100 |
| Pandas | 10 | 30-50 |
| SciPy | 10 | 30-50 |
| Keras | 10 | 30-50 |

### Phase 4: Manual error analysis

Mục tiêu: trả lời RQ3.

- Nếu tổng samples >= 1000: lấy 10%.
- Nếu tổng samples nhỏ hơn 300: lấy tối thiểu 30-50 samples.
- Sample theo nhóm thất bại, không sample ngẫu nhiên toàn bộ.

## 10. Narrative chính của bài

Thông điệp trung tâm nên là:

> LLM-generated postconditions có thể đạt soundness cao trên fixed implementations, nhưng real-world bug detection khó hơn nhiều so với benchmark và mutation-only evaluation. SpecMind-style feedback/refinement có tiềm năng cải thiện correctness và completeness, nhưng hiệu quả phụ thuộc mạnh vào context, mutant quality, observability của bug, và khả năng biến thay đổi PR thành function-level behavioral property.

Điểm mạnh của paper là không che giấu detection thấp. Ngược lại, bài nên dùng detection thấp như phát hiện nghiên cứu:

- Nhiều PR/BFC không tạo ra bug quan sát được ở function-level.
- Nhiều assertion đúng nhưng quá yếu.
- Mutation score không đồng nghĩa với real bug detection.
- Cần phân biệt overall BFC detection và detectable BFC detection.

## 11. Threats to Validity

- **Model drift:** API model có thể thay đổi theo thời gian.
- **Dataset bias:** Marshmallow/Pandas/SciPy/Keras không đại diện toàn bộ Python ecosystem.
- **Function extraction bias:** Changed function chưa chắc là nơi duy nhất chứa bug.
- **Mutant quality:** Equivalent/incompetent mutants có thể làm sai lệch mutation score.
- **Harness reliability:** Real-world dependencies dễ gây setup/runtime error.
- **Prompt sensitivity:** Prompt nhỏ thay đổi có thể ảnh hưởng kết quả.
- **Cost constraints:** Số model và số BFC có thể bị giới hạn bởi chi phí.

## 12. Draft conclusion

This study reframes LLM-generated postcondition evaluation from benchmark-only correctness toward real-world bug detection. Preliminary results on Marshmallow show that postconditions generated by SpecMind can often remain sound on fixed implementations, but they detect only a small fraction of real pre/post behavioral differences and leave many mutants alive. These findings suggest that future postcondition-generation systems need stronger semantic prompting, richer local and non-local context, better mutant filtering, and evaluation metrics that separate assertion validity from actual bug detectability.
