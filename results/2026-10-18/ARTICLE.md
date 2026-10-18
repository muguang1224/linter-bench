# I built three tools to measure CI's blind spots. Here is what they found.

**CI is a filter, not a proof.** That sentence is easy to say and surprisingly hard to quantify. Over the last few days I built three small, zero-dependency tools that try to measure three different gaps between what a repository claims and what its automation actually verifies:

| Tool | Question | Tests |
|---|---|---:|
| [`matrix-gap`](https://github.com/muguang1224/matrix-gap) | Does CI test every platform/version the repo claims to support? | 134 |
| [`revert-audit`](https://github.com/muguang1224/revert-audit) | Of the changes that were later reverted, how many had green CI? | 90 |
| [`linter-bench`](https://github.com/muguang1224/linter-bench) | When a linter reports something, how often is it right — and what does it miss? | 102 |

This is the consolidation pass: real runs, real numbers, and the limitations that make the numbers smaller than they look.

## TL;DR

- All three of my repositories pass `matrix-gap`'s own audit: six machine-readable support claims each, zero gaps.
- On a 100-commit Django sample, two changes were reverted. One had passed every observed check; one had not. A 50% green rate on n=2 is not a rate — it is a reason to collect more.
- On 600 recent CPython commits, one revert was found; its original change had green CI and touched tests. On 300 `psf/requests` commits, one 2023 revert had no surviving checks, so the honest verdict was `unknown`, not green.
- On `linter-bench`'s 20-case corpus, ruff scored 75.0% precision / 42.9% recall under location matching, and 50.0% / 28.6% under rule matching. Bandit scored 100% / 40.0% both ways. The difference is the point: **where** you match changes the story.
- The controls did what controls should: the reference detector scored 100% by construction; the naive “flag every line containing `assert`” detector scored 57.1% F1.

## 1. Does CI test what the repo claims?

`matrix-gap` reads `requires-python`, classifiers, `engines.node`, `rust-version`, the `go` directive, tox envlists, and the GitHub Actions matrix. It expands `include`/`exclude`, refuses to guess dynamic matrices, and reports the difference.

I ran it on all three repositories:

```text
matrix-gap:      6 claims, 0 high / 0 medium / 0 low
revert-audit:    6 claims, 0 high / 0 medium / 0 low
linter-bench:    6 claims, 0 high / 0 medium / 0 low
```

That is a clean dogfood result, not a headline. The interesting part is what the tool refuses to do: when a matrix is `${{ fromJSON(...) }}` or a version is `3.x`, it emits `MG005` (“not expanded”) instead of inventing coverage. It also catches a YAML trap that has bitten real projects: an unquoted `python-version: 3.10` is parsed as the float `3.1`, so CI silently tests the wrong version. The checker treats that as an error in the claim, not as a pass.

## 2. How much does CI miss on changes that get reverted?

`revert-audit` walks a GitHub repository's history, resolves each revert back to the original commit/PR, and reads the checks that ran on that change. It separates `green`, `failed`, and `unknown` (missing, pending, or expired checks), and excludes re-lands from the denominator.

Three real runs, with their sample sizes stated up front:

| Repository | Commits scanned | Reverts found | Green | Failed | Unknown | Green rate (n) |
|---|---:|---:|---:|---:|---:|---:|
| `django/django` | 100 | 2 | 1 | 1 | 0 | 50.0% (n=2) |
| `python/cpython` | 600 | 1 | 1 | 0 | 0 | 100.0% (n=1) |
| `psf/requests` | 300 | 1 | 0 | 0 | 1 | — (n=0) |

Details that matter:

- The Django pair was reverted roughly **269 days** after the original change. Both had touched tests. One of the two had a failing check at the time; the other had a fully green set, including coverage, PostgreSQL, and benchmark jobs. This is the shape of a CI blind spot: a change that passed everything and still had to be undone months later.
- The CPython revert was **12.9 days** after the original change, touched 8 files and 99 lines, and had a green check set. Again: one observation, not a rate.
- The requests revert was from 2023. GitHub no longer had the checks, so the tool said `unknown`. That is the correct answer. Counting it as green would have inflated the metric; counting it as zero would have deflated it.

The metric is deliberately narrow: **“was every observed check green at the time?”** It is not “was the change buggy?” Reverts are product decisions too. The report says “reverted”, never “defective”.

## 3. Do linters actually work?

`linter-bench` is a labeled corpus: 20 tiny Python cases, 13 with defects, 7 clean, 14 line-level labels across test-quality, dead-code, error-handling, and security. A tool gets credit only when a finding lands on a labeled file and line (within one line); everything else is a false positive.

### Controls first

Before comparing real tools, the benchmark has to prove it can discriminate:

| Control | Cases in scope | TP | FP | FN | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| `reference` (implements the labels) | 20 | 14 | 0 | 0 | 100.0% | 100.0% | 100.0% |
| `naive` (flags every line containing `assert`) | 8 | 4 | 3 | 3 | 57.1% | 57.1% | 57.1% |

The first is a positive control: it proves the harness, adapters, and labels agree. The second is a negative control: it proves the benchmark can detect bad precision. Neither is a product, and neither belongs on a leaderboard.

### Real tools, two matching modes

I installed ruff 0.17.1 and bandit 1.9.4, ran them on Python 3.10.12, and configured only the categories each tool plausibly targets. Same corpus, same tolerance, two matching rules:

**Location matching** — a finding counts if the file and line match, regardless of rule code.

| Tool | Cases | TP | FP | FN | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| ruff | 12 | 3 | 1 | 4 | 75.0% | 42.9% | 54.5% |
| bandit | 8 | 2 | 0 | 3 | 100.0% | 40.0% | 57.1% |

**Rule matching** — the tool's rule code must map to the label's canonical rule.

| Tool | Cases | TP | FP | FN | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| ruff | 12 | 2 | 2 | 5 | 50.0% | 28.6% | 36.4% |
| bandit | 8 | 2 | 0 | 3 | 100.0% | 40.0% | 57.1% |

Why did ruff lose a true positive and gain a false positive when matching got stricter? Because of `S603` (“subprocess call — check for execution of untrusted input”). Under location matching, `S603` on the *unchecked* subprocess case was credited as the labeled `error.unchecked-subprocess` defect — even though `S603` is a different claim. It also fired on the *checked* case, where it was counted as a false positive. Under rule matching, `S603` maps to nothing, so both the credit and the false positive disappear. The stricter score is lower, and more honest.

Bandit’s missing recall is also concrete: in this configuration/version it did not emit `B110` for `except OSError: pass`, so the `error.except-pass` label stayed a false negative. It did catch the hard-coded password and `shell=True`. Neither tool is “bad”; they are configured for different jobs than this corpus.

## 4. Five things I learned

**1. “Green” needs a timestamp and a scope.** Checks expire. A 2023 commit with no surviving checks is not green and not failed. Making `unknown` a first-class state was the single most important design choice in `revert-audit`.

**2. Matching rules change the ranking.** Location matching is generous and easy; rule matching is stricter and surfaces the fact that two tools can flag the same line for different reasons. Report both if you report either.

**3. Controls are not optional.** A benchmark with only real tools cannot tell you whether a low score means “bad tool” or “broken harness”. The reference control catches harness bugs; the naive control catches a benchmark that cannot discriminate.

**4. Measuring tools need adversarial tests too.** Reviewers found real bugs in all three: greedy matching that made scores depend on label order, a tool that could read `case.json` from its working directory and copy the answers, a cache entry that could fabricate an empty report, a SARIF adapter that counted one result twice. A measurement whose own correctness is untested is just a confident opinion.

**5. Small samples are still useful if you say n.** One green revert in CPython is not “100% of CPython reverts”. It is one observation with a timestamp, files, and checks — enough to debug the tool, not enough to rank projects.

## 5. What this does not prove

- `linter-bench` is 20 Python cases. It is a smoke test, not a survey. A two-point F1 difference is noise.
- `revert-audit` only sees GitHub, only observed checks (not branch-protection requirements), and only reverts it can resolve. Squash-merged revert PRs whose body was discarded can be `unidentified`.
- `matrix-gap` is static. A dynamic matrix is reported as unknown, not as a gap.
- Labels are opinions. They are written down, line-addressed, and reviewable — that is the most that can be claimed.
- Tool versions matter. ruff 0.17.1 and bandit 1.9.4 are not every version.

## 6. Reproduce it

```bash
# linter-bench
pip install ruff bandit
cd linter-bench
python3 linter_bench.py --tools tools.controls.json
python3 linter_bench.py --tools results/2026-10-18/configs/tools.real.location.json
python3 linter_bench.py --tools results/2026-10-18/configs/tools.real.rule.json

# revert-audit (public API; a token raises the rate limit)
cd ../revert-audit
python3 revert_audit.py django/django --limit 100 --max-reverts 2
python3 revert_audit.py python/cpython --limit 600 --max-reverts 3

# matrix-gap
cd ../matrix-gap
python3 matrix_gap.py ../matrix-gap
python3 matrix_gap.py ../revert-audit
python3 matrix_gap.py ../linter-bench
```

Raw JSON for every number in this article is in [`results/`](results/). The configs used for the real-tool runs are in [`configs/`](configs/). Every repository is MIT-licensed and has no runtime dependencies.

## 7. The three repositories

- [`matrix-gap`](https://github.com/muguang1224/matrix-gap) — claimed support vs tested CI matrix.
- [`revert-audit`](https://github.com/muguang1224/revert-audit) — CI false-negative rate on reverted changes.
- [`linter-bench`](https://github.com/muguang1224/linter-bench) — labeled precision/recall benchmark for static checkers.

The through-line is not “CI is broken”. It is that **a passing check is a claim about a specific set of tests, on a specific commit, at a specific time**. Once you write that claim down, you can measure it. Most of the time, the honest answer is smaller and more useful than the slogan.
