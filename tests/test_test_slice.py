"""Срез тест-файла (specs/token-cuts-2 шаг 3): прошедшие тест-методы убираются, всё, что может быть причиной
провала, остаётся; при любом сомнении — файл как есть."""
import ast
import asyncio
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.settings import load_settings_raw  # noqa: E402
from harness.snapshot import WorkspaceSnapshot  # noqa: E402
from harness.tasks import Task  # noqa: E402
from harness.test_slice import drop_passing, failed_tests  # noqa: E402

NOTE = "# {n} passing tests omitted"
LIB = "def add(a, b):\n    return a - b\n\n\ndef neg(x):\n    return -x\n"
TESTS = '''import unittest

import mylib as m

LIMIT = 3


class T(unittest.TestCase):
    def setUp(self):
        self.k = 2

    def helper(self, x):
        return x * self.k

    def test_neg(self):
        self.assertEqual(m.neg(2), -2)

    @unittest.skipIf(False, "never")
    def test_neg_zero(self):
        self.assertEqual(m.neg(0), 0)

    def test_add(self):
        self.assertEqual(m.add(1, 2), 3)


if __name__ == '__main__':
    unittest.main()
'''
OUTPUT = ("..F\n======\nFAIL: test_add (test_mylib.T.test_add)\n------\nTraceback...\nAssertionError: -1 != 3\n"
          "------\nRan 3 tests in 0.001s\n\nFAILED (failures=1)\n")


class FailedTests(unittest.TestCase):
    def test_names_of_failed_tests(self):
        self.assertEqual(failed_tests(OUTPUT), {"test_add"})

    def test_all_failed_or_none_failed_or_not_unittest(self):
        self.assertIsNone(failed_tests(OUTPUT.replace("Ran 3 tests", "Ran 1 test")))
        self.assertIsNone(failed_tests("...\nRan 3 tests in 0.001s\n\nOK\n"))
        self.assertIsNone(failed_tests("Traceback (most recent call last):\nImportError: no mylib\n"))


class DropPassing(unittest.TestCase):
    def test_passing_methods_dropped_rest_kept(self):
        out = drop_passing(TESTS, {"test_add"}, NOTE)
        ast.parse(out)  # остаётся корректным Python
        self.assertNotIn("test_neg", out)
        self.assertNotIn("skipIf", out)  # декоратор уходит вместе с методом
        for kept in ("import mylib as m", "LIMIT = 3", "def setUp", "def helper", "def test_add",
                     "m.add(1, 2), 3", "unittest.main()", "    # 2 passing tests omitted"):
            self.assertIn(kept, out)
        self.assertLess(len(out), len(TESTS))

    def test_failure_not_a_test_method_keeps_file(self):
        for failed in ({"setUpClass"}, {"test_mylib"}, {"test_add", "test_missing"}):
            with self.subTest(failed=failed):
                self.assertEqual(drop_passing(TESTS, failed, NOTE), TESTS)

    def test_unparsable_file_kept(self):
        broken = TESTS.replace("def test_add(self):", "def test_add(self:")
        self.assertEqual(drop_passing(broken, {"test_add"}, NOTE), broken)


class SnapshotTrim(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        ws = self.tmp / "t" / "workspace"
        ws.mkdir(parents=True)
        (ws / "mylib.py").write_text(LIB, encoding="utf-8", newline="")
        (ws / "test_mylib.py").write_text(TESTS, encoding="utf-8", newline="")
        self.task = Task("t", "coding", "Fix mylib.py so that test_mylib.py passes.", 1, "unittest", self.tmp / "t")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def snap(self, trim: bool) -> str:
        cfg = dict(load_settings_raw()["snapshot"], trim_passing_tests=trim)
        return asyncio.run(WorkspaceSnapshot(cfg).build(self.task))

    def test_trim_on_shows_only_failed_test_methods(self):
        text = self.snap(True)
        self.assertIn("def test_add", text)
        self.assertNotIn("def test_neg(", text)
        self.assertIn("# 2 passing tests omitted", text)
        self.assertIn("return a - b", text)  # модуль целиком

    def test_trim_off_keeps_whole_file(self):
        self.assertIn("def test_neg(", self.snap(False))


if __name__ == "__main__":
    unittest.main()
