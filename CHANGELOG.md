# Changelog

All notable changes to this project are documented here. The format follows
Keep a Changelog, and the project uses semantic versioning.

## [0.1.0] - 2026-10-18

### Added

- `linter-bench` harness: 20 labeled Python cases (13 defect, 7 clean, 14
  labels) across test-quality, dead-code, error-handling, and security.
- Tool-agnostic runner with adapters for generic JSON, SARIF, text, ruff,
  semgrep, and bandit.
- Precision/recall/F1 scoring with maximum bipartite matching, per-tool
  category scope, explicit rule/path ignores, crash/timeout reporting, and
  parallel execution.
- Human, JSON (`schema_version: 1`), and Markdown outputs; `--validate`,
  `--list`, and `--fail-under-recall` / `--fail-under-precision` gates.
- Positive control (`reference_detector.py`) and negative control
  (`naive_detector.py`); both are clearly labeled as controls, not products.
- 102 unittest cases; CI validates the corpus and exercises both controls.

### Fixed during adversarial verification

Three reviewers attacked corpus fairness, scoring, adapters, and the controls.
The following real defects were fixed before publishing:

- Greedy label matching made scores depend on label order; replaced with
  maximum bipartite matching.
- Malformed adapter output (string rows, `runs: ["x"]`, bad line types) threw
  uncaught exceptions and killed the whole run; now a per-case error.
- A literal `{}` in a command part raised `IndexError`; now tolerated.
- Path normalization stripped a case-dir prefix unconditionally, breaking cases
  whose top-level directory had the same name; it now strips only when the
  result is a real file.
- Labels were not path-normalized (`./app/x.py` could never match).
- Empty `categories` and `ok_exit_codes` silently became defaults.
- `--fail-under-*` passed when the metric was undefined (`None`).
- `--list` crashed on non-UTF-8 stdout.
- SARIF emitted one finding per location, ignored `ruleIndex`, and did not
  percent-decode URIs.
- `stream: "both"` concatenated JSON documents and always failed.
- Tools could read `case.json` from their working directory and game the
  benchmark; every case is now staged without the answer key.
- Corpus cases contained unlabeled second defects: `shell=True` without
  `check=True`, an undefined name in a dead assert, constant comparisons,
  a redundant `assertEqual`, `except Exception: pass` doubling as
  `error.except-pass`, and an unused attribute in a "clean" case.
- The reference detector was file-blind when suppressing `except-pass` inside
  tests, disabled itself under any hidden parent directory, missed import
  aliases and string annotations as uses, treated `check=False` as safe,
  missed truthy `shell=1` and aliased `subprocess`, and missed tuple-form
  `except (A, Exception)`.
