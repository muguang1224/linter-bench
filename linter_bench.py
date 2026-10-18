#!/usr/bin/env python3
"""linter-bench -- a small, reproducible benchmark for static checkers.

The benchmark is the product: a labeled corpus of tiny Python projects, a
tool-agnostic runner, and one scoring rule. A finding counts only when it lands
on the labeled file and line (optionally: the labeled rule). Everything else is
a false positive; every unmatched label is a false negative.

Zero dependencies. No network. The corpus never changes at runtime.

Exit codes:
  0  report produced
  1  --fail-under-recall/--fail-under-precision threshold missed, or invalid corpus
  2  tool/configuration error

This is a harness, not a linter. It does not decide what "good" code is; the
labels do, and the labels are in the repository for anyone to inspect.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

__version__ = "0.1.0"
SCHEMA_VERSION = 1

CATEGORIES = ("test-quality", "dead-code", "error-handling", "security")
ADAPTERS = ("json", "sarif", "text", "ruff", "semgrep", "bandit")
TEXT_FINDING_RE = re.compile(r"^(.+?):(\d+)(?::(\d+))?:\s*(.*)$")
TEXT_RULE_RE = re.compile(
    r"^(?:\[([A-Za-z][A-Za-z0-9_.-]*)\]|([A-Z][A-Z0-9_.-]*\d[A-Za-z0-9_.-]*)|"
    r"([A-Za-z][A-Za-z0-9_.-]*\.[A-Za-z0-9_.-]+))\s*:?\s*(.*)$")


class ToolError(Exception):
    pass


class AdapterError(ToolError):
    pass


@dataclass
class Label:
    file: str
    line: int
    rule: str
    message: str = ""
    aliases: List[str] = field(default_factory=list)


@dataclass
class Case:
    id: str
    path: Path
    category: str
    defect: bool
    description: str
    labels: List[Label]


@dataclass
class Finding:
    file: str
    line: int
    rule: str = ""
    message: str = ""
    severity: str = ""


@dataclass
class ToolSpec:
    name: str
    command: List[str]
    adapter: str = "json"
    categories: List[str] = field(default_factory=lambda: ["*"])
    match: str = "location"
    rules: Dict[str, str] = field(default_factory=dict)
    timeout: float = 30.0
    stream: str = "stdout"
    ok_exit_codes: List[int] = field(default_factory=lambda: [0, 1])
    cwd: str = "case"
    env: Dict[str, str] = field(default_factory=dict)
    line_tolerance: int = 1
    ignore_rules: List[str] = field(default_factory=list)
    ignore_paths: List[str] = field(default_factory=list)


@dataclass
class CaseResult:
    case_id: str
    category: str
    scope: bool
    tp: int = 0
    fp: int = 0
    fn: int = 0
    findings: List[Finding] = field(default_factory=list)
    error: str = ""
    duration: float = 0.0


@dataclass
class ToolReport:
    name: str
    spec: ToolSpec
    results: List[CaseResult] = field(default_factory=list)
    tp: int = 0
    fp: int = 0
    fn: int = 0
    crashes: int = 0
    duration: float = 0.0

    @property
    def precision(self) -> Optional[float]:
        total = self.tp + self.fp
        return self.tp / total if total else None

    @property
    def recall(self) -> Optional[float]:
        total = self.tp + self.fn
        return self.tp / total if total else None

    @property
    def f1(self) -> Optional[float]:
        p, r = self.precision, self.recall
        if p is None or r is None or p + r == 0:
            return None
        return 2 * p * r / (p + r)


# ---------------------------------------------------------------------------
# corpus


def _clean_rel(value: str) -> str:
    value = str(value or "").strip().replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value


def _norm_path(value: str, case_dir: Path) -> str:
    value = _clean_rel(value)
    if value.startswith("file://"):
        value = urllib.parse.unquote(value[7:])
    if os.path.isabs(value):
        try:
            value = os.path.relpath(value, str(case_dir)).replace("\\", "/")
        except ValueError:
            pass
    prefix = case_dir.name + "/"
    if value.startswith(prefix):
        candidate = value[len(prefix):]
        # Only strip the case-dir prefix when doing so reveals a real file;
        # a case directory may legitimately contain a top-level dir of the
        # same name.
        if (case_dir / candidate).exists() and not (case_dir / value).exists():
            value = candidate
    return value


def load_corpus(root: Path) -> Tuple[List[Case], List[str], List[str]]:
    """Load and validate every case. Returns (cases, errors, warnings)."""
    cases: List[Case] = []
    errors: List[str] = []
    warnings: List[str] = []
    seen_ids = set()
    if not root.is_dir():
        return [], ["corpus directory not found: %s" % root], []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        manifest = entry / "case.json"
        if not manifest.is_file():
            errors.append("%s: missing case.json" % entry.name)
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            errors.append("%s: invalid case.json (%s)" % (entry.name, exc))
            continue
        if not isinstance(data, dict):
            errors.append("%s: case.json must be an object" % entry.name)
            continue
        case_id = str(data.get("id") or entry.name)
        if case_id in seen_ids:
            errors.append("%s: duplicate case id %r" % (entry.name, case_id))
        seen_ids.add(case_id)
        category = str(data.get("category") or "")
        if category not in CATEGORIES:
            warnings.append("%s: unknown category %r" % (entry.name, category))
        defect = bool(data.get("defect"))
        description = str(data.get("description") or "")
        raw_labels = data.get("labels") or []
        labels: List[Label] = []
        if not isinstance(raw_labels, list):
            errors.append("%s: labels must be a list" % entry.name)
            raw_labels = []
        for index, item in enumerate(raw_labels):
            if not isinstance(item, dict):
                errors.append("%s: label %d is not an object" % (entry.name, index))
                continue
            filename = _clean_rel(str(item.get("file") or ""))
            rule = str(item.get("rule") or "")
            try:
                line = int(item.get("line"))
            except (TypeError, ValueError):
                errors.append("%s: label %d has no integer line" % (entry.name, index))
                continue
            if not filename or not rule:
                errors.append("%s: label %d needs file and rule" % (entry.name, index))
                continue
            if os.path.isabs(filename) or ".." in Path(filename).parts:
                errors.append("%s: label file must stay inside the case: %s"
                              % (entry.name, filename))
                continue
            target = entry / filename
            if not target.is_file():
                errors.append("%s: label file not found: %s" % (entry.name, filename))
                continue
            lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
            if not (1 <= line <= len(lines)):
                errors.append("%s: label line %d out of range for %s (%d lines)"
                              % (entry.name, line, filename, len(lines)))
                continue
            if not lines[line - 1].strip():
                errors.append("%s: label points at a blank line %s:%d"
                              % (entry.name, filename, line))
            aliases = item.get("aliases") or []
            if not isinstance(aliases, list):
                aliases = []
            labels.append(Label(filename.replace("\\", "/"), line, rule,
                                str(item.get("message") or ""),
                                [str(a) for a in aliases]))
        if defect != bool(labels):
            errors.append("%s: defect must be true exactly when labels are present"
                          % entry.name)
        cases.append(Case(case_id, entry, category, defect, description, labels))
    return cases, errors, warnings


# ---------------------------------------------------------------------------
# tools


def load_tools(path: Path) -> List[ToolSpec]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ToolError("could not read tools file %s: %s" % (path, exc))
    raw = data.get("tools") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        raise ToolError("tools file must contain a list or {'tools': [...]}")
    tools: List[ToolSpec] = []
    names = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ToolError("tool %d is not an object" % index)
        name = str(item.get("name") or "").strip()
        if not name:
            raise ToolError("tool %d has no name" % index)
        if name in names:
            raise ToolError("duplicate tool name: %s" % name)
        names.add(name)
        command = item.get("command")
        if isinstance(command, str):
            command = shlex.split(command)
        if not isinstance(command, list) or not command:
            raise ToolError("tool %s needs a command list" % name)
        adapter = str(item.get("adapter") or "json")
        if adapter not in ADAPTERS:
            raise ToolError("tool %s has unknown adapter %r" % (name, adapter))
        match = str(item.get("match") or "location")
        if match not in ("location", "rule"):
            raise ToolError("tool %s has unknown match mode %r" % (name, match))
        categories = item.get("categories")
        if categories is None:
            categories = ["*"]
        if not isinstance(categories, list):
            raise ToolError("tool %s categories must be a list" % name)
        rules = item.get("rules") or {}
        if not isinstance(rules, dict):
            raise ToolError("tool %s rules must be an object" % name)
        try:
            timeout = float(item.get("timeout", 30))
        except (TypeError, ValueError):
            raise ToolError("tool %s has invalid timeout" % name)
        if timeout <= 0:
            raise ToolError("tool %s timeout must be positive" % name)
        stream = str(item.get("stream") or "stdout")
        if stream not in ("stdout", "stderr", "both"):
            raise ToolError("tool %s has unknown stream %r" % (name, stream))
        raw_codes = item.get("ok_exit_codes")
        if raw_codes is None:
            raw_codes = [0, 1]
        if not isinstance(raw_codes, list):
            raise ToolError("tool %s ok_exit_codes must be a list" % name)
        try:
            ok_codes = [int(code) for code in raw_codes]
        except (TypeError, ValueError):
            raise ToolError("tool %s ok_exit_codes must contain integers" % name)
        ignore_rules = item.get("ignore_rules") or []
        ignore_paths = item.get("ignore_paths") or []
        if not isinstance(ignore_rules, list) or not isinstance(ignore_paths, list):
            raise ToolError("tool %s ignore_rules/ignore_paths must be lists" % name)
        cwd = str(item.get("cwd") or "case")
        if cwd != "case":
            raise ToolError("tool %s: cwd is always a staged case copy" % name)
        env = item.get("env") or {}
        if not isinstance(env, dict):
            raise ToolError("tool %s env must be an object" % name)
        tools.append(ToolSpec(
            name=name, command=[str(part) for part in command], adapter=adapter,
            categories=[str(c) for c in categories], match=match,
            rules={str(k): str(v) for k, v in rules.items()},
            timeout=timeout, stream=stream,
            ok_exit_codes=ok_codes,
            cwd=cwd, env={str(k): str(v) for k, v in env.items()},
            ignore_rules=[str(rule) for rule in ignore_rules],
            ignore_paths=[str(path) for path in ignore_paths],
        ))
    return tools


# ---------------------------------------------------------------------------
# adapters


def _first(item: Dict[str, Any], keys: Sequence[str], default: Any = None) -> Any:
    for key in keys:
        value = item.get(key)
        if value is not None and value != "":
            return value
    return default


def _finding(item: Dict[str, Any], case_dir: Path) -> Optional[Finding]:
    if not isinstance(item, dict):
        return None
    filename = _first(item, ("file", "path", "filename", "uri", "target"))
    line = _first(item, ("line", "line_number", "startLine", "start_line"))
    if isinstance(line, dict):
        line = line.get("line") or line.get("startLine")
    if filename is None or line is None:
        return None
    try:
        line_number = int(line)
    except (TypeError, ValueError):
        return None
    if line_number <= 0:
        return None
    rule = _first(item, ("rule", "code", "rule_id", "ruleId", "check_id",
                         "test_id", "id", "kind"), "")
    message = _first(item, ("message", "msg", "text", "issue_text",
                            "description", "title"), "")
    severity = _first(item, ("severity", "level", "confidence"), "")
    return Finding(_norm_path(str(filename), case_dir), line_number,
                   str(rule), str(message), str(severity))


def adapt_json(text: str, case_dir: Path) -> List[Finding]:
    try:
        data = json.loads(text or "[]")
    except ValueError as exc:
        raise AdapterError("invalid JSON: %s" % exc)
    if isinstance(data, dict) and "runs" in data:
        return adapt_sarif(text, case_dir)
    if isinstance(data, dict):
        data = _first(data, ("findings", "results", "issues", "warnings", "errors"), [])
    if not isinstance(data, list):
        raise AdapterError("JSON output is not a list of findings")
    out = []
    for item in data:
        finding = _finding(item, case_dir)
        if finding:
            out.append(finding)
    return out


def adapt_sarif(text: str, case_dir: Path) -> List[Finding]:
    try:
        data = json.loads(text or "{}")
    except ValueError as exc:
        raise AdapterError("invalid SARIF JSON: %s" % exc)
    if not isinstance(data, dict):
        raise AdapterError("SARIF root must be an object")
    out: List[Finding] = []
    for run in data.get("runs") or []:
        if not isinstance(run, dict):
            continue
        driver_rules = ((run.get("tool") or {}).get("driver") or {}).get("rules") or []
        for result in run.get("results") or []:
            if not isinstance(result, dict):
                continue
            rule = str(result.get("ruleId") or "")
            if not rule:
                rule_obj = result.get("rule") or {}
                if isinstance(rule_obj, dict) and rule_obj.get("id"):
                    rule = str(rule_obj["id"])
            if not rule:
                index = result.get("ruleIndex")
                if isinstance(index, int) and 0 <= index < len(driver_rules):
                    descriptor = driver_rules[index]
                    if isinstance(descriptor, dict) and descriptor.get("id"):
                        rule = str(descriptor["id"])
            message_obj = result.get("message") or {}
            message = str(message_obj.get("text") or "") if isinstance(message_obj, dict) else ""
            severity = str(result.get("level") or "")
            location = None
            for candidate in result.get("locations") or []:
                if isinstance(candidate, dict):
                    location = candidate
                    break
            if location is None:
                continue
            physical = location.get("physicalLocation") or {}
            artifact = physical.get("artifactLocation") or {}
            region = physical.get("region") or {}
            uri = artifact.get("uri")
            line = region.get("startLine")
            if uri is None or line is None:
                continue
            try:
                line_number = int(line)
            except (TypeError, ValueError):
                continue
            if line_number <= 0:
                continue
            uri = urllib.parse.unquote(str(uri))
            out.append(Finding(_norm_path(uri, case_dir), line_number,
                               rule, message, severity))
    return out


def adapt_text(text: str, case_dir: Path) -> List[Finding]:
    out: List[Finding] = []
    for raw in (text or "").splitlines():
        line_match = TEXT_FINDING_RE.match(raw.strip())
        if not line_match:
            continue
        filename, line, _column, rest = line_match.groups()
        try:
            line_number = int(line)
        except ValueError:
            continue
        rule = ""
        message = rest
        rule_match = TEXT_RULE_RE.match(rest)
        if rule_match:
            rule = next((part for part in rule_match.groups()[:3] if part), "")
            message = rule_match.group(4)
        out.append(Finding(_norm_path(filename, case_dir), line_number, rule, message))
    return out


def adapt_ruff(text: str, case_dir: Path) -> List[Finding]:
    try:
        data = json.loads(text or "[]")
    except ValueError as exc:
        raise AdapterError("invalid ruff JSON: %s" % exc)
    if not isinstance(data, list):
        raise AdapterError("ruff JSON must be a list")
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        location = item.get("location") or {}
        if not isinstance(location, dict):
            continue
        try:
            line = int(location.get("row") or 0)
        except (TypeError, ValueError):
            continue
        if line <= 0:
            continue
        out.append(Finding(
            _norm_path(str(item.get("filename") or ""), case_dir),
            line,
            str(item.get("code") or ""),
            str(item.get("message") or ""),
        ))
    return [f for f in out if f.file]


def adapt_semgrep(text: str, case_dir: Path) -> List[Finding]:
    try:
        data = json.loads(text or "{}")
    except ValueError as exc:
        raise AdapterError("invalid semgrep JSON: %s" % exc)
    if not isinstance(data, dict):
        raise AdapterError("semgrep JSON must be an object")
    out = []
    for item in data.get("results") or []:
        if not isinstance(item, dict):
            continue
        start = item.get("start") or {}
        if not isinstance(start, dict):
            continue
        try:
            line = int(start.get("line") or 0)
        except (TypeError, ValueError):
            continue
        if line <= 0:
            continue
        extra = item.get("extra") or {}
        if not isinstance(extra, dict):
            extra = {}
        out.append(Finding(
            _norm_path(str(item.get("path") or ""), case_dir),
            line,
            str(item.get("check_id") or ""),
            str(extra.get("message") or ""),
            str(extra.get("severity") or ""),
        ))
    return [f for f in out if f.file]


def adapt_bandit(text: str, case_dir: Path) -> List[Finding]:
    try:
        data = json.loads(text or "{}")
    except ValueError as exc:
        raise AdapterError("invalid bandit JSON: %s" % exc)
    if not isinstance(data, dict):
        raise AdapterError("bandit JSON must be an object")
    out = []
    for item in data.get("results") or []:
        if not isinstance(item, dict):
            continue
        try:
            line = int(item.get("line_number") or 0)
        except (TypeError, ValueError):
            continue
        if line <= 0:
            continue
        out.append(Finding(
            _norm_path(str(item.get("filename") or ""), case_dir),
            line,
            str(item.get("test_id") or ""),
            str(item.get("issue_text") or ""),
            str(item.get("issue_severity") or ""),
        ))
    return [f for f in out if f.file]


ADAPTER_FUNCS: Dict[str, Callable[[str, Path], List[Finding]]] = {
    "json": adapt_json,
    "sarif": adapt_sarif,
    "text": adapt_text,
    "ruff": adapt_ruff,
    "semgrep": adapt_semgrep,
    "bandit": adapt_bandit,
}


# ---------------------------------------------------------------------------
# matching + running


def canonical_rule(raw: str, mapping: Dict[str, str]) -> str:
    if not raw:
        return ""
    if raw in mapping:
        return mapping[raw]
    lowered = raw.lower()
    for key, value in mapping.items():
        if lowered == key.lower() or lowered.startswith(key.lower() + "."):
            return value
    return raw


def match_case(labels: Sequence[Label], findings: Sequence[Finding],
               mode: str, tolerance: int,
               rules: Dict[str, str]) -> Tuple[int, List[Finding], int]:
    """Maximum bipartite matching, so label order cannot change the score."""
    edges: List[List[int]] = []
    for label in labels:
        candidates: List[Tuple[int, int]] = []
        for index, finding in enumerate(findings):
            if finding.file != label.file:
                continue
            distance = abs(finding.line - label.line)
            if distance > tolerance:
                continue
            if mode == "rule":
                rule = canonical_rule(finding.rule, rules)
                allowed = {label.rule} | set(label.aliases)
                if rule not in allowed:
                    continue
            candidates.append((distance, index))
        candidates.sort()
        edges.append([index for _, index in candidates])
    matched_finding = [-1] * len(findings)

    def augment(label_index: int, seen: List[bool]) -> bool:
        for finding_index in edges[label_index]:
            if seen[finding_index]:
                continue
            seen[finding_index] = True
            current = matched_finding[finding_index]
            if current == -1 or augment(current, seen):
                matched_finding[finding_index] = label_index
                return True
        return False

    matched = 0
    for label_index in range(len(labels)):
        if augment(label_index, [False] * len(findings)):
            matched += 1
    used = {index for index, label_index in enumerate(matched_finding) if label_index != -1}
    unmatched = [finding for index, finding in enumerate(findings) if index not in used]
    return matched, unmatched, len(labels) - matched


def substitute(command: Sequence[str], case: Case, corpus_root: Path,
               bench_root: Path, case_dir: Optional[Path] = None) -> List[str]:
    values = {
        "python": sys.executable,
        "case": str((case_dir or case.path).resolve()),
        "corpus": str(corpus_root.resolve()),
        "bench": str(bench_root.resolve()),
    }
    out = []
    for part in command:
        try:
            out.append(part.format(**values))
        except (KeyError, ValueError, IndexError):
            out.append(part)
    return out


def _stage_case(case: Case) -> Path:
    """Copy the case sources to a temp dir so tools cannot read case.json."""
    staged = Path(tempfile.mkdtemp(prefix="linter-bench-case-"))
    for path in case.path.rglob("*"):
        if not path.is_file() or path.name == "case.json":
            continue
        relative = path.relative_to(case.path)
        target = staged / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(path, target)
        except OSError:
            pass
    return staged


def _filter_findings(findings: Sequence[Finding], tool: ToolSpec) -> List[Finding]:
    out: List[Finding] = []
    for finding in findings:
        if finding.rule and any(fnmatch.fnmatch(finding.rule, pattern)
                                for pattern in tool.ignore_rules):
            continue
        if any(fnmatch.fnmatch(finding.file, pattern)
               for pattern in tool.ignore_paths):
            continue
        out.append(finding)
    return out


def run_case(tool: ToolSpec, case: Case, corpus_root: Path, bench_root: Path,
             runner: Callable[..., Any] = subprocess.run) -> CaseResult:
    result = CaseResult(case.id, case.category, True)
    staged = _stage_case(case)
    try:
        command = substitute(tool.command, case, corpus_root, bench_root,
                             case_dir=staged)
        cwd = str(staged)
        env = os.environ.copy()
        env.update(tool.env)
        start = time.perf_counter()
        try:
            completed = runner(command, cwd=cwd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace",
                               timeout=tool.timeout, env=env)
        except subprocess.TimeoutExpired:
            result.error = "timeout after %.0fs" % tool.timeout
            result.duration = round(time.perf_counter() - start, 3)
            result.fn = len(case.labels)
            return result
        except FileNotFoundError:
            result.error = "command not found: %s" % command[0]
            result.duration = round(time.perf_counter() - start, 3)
            result.fn = len(case.labels)
            return result
        except OSError as exc:
            result.error = "could not run: %s" % exc
            result.duration = round(time.perf_counter() - start, 3)
            result.fn = len(case.labels)
            return result
        result.duration = round(time.perf_counter() - start, 3)
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        try:
            if tool.stream == "stdout":
                findings = ADAPTER_FUNCS[tool.adapter](stdout, staged)
            elif tool.stream == "stderr":
                findings = ADAPTER_FUNCS[tool.adapter](stderr, staged)
            else:
                try:
                    findings = ADAPTER_FUNCS[tool.adapter](stdout, staged)
                except Exception:
                    findings = ADAPTER_FUNCS[tool.adapter](stderr, staged)
        except Exception as exc:
            result.error = "adapter: %s" % exc
            result.fn = len(case.labels)
            return result
        findings = _filter_findings(findings, tool)
        result.findings = findings
        if completed.returncode not in tool.ok_exit_codes:
            result.error = "exit %d" % completed.returncode
            result.fn = len(case.labels)
            result.fp = len(findings)
            return result
        tp, fp, fn = match_case(case.labels, findings, tool.match,
                                tool.line_tolerance, tool.rules)
        result.tp, result.fp, result.fn = tp, len(fp), fn
        return result
    finally:
        shutil.rmtree(staged, ignore_errors=True)


# ---------------------------------------------------------------------------
# orchestration + rendering


def _in_scope(tool: ToolSpec, case: Case) -> bool:
    return "*" in tool.categories or case.category in tool.categories


def run_benchmark(tools: Sequence[ToolSpec], cases: Sequence[Case],
                  corpus_root: Path, bench_root: Path, line_tolerance: int = 1,
                  jobs: int = 1,
                  runner: Callable[..., Any] = subprocess.run) -> List[ToolReport]:
    for tool in tools:
        tool.line_tolerance = line_tolerance
    reports: List[ToolReport] = []
    for tool in tools:
        scoped = [case for case in cases if _in_scope(tool, case)]
        tasks = [(tool, case) for case in scoped]
        start = time.perf_counter()
        if jobs > 1 and len(tasks) > 1:
            with ThreadPoolExecutor(max_workers=jobs) as pool:
                results = list(pool.map(
                    lambda pair: run_case(pair[0], pair[1], corpus_root,
                                          bench_root, runner=runner),
                    tasks))
        else:
            results = [run_case(tool, case, corpus_root, bench_root, runner=runner)
                       for case in scoped]
        report = ToolReport(name=tool.name, spec=tool, results=results)
        for item in results:
            report.tp += item.tp
            report.fp += item.fp
            report.fn += item.fn
            if item.error:
                report.crashes += 1
        report.duration = round(time.perf_counter() - start, 3)
        reports.append(report)
    return reports


def _pct(value: Optional[float]) -> str:
    return "-" if value is None else "%.1f%%" % (100.0 * value)


def render_human(corpus_root: Path, cases: Sequence[Case],
                 reports: Sequence[ToolReport], warnings: Sequence[str]) -> str:
    out: List[str] = []
    defects = sum(1 for case in cases if case.defect)
    out.append("linter-bench %s -- corpus: %s (%d cases: %d defect, %d clean)"
               % (__version__, corpus_root, len(cases), defects, len(cases) - defects))
    out.append("")
    out.append("%-22s %5s %4s %4s %4s %9s %9s %8s %6s %7s"
               % ("TOOL", "CASES", "TP", "FP", "FN", "PRECISION", "RECALL",
                  "F1", "CRASH", "TIME"))
    for report in reports:
        out.append("%-22s %5d %4d %4d %4d %9s %9s %8s %6d %7.2fs"
                   % (report.name[:22], sum(1 for r in report.results if r.scope),
                      report.tp, report.fp, report.fn,
                      _pct(report.precision), _pct(report.recall),
                      _pct(report.f1), report.crashes, report.duration))
    failures = [r for r in reports if r.crashes]
    if failures:
        out.append("")
        out.append("CRASHES")
        for report in failures:
            for result in report.results:
                if result.error:
                    out.append("  %s / %s: %s" % (report.name, result.case_id, result.error))
    if warnings:
        out.append("")
        out.append("WARNINGS")
        for warning in warnings:
            out.append("  - %s" % warning)
    out.append("")
    out.append("Recall is computed only over labels in each tool's declared categories.")
    out.append("Findings outside a label's line tolerance count as false positives.")
    return "\n".join(out)


def render_markdown(corpus_root: Path, cases: Sequence[Case],
                    reports: Sequence[ToolReport]) -> str:
    defects = sum(1 for case in cases if case.defect)
    out = ["# linter-bench results", "",
           "Corpus: `%s` — %d cases (%d defect, %d clean)."
           % (corpus_root, len(cases), defects, len(cases) - defects), "",
           "| Tool | Cases | TP | FP | FN | Precision | Recall | F1 | Crashes |",
           "|------|------:|--:|--:|--:|----------:|-------:|---:|--------:|"]
    for report in reports:
        out.append("| %s | %d | %d | %d | %d | %s | %s | %s | %d |"
                   % (report.name.replace("|", "\\|"),
                      sum(1 for r in report.results if r.scope),
                      report.tp, report.fp, report.fn,
                      _pct(report.precision), _pct(report.recall),
                      _pct(report.f1), report.crashes))
    out.append("")
    return "\n".join(out)


def report_json(corpus_root: Path, cases: Sequence[Case],
                reports: Sequence[ToolReport], warnings: Sequence[str],
                line_tolerance: int) -> str:
    payload = {
        "tool": "linter-bench",
        "version": __version__,
        "schema_version": SCHEMA_VERSION,
        "corpus": str(corpus_root),
        "line_tolerance": line_tolerance,
        "cases": [
            {
                "id": case.id,
                "category": case.category,
                "defect": case.defect,
                "description": case.description,
                "labels": [asdict(label) for label in case.labels],
            }
            for case in cases
        ],
        "tools": [
            {
                "name": report.name,
                "adapter": report.spec.adapter,
                "match": report.spec.match,
                "categories": report.spec.categories,
                "tp": report.tp,
                "fp": report.fp,
                "fn": report.fn,
                "precision": report.precision,
                "recall": report.recall,
                "f1": report.f1,
                "crashes": report.crashes,
                "duration": report.duration,
                "results": [
                    {
                        "case": result.case_id,
                        "category": result.category,
                        "tp": result.tp,
                        "fp": result.fp,
                        "fn": result.fn,
                        "error": result.error,
                        "duration": result.duration,
                        "findings": [asdict(finding) for finding in result.findings],
                    }
                    for result in report.results
                ],
            }
            for report in reports
        ],
        "warnings": list(warnings),
    }
    return json.dumps(payload, indent=2, ensure_ascii=True)


# ---------------------------------------------------------------------------
# CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="linter-bench",
        description="Run a labeled corpus against static checkers and score precision/recall.",
    )
    parser.add_argument("--corpus", type=Path, default=Path("corpus"),
                        help="corpus directory (default: corpus)")
    parser.add_argument("--tools", type=Path, default=Path("tools.reference.json"),
                        help="tool configuration JSON (default: tools.reference.json)")
    parser.add_argument("--only", default="", help="comma-separated tool names")
    parser.add_argument("--category", action="append", default=[],
                        help="only cases in this category (repeatable)")
    parser.add_argument("--line-tolerance", type=int, default=1,
                        help="allowed line distance when matching (default: 1)")
    parser.add_argument("--jobs", type=int, default=1, help="parallel tool runs (default: 1)")
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    parser.add_argument("--markdown", action="store_true", help="markdown table")
    parser.add_argument("--validate", action="store_true", help="validate the corpus and exit")
    parser.add_argument("--list", action="store_true", help="list cases and tools")
    parser.add_argument("--fail-under-recall", type=float, default=None, metavar="RATE")
    parser.add_argument("--fail-under-precision", type=float, default=None, metavar="RATE")
    parser.add_argument("--version", action="version", version="linter-bench " + __version__)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.line_tolerance < 0:
        print("linter-bench: error: --line-tolerance must be >= 0", file=sys.stderr)
        return 2
    if args.jobs < 1:
        print("linter-bench: error: --jobs must be >= 1", file=sys.stderr)
        return 2
    for name, value in (("--fail-under-recall", args.fail_under_recall),
                        ("--fail-under-precision", args.fail_under_precision)):
        if value is not None and not (0.0 <= value <= 1.0):
            print("linter-bench: error: %s must be between 0 and 1" % name,
                  file=sys.stderr)
            return 2

    cases, errors, warnings = load_corpus(args.corpus)
    if args.validate:
        for warning in warnings:
            print("warning: %s" % warning, file=sys.stderr)
        if not cases:
            print("error: no cases found in %s" % args.corpus, file=sys.stderr)
            return 1
        if errors:
            for error in errors:
                print("error: %s" % error, file=sys.stderr)
            return 1
        print("corpus OK: %d cases, %d defect, %d clean, %d labels"
              % (len(cases), sum(1 for c in cases if c.defect),
                 sum(1 for c in cases if not c.defect),
                 sum(len(c.labels) for c in cases)))
        return 0
    if errors:
        for error in errors:
            print("linter-bench: error: %s" % error, file=sys.stderr)
        return 1

    try:
        tools = load_tools(args.tools)
    except ToolError as exc:
        print("linter-bench: error: %s" % exc, file=sys.stderr)
        return 2
    if args.only:
        wanted = {part.strip() for part in args.only.split(",") if part.strip()}
        tools = [tool for tool in tools if tool.name in wanted]
    if args.category:
        wanted_categories = set(args.category)
        cases = [case for case in cases if case.category in wanted_categories]
    if not tools:
        print("linter-bench: error: no tools selected", file=sys.stderr)
        return 2
    if not cases:
        print("linter-bench: error: no cases selected", file=sys.stderr)
        return 2

    if args.list:
        lines = ["CASES"]
        for case in cases:
            lines.append("  %-26s %-15s %s (%d labels)"
                         % (case.id, case.category,
                            "defect" if case.defect else "clean",
                            len(case.labels)))
        lines.append("")
        lines.append("TOOLS")
        for tool in tools:
            lines.append("  %-22s adapter=%-8s match=%-8s categories=%s"
                         % (tool.name, tool.adapter, tool.match,
                            ",".join(tool.categories)))
        _emit("\n".join(lines))
        return 0

    reports = run_benchmark(tools, cases, args.corpus, args.tools.parent.resolve(),
                            line_tolerance=args.line_tolerance, jobs=args.jobs)
    if args.json:
        _emit(report_json(args.corpus, cases, reports, warnings, args.line_tolerance))
    elif args.markdown:
        _emit(render_markdown(args.corpus, cases, reports))
    else:
        _emit(render_human(args.corpus, cases, reports, warnings))

    failed = False
    if args.fail_under_recall is not None:
        for report in reports:
            if report.recall is None or report.recall < args.fail_under_recall:
                failed = True
    if args.fail_under_precision is not None:
        for report in reports:
            if report.precision is None or report.precision < args.fail_under_precision:
                failed = True
    return 1 if failed else 0


def _emit(text: str) -> None:
    try:
        sys.stdout.write(text + "\n")
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        sys.stdout.write(text.encode(encoding, "backslashreplace").decode(encoding) + "\n")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        os._exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
