#!/usr/bin/env python3
"""A deliberately naive negative control for linter-bench.

It reports every line containing the word "assert", with one generic rule name.
It is not a linter; it is here so the benchmark has a subject with obviously
poor precision. Its output shape is the generic JSON finding list.

Usage: python3 naive_detector.py CASE_DIR
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv):
    if len(argv) != 1:
        print("usage: naive_detector.py CASE_DIR", file=sys.stderr)
        return 2
    root = Path(argv[0])
    if not root.is_dir():
        print("not a directory: %s" % root, file=sys.stderr)
        return 2
    findings = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if "assert" in line:
                findings.append({
                    "file": relative,
                    "line": number,
                    "rule": "generic.assert",
                    "message": "line mentions assert",
                })
    print(json.dumps(findings, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
