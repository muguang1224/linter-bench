#!/usr/bin/env python3
"""A deliberately small reference detector for the linter-bench corpus.

This is a control, not a product. It exists so the harness has a deterministic
subject whose expected findings are visible in its source. It implements a
subset of the canonical rules with straightforward AST checks; it is not a
replacement for ruff, semgrep, bandit, or any project's own linter.

Usage:
    python3 reference_detector.py CASE_DIR
prints a JSON array of {file,line,rule,message}.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

SECRET_NAME_RE = re.compile(r"(PASSWORD|PASSWD|SECRET|TOKEN|API_KEY|APIKEY)", re.I)


def _iter_py(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if any(part.startswith(".") for part in relative.parts):
            continue
        yield path


def _call_name(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _is_assert_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    name = _call_name(node.func)
    if name is None:
        return False
    return name.startswith("assert") or name in ("fail", "raises")


def _has_assert(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Assert) or _is_assert_call(child):
            return True
    return False


def _asserts(node: ast.AST) -> Iterable[ast.Assert]:
    for child in ast.walk(node):
        if isinstance(child, ast.Assert):
            yield child


def _handler_catches(handler: ast.ExceptHandler, name: str) -> bool:
    if handler.type is None:
        return name == "Exception"
    if isinstance(handler.type, ast.Tuple):
        return any(_call_name(element) == name for element in handler.type.elts)
    return _call_name(handler.type) == name


def _all_pass(body: Sequence[ast.stmt]) -> bool:
    return bool(body) and all(isinstance(stmt, ast.Pass) for stmt in body)


def _in_ranges(filename: str, line: int,
               ranges: Sequence[Tuple[str, int, int]]) -> bool:
    return any(scope == filename and start <= line <= end
               for scope, start, end in ranges)


def detect(case_dir: Path) -> List[Dict[str, object]]:
    trees: List[Tuple[str, ast.Module]] = []
    for path in _iter_py(case_dir):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        trees.append((path.relative_to(case_dir).as_posix(), tree))

    findings: List[Dict[str, object]] = []

    def add(filename: str, line: int, rule: str, message: str) -> None:
        findings.append({"file": filename, "line": line, "rule": rule, "message": message})

    loads: Counter = Counter()
    module_defs: List[Tuple[str, str, int, str]] = []  # file, name, line, kind
    test_ranges: List[Tuple[str, int, int]] = []
    subprocess_aliases = {"subprocess"}

    for filename, tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                loads[node.id] += 1
            elif isinstance(node, ast.Attribute):
                loads[node.attr] += 1
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", node.value):
                    loads[node.value] += 1
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    loads[alias.name] += 1
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "subprocess" and alias.asname:
                        subprocess_aliases.add(alias.asname)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                kind = "class" if isinstance(node, ast.ClassDef) else "function"
                if isinstance(node, ast.ClassDef) or node.col_offset == 0:
                    module_defs.append((filename, node.name, node.lineno, kind))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("test_"):
                    test_ranges.append((filename, node.lineno,
                                        getattr(node, "end_lineno", node.lineno)))

    # Test-quality rules.
    for filename, tree in trees:
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                continue
            if not _has_assert(node):
                add(filename, node.lineno, "test.no-assert", "test has no assertion")
            for assertion in _asserts(node):
                test = assertion.test
                if isinstance(test, ast.Constant):
                    add(filename, assertion.lineno, "test.constant-assert",
                        "assertion on a constant")
                if isinstance(test, ast.Compare) and len(test.ops) == 1:
                    left = test.left
                    right = test.comparators[0] if test.comparators else None
                    if isinstance(left, ast.Name) and isinstance(right, ast.Name) \
                            and left.id == right.id:
                        add(filename, assertion.lineno, "test.self-compare",
                            "value compared with itself")
            for dead in _dead_asserts(node.body):
                add(filename, dead.lineno, "test.dead-assert", "assertion after return")
            for child in ast.walk(node):
                if isinstance(child, ast.Try):
                    for handler in child.handlers:
                        if _handler_catches(handler, "Exception") or handler.type is None:
                            add(filename, handler.lineno, "test.overbroad-except",
                                "test catches Exception")
                        if _handler_catches(handler, "AssertionError") and _all_pass(handler.body):
                            for assertion in _asserts(ast.Module(body=child.body, type_ignores=[])):
                                add(filename, assertion.lineno, "test.swallowed-assert",
                                    "AssertionError is swallowed")
            for decorator in node.decorator_list:
                name = _call_name(decorator)
                if name == "skip" and not getattr(decorator, "args", []):
                    add(filename, decorator.lineno, "test.skip-no-reason",
                        "skip without a reason")

    # Error handling (outside test functions).
    for filename, tree in trees:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            for handler in node.handlers:
                if _all_pass(handler.body) and not _has_assert(ast.Module(body=node.body, type_ignores=[])):
                    if not _in_ranges(filename, handler.lineno, test_ranges):
                        add(filename, handler.lineno, "error.except-pass",
                            "exception is swallowed")
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node.func)
            if name not in ("run", "Popen", "check_output", "call"):
                continue
            owner = getattr(node.func, "value", None)
            if not (isinstance(owner, ast.Name) and owner.id in subprocess_aliases):
                continue
            keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg}
            shell = keywords.get("shell")
            shell_truthy = isinstance(shell, ast.Constant) and bool(shell.value)
            check = keywords.get("check")
            check_true = isinstance(check, ast.Constant) and check.value is True
            if shell_truthy:
                add(filename, node.lineno, "security.shell-true", "shell=True")
            elif not check_true:
                add(filename, node.lineno, "error.unchecked-subprocess",
                    "subprocess result is unchecked")

    # Security: hard-coded credentials.
    for filename, tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                value = node.value
                if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                    continue
                if not value.value:
                    continue
                for target in targets:
                    if isinstance(target, ast.Name) and SECRET_NAME_RE.search(target.id):
                        add(filename, node.lineno, "security.hardcoded-secret",
                            "hard-coded credential")

    # Dead code: module-level definitions never loaded by name.
    for filename, name, line, kind in module_defs:
        if filename.startswith("tests/") or "/tests/" in filename:
            continue
        if name.startswith("_") or name.startswith("test_"):
            continue
        if kind == "function" and name == "main":
            continue
        if loads.get(name, 0) == 0:
            rule = "dead.unused-class" if kind == "class" else "dead.unused-function"
            add(filename, line, rule, "%s is never referenced" % name)

    # Deduplicate and sort.
    unique = {}
    for finding in findings:
        key = (finding["file"], finding["line"], finding["rule"])
        unique[key] = finding
    return [unique[key] for key in sorted(unique)]


def _dead_asserts(body: Sequence[ast.stmt]) -> Iterable[ast.Assert]:
    seen_return = False
    for statement in body:
        if seen_return:
            for child in ast.walk(statement):
                if isinstance(child, ast.Assert):
                    yield child
        if isinstance(statement, ast.Return):
            seen_return = True


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if len(args) != 1:
        print("usage: reference_detector.py CASE_DIR", file=sys.stderr)
        return 2
    case_dir = Path(args[0])
    if not case_dir.is_dir():
        print("not a directory: %s" % case_dir, file=sys.stderr)
        return 2
    print(json.dumps(detect(case_dir), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
