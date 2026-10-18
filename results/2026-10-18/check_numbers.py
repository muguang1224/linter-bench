#!/usr/bin/env python3
"""Assert the headline numbers used in ARTICLE.md against the raw JSON."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load(name):
    return json.loads((ROOT / "results" / name).read_text(encoding="utf-8"))


def tool(name):
    return {item["name"]: item for item in load(name)["tools"]}


def main():
    controls = tool("linter-bench-controls.json")
    assert (controls["reference"]["tp"], controls["reference"]["fp"],
            controls["reference"]["fn"]) == (14, 0, 0)
    assert (controls["naive"]["tp"], controls["naive"]["fp"],
            controls["naive"]["fn"]) == (4, 3, 3)

    location = tool("linter-bench-real-location.json")
    assert (location["ruff"]["tp"], location["ruff"]["fp"],
            location["ruff"]["fn"]) == (3, 1, 4)
    assert (location["bandit"]["tp"], location["bandit"]["fp"],
            location["bandit"]["fn"]) == (2, 0, 3)

    rule = tool("linter-bench-real-rule.json")
    assert (rule["ruff"]["tp"], rule["ruff"]["fp"],
            rule["ruff"]["fn"]) == (2, 2, 5)
    assert (rule["bandit"]["tp"], rule["bandit"]["fp"],
            rule["bandit"]["fn"]) == (2, 0, 3)

    django = load("revert-audit-django.json")
    assert django["scanned"] == 100
    assert (django["summary"]["total"], django["summary"]["green"],
            django["summary"]["failed"], django["summary"]["unknown"]) == (2, 1, 1, 0)

    cpython = load("revert-audit-cpython.json")
    assert cpython["scanned"] == 600
    assert (cpython["summary"]["total"], cpython["summary"]["green"]) == (1, 1)

    requests = load("revert-audit-requests.json")
    assert requests["scanned"] == 300
    assert (requests["summary"]["total"], requests["summary"]["unknown"]) == (1, 1)

    for repo in ("matrix-gap", "revert-audit", "linter-bench"):
        report = load("matrix-gap-%s.json" % repo)
        assert len(report["claims"]) == 6, repo
        assert report["summary"] == {"high": 0, "medium": 0, "low": 0}, repo

    print("all headline numbers match the raw JSON")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
