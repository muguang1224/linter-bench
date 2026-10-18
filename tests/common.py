"""Shared helpers for the linter-bench test suite."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import linter_bench as lb  # noqa: E402


def tmpdir(testcase):
    path = Path(tempfile.mkdtemp(prefix="linter-bench-test-"))
    testcase.addCleanup(shutil.rmtree, str(path), True)
    return path


def write_case(root: Path, case_id: str, category: str = "test-quality",
               labels=None, defect=None, files=None) -> Path:
    path = root / case_id
    path.mkdir(parents=True)
    for name, content in (files or {"app/x.py": "def x():\n    return 1\n"}).items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    payload = {
        "id": case_id,
        "category": category,
        "defect": (bool(labels) if defect is None else defect),
        "description": "test case",
        "labels": labels or [],
    }
    (path / "case.json").write_text(json.dumps(payload), encoding="utf-8")
    return path


def write_tools(path: Path, tools) -> Path:
    path.write_text(json.dumps({"tools": tools}), encoding="utf-8")
    return path


def run_main(*args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = lb.main([str(a) for a in args])
    return code, buf.getvalue()


def completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(args=["tool"], returncode=returncode,
                                       stdout=stdout, stderr=stderr)
