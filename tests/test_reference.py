import json
import subprocess
import sys
import unittest

from common import ROOT, tmpdir

import reference_detector as ref


def detect(case_id):
    return ref.detect(ROOT / "corpus" / case_id)


def rules(case_id):
    return {(item["file"], item["line"], item["rule"]) for item in detect(case_id)}


class ReferenceDetectorTests(unittest.TestCase):
    def test_no_assert(self):
        self.assertIn(("tests/test_calc.py", 3, "test.no-assert"), rules("tq-no-assert"))

    def test_mock_assertion_is_not_no_assert(self):
        self.assertEqual(rules("tq-clean-mock"), set())

    def test_constant_assert(self):
        self.assertIn(("tests/test_flags.py", 2, "test.constant-assert"),
                      rules("tq-constant-assert"))

    def test_self_compare(self):
        self.assertIn(("tests/test_math.py", 3, "test.self-compare"),
                      rules("tq-self-compare"))

    def test_dead_assert(self):
        self.assertIn(("tests/test_dead.py", 3, "test.dead-assert"),
                      rules("tq-dead-assert"))

    def test_swallowed_assert(self):
        self.assertIn(("tests/test_swallow.py", 3, "test.swallowed-assert"),
                      rules("tq-swallowed-assert"))

    def test_overbroad_except(self):
        self.assertIn(("tests/test_overbroad.py", 7, "test.overbroad-except"),
                      rules("tq-overbroad-except"))

    def test_skip_without_reason(self):
        self.assertIn(("tests/test_skip.py", 4, "test.skip-no-reason"),
                      rules("tq-skip-no-reason"))

    def test_unused_function(self):
        self.assertIn(("app/core.py", 4, "dead.unused-function"),
                      rules("dc-unused-function"))

    def test_unused_class(self):
        self.assertIn(("app/model.py", 4, "dead.unused-class"),
                      rules("dc-unused-class"))

    def test_covered_functions_not_flagged(self):
        self.assertEqual(rules("dc-clean-covered"), set())

    def test_dunder_not_flagged(self):
        self.assertEqual(rules("dc-clean-dunder"), set())

    def test_except_pass(self):
        self.assertIn(("app/loader.py", 5, "error.except-pass"),
                      rules("eh-except-pass"))

    def test_handled_exception_not_flagged(self):
        self.assertEqual(rules("eh-clean-handled"), set())

    def test_unchecked_subprocess(self):
        self.assertIn(("app/runner.py", 4, "error.unchecked-subprocess"),
                      rules("eh-unchecked-subprocess"))

    def test_checked_subprocess_not_flagged(self):
        self.assertEqual(rules("eh-clean-subprocess"), set())

    def test_hardcoded_secrets(self):
        found = rules("sec-hardcoded-password")
        self.assertIn(("app/config.py", 1, "security.hardcoded-secret"), found)
        self.assertIn(("app/config.py", 2, "security.hardcoded-secret"), found)

    def test_environment_secret_not_flagged(self):
        self.assertEqual(rules("sec-clean-env"), set())

    def test_shell_true(self):
        self.assertIn(("app/shell.py", 4, "security.shell-true"),
                      rules("sec-shell-true"))

    def test_clean_log_not_flagged(self):
        self.assertEqual(rules("sec-clean-log"), set())

    def test_hidden_parent_directory_does_not_disable_detector(self):
        root = tmpdir(self)
        case = root / ".hidden" / "case"
        (case / "app").mkdir(parents=True)
        (case / "app" / "x.py").write_text("def orphan():\n    return 1\n", encoding="utf-8")
        self.assertTrue(ref.detect(case))

    def test_import_alias_counts_as_use(self):
        root = tmpdir(self)
        case = root / "case"
        (case / "app").mkdir(parents=True)
        (case / "tests").mkdir()
        (case / "app" / "core.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
        (case / "tests" / "test_core.py").write_text(
            "from app.core import helper as h\n\ndef test_h():\n    assert h() == 1\n",
            encoding="utf-8")
        self.assertEqual(ref.detect(case), [])

    def test_check_false_is_unchecked(self):
        root = tmpdir(self)
        case = root / "case"
        (case / "app").mkdir(parents=True)
        (case / "app" / "x.py").write_text(
            "import subprocess\n\ndef run(c):\n    return subprocess.run(c, check=False)\n",
            encoding="utf-8")
        rules = {(item["line"], item["rule"]) for item in ref.detect(case)}
        self.assertIn((4, "error.unchecked-subprocess"), rules)

    def test_truthy_shell_is_flagged(self):
        root = tmpdir(self)
        case = root / "case"
        (case / "app").mkdir(parents=True)
        (case / "app" / "x.py").write_text(
            "import subprocess\n\ndef run(c):\n    return subprocess.run(c, shell=1)\n",
            encoding="utf-8")
        rules = {(item["line"], item["rule"]) for item in ref.detect(case)}
        self.assertIn((4, "security.shell-true"), rules)

    def test_tuple_exception_is_overbroad(self):
        root = tmpdir(self)
        case = root / "case"
        (case / "tests").mkdir(parents=True)
        (case / "tests" / "test_x.py").write_text(
            "def test_x():\n    try:\n        pass\n"
            "    except (ValueError, Exception):\n        pass\n",
            encoding="utf-8")
        rules = {(item["line"], item["rule"]) for item in ref.detect(case)}
        self.assertIn((4, "test.overbroad-except"), rules)

    def test_test_ranges_are_file_scoped(self):
        root = tmpdir(self)
        case = root / "case"
        (case / "app").mkdir(parents=True)
        (case / "tests").mkdir()
        (case / "app" / "prod.py").write_text(
            "def load():\n    try:\n        return 1\n"
            "    except OSError:\n        pass\n", encoding="utf-8")
        (case / "tests" / "test_x.py").write_text(
            "def test_one():\n    assert 1\n    x = 1\n    assert x\n",
            encoding="utf-8")
        rules = {(item["file"], item["line"], item["rule"])
                 for item in ref.detect(case)}
        self.assertIn(("app/prod.py", 4, "error.except-pass"), rules)

    def test_cli_outputs_json(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "reference_detector.py"),
             str(ROOT / "corpus" / "tq-no-assert")],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload[0]["rule"], "test.no-assert")


if __name__ == "__main__":
    unittest.main()
