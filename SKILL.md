---
name: linter-bench
description: Run a labeled corpus against static checkers and score precision, recall, F1, crashes, and time. Use when a user asks whether a linter is good, wants a reproducible comparison, or needs to add labeled cases without gaming the benchmark.
---

# linter-bench

A small, inspectable benchmark for static checkers. The corpus is 20 tiny Python cases (13 defect, 7 clean) with line-level labels; the harness runs configured tools, normalizes their output through adapters, and reports TP/FP/FN plus precision, recall, F1, crashes, and duration.

## When to use

- "Is this linter any good?" / "Which one should we adopt?"
- A user wants evidence for a review, blog post, or README claim.
- Adding a regression case for a bug a linter missed or a false positive it produced.

## Run

```bash
python3 linter_bench.py --validate
python3 linter_bench.py --tools tools.controls.json
python3 linter_bench.py --tools my-tools.json --only my-tool --json
```

A tool config names a command, an adapter (`json`, `sarif`, `text`, `ruff`, `semgrep`, `bandit`), the categories it targets, and optionally rule/path ignores. Tools run against a staged copy of each case; `case.json` is not present.

## Reading the result

- `precision` answers "when it reports, how often is it right?"
- `recall` answers "of the labeled defects in scope, how many did it find?"
- `F1` is a summary; do not optimize it without saying what you traded.
- `crashes` is separate. A crash is not a low score; it is a failed run.
- A small `cases` count means the comparison is weak. Say the sample size.

## Guardrails

- **Do not tune the corpus to a tool.** If a label moves because a tool reports elsewhere, you are fitting the benchmark.
- **Do not hide false positives.** `ignore_rules` and `ignore_paths` are explicit scope; they must be printed and justified.
- **Do not treat the reference detector as a baseline.** It is a positive control; `naive` is a negative control.
- **Do not generalize from 20 cases.** Report scope, language, tool version, and tolerance.
- **Do not run an untrusted `tools.json`.** The harness executes the command it is given.
- **Do not change matching rules between tools.** Same tolerance, same matching mode, same corpus.

## Output contract

`--json` emits `schema_version: 1` with `cases`, `tools[]` (aggregate metrics and per-case results), `line_tolerance`, and `warnings`. Per-case results include TP/FP/FN, error text, duration, and normalized findings.

Exit codes: `0` report, `1` threshold missed or invalid corpus, `2` configuration/tool error.
