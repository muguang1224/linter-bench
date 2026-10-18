import json
import unittest
from pathlib import Path

from common import lb, tmpdir


CASE = Path("/tmp/case")


class MatchTests(unittest.TestCase):
    def test_exact_location(self):
        labels = [lb.Label("a.py", 10, "rule.x")]
        findings = [lb.Finding("a.py", 10, "other")]
        tp, fp, fn = lb.match_case(labels, findings, "location", 0, {})
        self.assertEqual((tp, len(fp), fn), (1, 0, 0))

    def test_tolerance(self):
        labels = [lb.Label("a.py", 10, "rule.x")]
        findings = [lb.Finding("a.py", 12, "other")]
        self.assertEqual(lb.match_case(labels, findings, "location", 2, {})[0], 1)
        self.assertEqual(lb.match_case(labels, findings, "location", 1, {})[0], 0)

    def test_wrong_file(self):
        labels = [lb.Label("a.py", 10, "rule.x")]
        findings = [lb.Finding("b.py", 10, "rule.x")]
        tp, fp, fn = lb.match_case(labels, findings, "rule", 0, {})
        self.assertEqual((tp, len(fp), fn), (0, 1, 1))

    def test_rule_mode_requires_mapping(self):
        labels = [lb.Label("a.py", 10, "test.no-assert")]
        findings = [lb.Finding("a.py", 10, "WA001")]
        self.assertEqual(lb.match_case(labels, findings, "rule", 0, {})[0], 0)
        self.assertEqual(lb.match_case(
            labels, findings, "rule", 0, {"WA001": "test.no-assert"})[0], 1)

    def test_rule_mode_accepts_aliases(self):
        labels = [lb.Label("a.py", 10, "test.no-assert", aliases=["no-assert"])]
        findings = [lb.Finding("a.py", 10, "no-assert")]
        self.assertEqual(lb.match_case(labels, findings, "rule", 0, {})[0], 1)

    def test_one_to_one(self):
        labels = [lb.Label("a.py", 10, "r"), lb.Label("a.py", 10, "r")]
        findings = [lb.Finding("a.py", 10, "r")]
        tp, fp, fn = lb.match_case(labels, findings, "location", 0, {})
        self.assertEqual((tp, len(fp), fn), (1, 0, 1))

    def test_duplicate_finding_is_false_positive(self):
        labels = [lb.Label("a.py", 10, "r")]
        findings = [lb.Finding("a.py", 10, "r"), lb.Finding("a.py", 10, "r")]
        tp, fp, fn = lb.match_case(labels, findings, "location", 0, {})
        self.assertEqual((tp, len(fp), fn), (1, 1, 0))

    def test_closest_line_wins(self):
        labels = [lb.Label("a.py", 10, "r")]
        findings = [lb.Finding("a.py", 12, "r"), lb.Finding("a.py", 10, "r")]
        tp, fp, _ = lb.match_case(labels, findings, "location", 2, {})
        self.assertEqual(tp, 1)
        self.assertEqual(len(fp), 1)
        self.assertEqual(fp[0].line, 12)

    def test_clean_case_any_finding_is_fp(self):
        tp, fp, fn = lb.match_case([], [lb.Finding("a.py", 1, "r")], "location", 0, {})
        self.assertEqual((tp, len(fp), fn), (0, 1, 0))


class AdapterTests(unittest.TestCase):
    def test_json_list(self):
        text = json.dumps([{"file": "a.py", "line": 3, "rule": "X1", "message": "m"}])
        findings = lb.adapt_json(text, CASE)
        self.assertEqual(findings[0].file, "a.py")
        self.assertEqual(findings[0].line, 3)
        self.assertEqual(findings[0].rule, "X1")

    def test_maximum_matching_is_order_independent(self):
        labels = [lb.Label("a.py", 10, "r"), lb.Label("a.py", 11, "r")]
        findings = [lb.Finding("a.py", 9, "r"), lb.Finding("a.py", 10, "r")]
        self.assertEqual(lb.match_case(labels, findings, "location", 1, {})[0], 2)
        self.assertEqual(lb.match_case(list(reversed(labels)), findings,
                                       "location", 1, {})[0], 2)

    def test_norm_path_keeps_same_named_subdir(self):
        root = tmpdir(self)
        case_dir = root / "app"
        (case_dir / "app").mkdir(parents=True)
        (case_dir / "app" / "f.py").write_text("x = 1\n", encoding="utf-8")
        self.assertEqual(lb._norm_path("app/f.py", case_dir), "app/f.py")

    def test_sarif_uses_primary_location_only(self):
        sarif = {"runs": [{"results": [{
            "ruleId": "R",
            "locations": [
                {"physicalLocation": {"artifactLocation": {"uri": "a.py"},
                                      "region": {"startLine": 3}}},
                {"physicalLocation": {"artifactLocation": {"uri": "a.py"},
                                      "region": {"startLine": 3}}},
            ]}]}]}
        self.assertEqual(len(lb.adapt_sarif(json.dumps(sarif), CASE)), 1)
        self.assertEqual(len(lb.adapt_sarif(json.dumps(sarif), CASE)), 1)

    def test_sarif_rule_index_and_uri_decoding(self):
        sarif = {"runs": [{
            "tool": {"driver": {"rules": [{"id": "RULE.ONE"}]}},
            "results": [{
                "ruleIndex": 0,
                "locations": [{"physicalLocation": {
                    "artifactLocation": {"uri": "app%2Fx.py"},
                    "region": {"startLine": 4}}}]}]}]}
        finding = lb.adapt_sarif(json.dumps(sarif), CASE)[0]
        self.assertEqual(finding.rule, "RULE.ONE")
        self.assertEqual(finding.file, "app/x.py")

    def test_json_findings_key(self):
        text = json.dumps({"findings": [{"path": "a.py", "line_number": 4}]})
        self.assertEqual(lb.adapt_json(text, CASE)[0].line, 4)

    def test_json_absolute_path(self):
        text = json.dumps([{"file": "/tmp/case/a.py", "line": 1}])
        self.assertEqual(lb.adapt_json(text, CASE)[0].file, "a.py")

    def test_json_case_prefix(self):
        root = tmpdir(self)
        case_dir = root / "case"
        case_dir.mkdir()
        (case_dir / "a.py").write_text("x = 1\n", encoding="utf-8")
        text = json.dumps([{"file": "case/a.py", "line": 1}])
        self.assertEqual(lb.adapt_json(text, case_dir)[0].file, "a.py")

    def test_invalid_json(self):
        with self.assertRaises(lb.AdapterError):
            lb.adapt_json("not json", CASE)

    def test_sarif(self):
        sarif = {"version": "2.1.0", "runs": [{"results": [{
            "ruleId": "R1",
            "message": {"text": "problem"},
            "locations": [{"physicalLocation": {
                "artifactLocation": {"uri": "src/a.py"},
                "region": {"startLine": 7}}}]}]}]}
        findings = lb.adapt_sarif(json.dumps(sarif), CASE)
        self.assertEqual((findings[0].file, findings[0].line, findings[0].rule),
                         ("src/a.py", 7, "R1"))

    def test_json_dispatches_to_sarif(self):
        sarif = {"runs": [{"results": [{"ruleId": "R",
                 "locations": [{"physicalLocation": {
                     "artifactLocation": {"uri": "a.py"},
                     "region": {"startLine": 2}}}]}]}]}
        self.assertEqual(lb.adapt_json(json.dumps(sarif), CASE)[0].rule, "R")

    def test_text_with_code(self):
        findings = lb.adapt_text("tests/test_a.py:4: WA001 no assertion\n", CASE)
        self.assertEqual((findings[0].file, findings[0].line, findings[0].rule),
                         ("tests/test_a.py", 4, "WA001"))
        self.assertEqual(findings[0].message, "no assertion")

    def test_text_with_colon_and_brackets(self):
        findings = lb.adapt_text("a.py:3:5: [DT002] swallowed error\n", CASE)
        self.assertEqual(findings[0].line, 3)
        self.assertEqual(findings[0].rule, "DT002")

    def test_text_without_code(self):
        findings = lb.adapt_text("a.py:9: something happened\n", CASE)
        self.assertEqual(findings[0].rule, "")
        self.assertEqual(findings[0].message, "something happened")

    def test_text_dotted_rule(self):
        findings = lb.adapt_text("a.py:1: test.no-assert: no check\n", CASE)
        self.assertEqual(findings[0].rule, "test.no-assert")

    def test_ruff(self):
        text = json.dumps([{"code": "F401", "message": "unused",
                            "filename": "a.py", "location": {"row": 5}}])
        findings = lb.adapt_ruff(text, CASE)
        self.assertEqual((findings[0].rule, findings[0].line), ("F401", 5))

    def test_semgrep(self):
        text = json.dumps({"results": [{"check_id": "python.x", "path": "a.py",
                                        "start": {"line": 6},
                                        "extra": {"message": "m", "severity": "ERROR"}}]})
        findings = lb.adapt_semgrep(text, CASE)
        self.assertEqual((findings[0].rule, findings[0].line), ("python.x", 6))

    def test_bandit(self):
        text = json.dumps({"results": [{"test_id": "B602", "filename": "a.py",
                                        "line_number": 8, "issue_text": "shell"}]})
        findings = lb.adapt_bandit(text, CASE)
        self.assertEqual((findings[0].rule, findings[0].line), ("B602", 8))

    def test_text_ignores_noise(self):
        self.assertEqual(lb.adapt_text("Scanning...\nDone\n", CASE), [])


class ToolConfigTests(unittest.TestCase):
    def test_defaults_and_string_command(self):
        root = tmpdir(self)
        path = root / "tools.json"
        path.write_text(json.dumps({"tools": [{
            "name": "t", "command": "python -m tool --check"}]}), encoding="utf-8")
        tools = lb.load_tools(path)
        self.assertEqual(tools[0].command, ["python", "-m", "tool", "--check"])
        self.assertEqual(tools[0].adapter, "json")
        self.assertEqual(tools[0].categories, ["*"])
        self.assertEqual(tools[0].match, "location")

    def test_duplicate_names(self):
        root = tmpdir(self)
        path = root / "tools.json"
        path.write_text(json.dumps({"tools": [
            {"name": "t", "command": ["x"]},
            {"name": "t", "command": ["y"]}]}), encoding="utf-8")
        with self.assertRaises(lb.ToolError):
            lb.load_tools(path)

    def test_bad_adapter(self):
        root = tmpdir(self)
        path = root / "tools.json"
        path.write_text(json.dumps({"tools": [
            {"name": "t", "command": ["x"], "adapter": "nope"}]}), encoding="utf-8")
        with self.assertRaises(lb.ToolError):
            lb.load_tools(path)

    def test_bad_match(self):
        root = tmpdir(self)
        path = root / "tools.json"
        path.write_text(json.dumps({"tools": [
            {"name": "t", "command": ["x"], "match": "fuzzy"}]}), encoding="utf-8")
        with self.assertRaises(lb.ToolError):
            lb.load_tools(path)

    def test_empty_lists_are_preserved(self):
        root = tmpdir(self)
        path = root / "tools.json"
        path.write_text(json.dumps({"tools": [{
            "name": "t", "command": ["x"], "categories": [],
            "ok_exit_codes": []}]}), encoding="utf-8")
        tool = lb.load_tools(path)[0]
        self.assertEqual(tool.categories, [])
        self.assertEqual(tool.ok_exit_codes, [])

    def test_no_tools_file(self):
        with self.assertRaises(lb.ToolError):
            lb.load_tools(Path("/does/not/exist.json"))


if __name__ == "__main__":
    unittest.main()
