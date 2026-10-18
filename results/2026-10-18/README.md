# Consolidation: evidence pack

This folder contains the runs behind [`ARTICLE.md`](ARTICLE.md). It is not a
separate product; it is the audit trail for the three repositories.

## Environment

- Date: 2026-10-18
- Python: 3.10.12
- ruff: 0.17.1
- bandit: 1.9.4
- OS: Linux (sandbox)
- `revert-audit` used the public GitHub API without a token (60 requests/hour),
  so the samples are small and explicitly bounded by `--limit`.

## Contents

| Path | What it is |
|------|------------|
| `ARTICLE.md` | Long-form write-up with the numbers and limitations |
| `results/linter-bench-controls.{json,md}` | Reference and naive controls on the 20-case corpus |
| `results/linter-bench-real-location.{json,md}` | ruff/bandit, location matching |
| `results/linter-bench-real-rule.{json,md}` | ruff/bandit, rule matching |
| `results/revert-audit-django.json` | 100 commits, 2 reverts |
| `results/revert-audit-cpython.json` | 600 commits, 1 revert |
| `results/revert-audit-requests.json` | 300 commits, 1 revert, unknown CI |
| `results/matrix-gap-*.json` | self-audit of the three repositories |
| `configs/tools.real.location.json` | real-tool config, location matching |
| `configs/tools.real.rule.json` | real-tool config, rule matching |

## Reproduce

```bash
pip install ruff bandit

# from the linter-bench repository root
python3 linter_bench.py --tools tools.controls.json
python3 linter_bench.py --tools results/2026-10-18/configs/tools.real.location.json
python3 linter_bench.py --tools results/2026-10-18/configs/tools.real.rule.json

cd ../revert-audit
python3 revert_audit.py django/django --limit 100 --max-reverts 2
python3 revert_audit.py python/cpython --limit 600 --max-reverts 3
python3 revert_audit.py psf/requests --limit 300 --max-reverts 3

cd ../matrix-gap
python3 matrix_gap.py ../matrix-gap
python3 matrix_gap.py ../revert-audit
python3 matrix_gap.py ../linter-bench
```

## Reading rules

- Every claim in the article is traceable to one of these JSON files.
- `n` is always the number of resolved reverts or the number of in-scope labels.
- A green rate of 1/1 is not 100% of a population; it is one observation.
- Real-tool scores are configuration-dependent. The two configs differ only in
  matching mode and rule mappings; that is exactly the comparison being made.
- The control tools (`reference`, `naive`) are not products and are not ranked
  against ruff or bandit.
