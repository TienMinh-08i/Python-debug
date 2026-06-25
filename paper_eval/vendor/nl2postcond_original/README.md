# Vendored nl2postcond Original Files

These files were copied from:

`C:\specmind\bug_detector\src\nl2postcond`

The RQ1 runner uses this vendored copy for the nl2postcond baseline prompt and response preprocessing instead of a hand-written prompt.

## Files and SHA256

```text
727AEF59766E2D046B8BA47B5F273CE55C989D514C8875BA36693AF162B7556A  benchmarks.py
830C6A562981259D1CE488965C2F6CF7EB9CCEABC6C0C98013F0F7B8C1542CDE  generateLLMSamples.yaml
A9D4CDA79FED9BD2CCEC94DEA384972287E51C0810F17F072FD5E418F24064BD  llm_sample_generator.py
3DF902148D48BAC705F55DD73E4FDEA12E6880265D23B60BE896C36D4C9ED899  log.py
8F533CBB9E6994EFB309AD8C27165469F4B46ADCB65B5C13D1FB5A20B1344E54  prompts.py
FFFD00465EE89C392C384FD73AD0BD74683E6CD53C04269C95198B650FA70A6C  response_preprocessing.py
```

## Usage in `paper_eval`

- Prompt construction calls `prepare_prompt()` from the vendored `llm_sample_generator.py`.
- Templates and system prompt come from the vendored `prompts.py`.
- Output parsing uses `extract_code()` and `code_sanitize()` from the vendored `response_preprocessing.py`.
- The default RQ1 nl2postcond setting is `has_reference_code=True` via `--nl2postcond-context full`, matching the paper's baseline description that nl2postcond has access to the reference implementation.
