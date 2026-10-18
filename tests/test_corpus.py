import json
import unittest

from common import ROOT, lb, tmpdir, write_case


class CorpusTests(unittest.TestCase):
    def test_real_corpus_is_valid(self):
        cases, errors, warnings = lb.load_corpus(ROOT / "corpus")
        self.assertEqual(errors, [])
        self.assertEqual(len(cases), 20)
        self.assertEqual(sum(1 for c in cases if c.defect), 13)
        self.assertEqual(sum(1 for c in cases if not c.defect), 7)
        self.assertEqual(sum(len(c.labels) for c in cases), 14)
        self.assertEqual(warnings, [])

    def test_every_label_line_is_nonblank_and_in_range(self):
        cases, _, _ = lb.load_corpus(ROOT / "corpus")
        for case in cases:
            for label in case.labels:
                lines = (case.path / label.file).read_text(encoding="utf-8").splitlines()
                self.assertLessEqual(1, label.line)
                self.assertLessEqual(label.line, len(lines))
                self.assertTrue(lines[label.line - 1].strip(),
                                "%s:%d is blank" % (label.file, label.line))

    def test_ids_are_unique(self):
        cases, _, _ = lb.load_corpus(ROOT / "corpus")
        ids = [case.id for case in cases]
        self.assertEqual(len(ids), len(set(ids)))

    def test_missing_labels_file(self):
        root = tmpdir(self)
        write_case(root, "x", labels=[{"file": "missing.py", "line": 1, "rule": "r"}])
        _, errors, _ = lb.load_corpus(root)
        self.assertTrue(any("not found" in error for error in errors))

    def test_label_line_out_of_range(self):
        root = tmpdir(self)
        write_case(root, "x", labels=[{"file": "app/x.py", "line": 99, "rule": "r"}])
        _, errors, _ = lb.load_corpus(root)
        self.assertTrue(any("out of range" in error for error in errors))

    def test_label_on_blank_line(self):
        root = tmpdir(self)
        write_case(root, "x", files={"app/x.py": "def x():\n\n    return 1\n"},
                   labels=[{"file": "app/x.py", "line": 2, "rule": "r"}])
        _, errors, _ = lb.load_corpus(root)
        self.assertTrue(any("blank line" in error for error in errors))

    def test_defect_mismatch(self):
        root = tmpdir(self)
        write_case(root, "x", defect=True, labels=[])
        _, errors, _ = lb.load_corpus(root)
        self.assertTrue(any("defect" in error for error in errors))

    def test_path_traversal_rejected(self):
        root = tmpdir(self)
        write_case(root, "x", labels=[{"file": "../outside.py", "line": 1, "rule": "r"}])
        _, errors, _ = lb.load_corpus(root)
        self.assertTrue(any("inside the case" in error for error in errors))

    def test_unknown_category_is_warning(self):
        root = tmpdir(self)
        write_case(root, "x", category="mystery")
        _, errors, warnings = lb.load_corpus(root)
        self.assertEqual(errors, [])
        self.assertTrue(any("unknown category" in warning for warning in warnings))

    def test_missing_case_json(self):
        root = tmpdir(self)
        (root / "empty").mkdir()
        _, errors, _ = lb.load_corpus(root)
        self.assertTrue(any("missing case.json" in error for error in errors))

    def test_invalid_json(self):
        root = tmpdir(self)
        (root / "bad").mkdir()
        (root / "bad" / "case.json").write_text("{", encoding="utf-8")
        _, errors, _ = lb.load_corpus(root)
        self.assertTrue(any("invalid case.json" in error for error in errors))

    def test_duplicate_ids(self):
        root = tmpdir(self)
        write_case(root, "a")
        path_b = root / "b"
        path_b.mkdir()
        (path_b / "case.json").write_text(json.dumps({
            "id": "a", "category": "test-quality", "defect": False,
            "description": "", "labels": []}), encoding="utf-8")
        _, errors, _ = lb.load_corpus(root)
        self.assertTrue(any("duplicate case id" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
