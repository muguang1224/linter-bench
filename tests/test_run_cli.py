import json
import subprocess
import unittest
from pathlib import Path

from common import ROOT, lb, tmpdir, write_case, write_tools, run_main, completed


class RunCaseTests(unittest.TestCase):
    def make(self):
        root = tmpdir(self)
        case_dir = write_case(root, "c1", labels=[
            {"file": "app/x.py", "line": 1, "rule": "r.one"},
            {"file": "app/x.py", "line": 2, "rule": "r.two"},
        ], files={"app/x.py": "line one\nline two\n"})
        cases, errors, _ = lb.load_corpus(root)
        self.assertEqual(errors, [])
        return root, cases[0]

    def tool(self, **kwargs):
        base = dict(name="t", command=["tool"], adapter="json", match="rule",
                    categories=["*"], rules={"X1": "r.one", "X2": "r.two"})
        base.update(kwargs)
        return lb.ToolSpec(**base)

    def test_malformed_adapter_output_is_skipped(self):
        root, case = self.make()
        payload = '[{"filename": "app/x.py", "location": {"row": "zz"}}]'
        result = lb.run_case(self.tool(adapter="ruff"), case, root, root,
                             runner=lambda *a, **k: completed(payload))
        self.assertEqual(result.error, "")
        self.assertEqual(result.fn, 2)

    def test_literal_braces_in_command(self):
        root, case = self.make()
        seen = {}
        def runner(command, **kwargs):
            seen["command"] = command
            return completed("[]")
        lb.run_case(self.tool(command=["tool", "{}", "{case}"]), case, root, root,
                    runner=runner)
        self.assertEqual(seen["command"][1], "{}")

    def test_staging_hides_case_json(self):
        root, case = self.make()
        seen = {}
        def runner(command, **kwargs):
            cwd = Path(kwargs["cwd"])
            seen["manifest"] = (cwd / "case.json").exists()
            seen["source"] = (cwd / "app" / "x.py").exists()
            return completed("[]")
        lb.run_case(self.tool(), case, root, root, runner=runner)
        self.assertFalse(seen["manifest"])
        self.assertTrue(seen["source"])

    def test_both_stream_falls_back_to_stderr(self):
        root, case = self.make()
        payload = json.dumps([{"file": "app/x.py", "line": 1, "rule": "X1"}])
        result = lb.run_case(self.tool(stream="both"), case, root, root,
                             runner=lambda *a, **k: completed(
                                 "warning: starting", payload))
        self.assertEqual(result.tp, 1)

    def test_perfect_tool(self):
        root, case = self.make()
        payload = json.dumps([
            {"file": "app/x.py", "line": 1, "rule": "X1"},
            {"file": "app/x.py", "line": 2, "rule": "X2"},
        ])
        result = lb.run_case(self.tool(), case, root, root,
                             runner=lambda *a, **k: completed(payload))
        self.assertEqual((result.tp, result.fp, result.fn), (2, 0, 0))
        self.assertEqual(result.error, "")

    def test_miss_and_extra(self):
        root, case = self.make()
        payload = json.dumps([{"file": "app/x.py", "line": 1, "rule": "X1"},
                              {"file": "app/x.py", "line": 9, "rule": "X1"}])
        result = lb.run_case(self.tool(), case, root, root,
                             runner=lambda *a, **k: completed(payload))
        self.assertEqual((result.tp, result.fp, result.fn), (1, 1, 1))

    def test_nonzero_exit_is_crash(self):
        root, case = self.make()
        payload = json.dumps([{"file": "app/x.py", "line": 1, "rule": "X1"}])
        result = lb.run_case(self.tool(), case, root, root,
                             runner=lambda *a, **k: completed(payload, returncode=2))
        self.assertEqual(result.error, "exit 2")
        self.assertEqual((result.tp, result.fp, result.fn), (0, 1, 2))

    def test_allowed_exit_code(self):
        root, case = self.make()
        payload = json.dumps([{"file": "app/x.py", "line": 1, "rule": "X1"}])
        result = lb.run_case(self.tool(ok_exit_codes=[0, 1, 2]), case, root, root,
                             runner=lambda *a, **k: completed(payload, returncode=2))
        self.assertEqual(result.error, "")
        self.assertEqual(result.tp, 1)

    def test_timeout(self):
        root, case = self.make()
        def runner(*a, **k):
            raise subprocess.TimeoutExpired(cmd="tool", timeout=1)
        result = lb.run_case(self.tool(), case, root, root, runner=runner)
        self.assertIn("timeout", result.error)
        self.assertEqual(result.fn, 2)

    def test_command_missing(self):
        root, case = self.make()
        def runner(*a, **k):
            raise FileNotFoundError()
        result = lb.run_case(self.tool(), case, root, root, runner=runner)
        self.assertIn("not found", result.error)

    def test_adapter_error(self):
        root, case = self.make()
        result = lb.run_case(self.tool(), case, root, root,
                             runner=lambda *a, **k: completed("not json"))
        self.assertIn("adapter", result.error)
        self.assertEqual(result.fn, 2)

    def test_stderr_stream(self):
        root, case = self.make()
        payload = json.dumps([{"file": "app/x.py", "line": 1, "rule": "X1"}])
        result = lb.run_case(self.tool(stream="stderr"), case, root, root,
                             runner=lambda *a, **k: completed("", payload))
        self.assertEqual(result.tp, 1)

    def test_case_placeholder_is_absolute(self):
        root, case = self.make()
        seen = {}
        def runner(command, **kwargs):
            seen["command"] = command
            seen["cwd"] = kwargs.get("cwd")
            return completed("[]")
        lb.run_case(self.tool(command=["tool", "{case}"]), case, root, root,
                    runner=runner)
        self.assertTrue(Path(seen["command"][1]).is_absolute())
        self.assertTrue(Path(seen["cwd"]).is_absolute())


class BenchmarkTests(unittest.TestCase):
    def make(self):
        root = tmpdir(self)
        write_case(root, "t1", "test-quality",
                   labels=[{"file": "app/x.py", "line": 1, "rule": "r"}],
                   files={"app/x.py": "one\n"})
        write_case(root, "s1", "security",
                   labels=[{"file": "app/x.py", "line": 1, "rule": "r"}],
                   files={"app/x.py": "one\n"})
        write_case(root, "clean", "test-quality", defect=False, labels=[],
                   files={"app/x.py": "one\n"})
        cases, errors, _ = lb.load_corpus(root)
        self.assertEqual(errors, [])
        return root, cases

    def test_category_scope(self):
        root, cases = self.make()
        tool = lb.ToolSpec("t", ["tool"], adapter="json",
                           categories=["security"], match="location")
        payload = json.dumps([{"file": "app/x.py", "line": 1}])
        report = lb.run_benchmark([tool], cases, root, root,
                                  runner=lambda *a, **k: completed(payload))
        self.assertEqual(len(report[0].results), 1)
        self.assertEqual(report[0].results[0].case_id, "s1")
        self.assertEqual(report[0].tp, 1)

    def test_star_scope_includes_clean(self):
        root, cases = self.make()
        tool = lb.ToolSpec("t", ["tool"], adapter="json", categories=["*"])
        report = lb.run_benchmark([tool], cases, root, root)
        self.assertEqual(len(report[0].results), 3)

    def test_metrics(self):
        root, cases = self.make()
        tool = lb.ToolSpec("t", ["tool"], adapter="json", categories=["security"],
                           match="location")
        payload = json.dumps([{"file": "app/x.py", "line": 1},
                              {"file": "nope.py", "line": 1}])
        report = lb.run_benchmark([tool], cases, root, root,
                                  runner=lambda *a, **k: completed(payload))
        self.assertEqual(report[0].tp, 1)
        self.assertEqual(report[0].fp, 1)
        self.assertEqual(report[0].fn, 0)
        self.assertAlmostEqual(report[0].precision, 0.5)
        self.assertEqual(report[0].recall, 1.0)

    def test_parallel_same_metrics(self):
        root, cases = self.make()
        tool = lb.ToolSpec("t", ["tool"], adapter="json", categories=["*"])
        payload = json.dumps([{"file": "app/x.py", "line": 1}])
        def runner(*a, **k):
            return completed(payload)
        report = lb.run_benchmark([tool], cases, root, root, jobs=2, runner=runner)
        self.assertEqual(report[0].tp, 2)


class CliTests(unittest.TestCase):
    def test_validate(self):
        code, out = run_main("--corpus", ROOT / "corpus", "--validate")
        self.assertEqual(code, 0)
        self.assertIn("corpus OK", out)

    def test_list(self):
        code, out = run_main("--corpus", ROOT / "corpus",
                             "--tools", ROOT / "tools.reference.json", "--list")
        self.assertEqual(code, 0)
        self.assertIn("tq-no-assert", out)
        self.assertIn("reference", out)

    def test_reference_json(self):
        code, out = run_main("--corpus", ROOT / "corpus",
                             "--tools", ROOT / "tools.reference.json", "--json")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        tool = payload["tools"][0]
        self.assertEqual(tool["tp"], 14)
        self.assertEqual(tool["fp"], 0)
        self.assertEqual(tool["fn"], 0)
        self.assertEqual(tool["recall"], 1.0)

    def test_markdown(self):
        code, out = run_main("--corpus", ROOT / "corpus",
                             "--tools", ROOT / "tools.reference.json", "--markdown")
        self.assertEqual(code, 0)
        self.assertIn("| Tool | Cases |", out)

    def test_only_unknown(self):
        code, _ = run_main("--corpus", ROOT / "corpus",
                           "--tools", ROOT / "tools.reference.json",
                           "--only", "nope")
        self.assertEqual(code, 2)

    def test_category_filter(self):
        code, out = run_main("--corpus", ROOT / "corpus",
                             "--tools", ROOT / "tools.reference.json",
                             "--category", "security", "--json")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(len(payload["cases"]), 4)
        self.assertEqual(payload["tools"][0]["tp"], 3)

    def test_invalid_tolerance(self):
        code, _ = run_main("--corpus", ROOT / "corpus",
                           "--tools", ROOT / "tools.reference.json",
                           "--line-tolerance", "-1")
        self.assertEqual(code, 2)

    def test_fail_under_recall(self):
        root = tmpdir(self)
        write_case(root, "c", "test-quality",
                   labels=[{"file": "app/x.py", "line": 1, "rule": "r"}],
                   files={"app/x.py": "one\n"})
        tools = write_tools(root / "tools.json", [{
            "name": "empty",
            "command": ["tool"],
            "adapter": "json",
            "categories": ["test-quality"],
        }])
        code, _ = run_main("--corpus", root, "--tools", tools,
                           "--fail-under-recall", "0.5")
        self.assertEqual(code, 1)

    def test_fail_under_precision(self):
        root = tmpdir(self)
        write_case(root, "c", "test-quality",
                   labels=[{"file": "app/x.py", "line": 1, "rule": "r"}],
                   files={"app/x.py": "one\n"})
        # A tool that reports the label plus two false positives.
        script = root / "tool.py"
        script.write_text(
            "import json\nprint(json.dumps(["
            "{'file':'app/x.py','line':1},"
            "{'file':'app/x.py','line':2},"
            "{'file':'app/x.py','line':3}]))\n", encoding="utf-8")
        tools = write_tools(root / "tools.json", [{
            "name": "noisy",
            "command": ["{python}", str(script)],
            "adapter": "json",
            "categories": ["test-quality"],
        }])
        code, _ = run_main("--corpus", root, "--tools", tools,
                           "--fail-under-precision", "0.9")
        self.assertEqual(code, 1)

    def test_fail_under_recall_with_no_labels_fails(self):
        root = tmpdir(self)
        write_case(root, "s", "security",
                   labels=[{"file": "app/x.py", "line": 1, "rule": "r"}],
                   files={"app/x.py": "one\n"})
        tools = write_tools(root / "tools.json", [{
            "name": "empty",
            "command": ["tool"],
            "adapter": "json",
            "categories": ["dead-code"],
        }])
        code, _ = run_main("--corpus", root, "--tools", tools,
                           "--fail-under-recall", "0.9")
        self.assertEqual(code, 1)

    def test_controls_positive_and_negative(self):
        code, out = run_main("--corpus", ROOT / "corpus",
                             "--tools", ROOT / "tools.controls.json", "--json")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        scores = {tool["name"]: tool["f1"] for tool in payload["tools"]}
        self.assertEqual(scores["reference"], 1.0)
        self.assertIsNotNone(scores["naive"])
        self.assertLess(scores["naive"], 1.0)

    def test_json_is_ascii_safe(self):
        code, out = run_main("--corpus", ROOT / "corpus",
                             "--tools", ROOT / "tools.reference.json", "--json")
        self.assertEqual(code, 0)
        out.encode("ascii")


if __name__ == "__main__":
    unittest.main()
