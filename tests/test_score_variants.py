"""Приёмка offline scorer по specs/independent-holdout/spec.md."""
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tools.gen.score_variants import tests_pass


class TestOfflineScorer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ws = Path(self.tmp.name) / "ws"
        self.ws.mkdir()

    def write_case(self, assertion):
        (self.ws / "test_case.py").write_text(
            "import unittest\nclass Tests(unittest.TestCase):\n"
            f"    def test_value(self):\n        {assertion}\n", encoding="utf-8")

    def test_red_without_main_is_rejected(self):
        self.write_case("self.assertEqual(1, 2)")
        self.assertFalse(tests_pass(self.ws, None)[0])

    def test_green_without_main_runs(self):
        self.write_case("self.assertEqual(1, 1)")
        self.assertEqual(tests_pass(self.ws, None), (True, True))

    def test_empty_and_skip_rejected(self):
        self.assertFalse(tests_pass(self.ws, None)[0])
        self.write_case("self.skipTest('skip')")
        self.assertFalse(tests_pass(self.ws, None)[0])

    def test_import_failure(self):
        (self.ws / "test_case.py").write_text("import missing_holdout_module", encoding="utf-8")
        self.assertFalse(tests_pass(self.ws, None)[0])

    def test_required_hidden_and_legacy(self):
        self.write_case("self.assertTrue(True)")
        missing = self.ws.parent / "absent.py"
        self.assertEqual(tests_pass(self.ws, missing), (True, True))
        self.assertEqual(tests_pass(self.ws, missing, require_hidden=True), (True, False))

    def test_hidden_failure_and_parallel_isolation(self):
        self.write_case("self.assertTrue(True)")
        hidden = self.ws.parent / "secret.py"
        hidden.write_text("import unittest\nclass Hidden(unittest.TestCase):\n"
                          "    def test_hidden(self): self.fail('hidden')\n", encoding="utf-8")
        before = {p.name: p.read_bytes() for p in self.ws.iterdir()}
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: tests_pass(self.ws, hidden), range(2)))
        self.assertEqual(results, [(True, False)] * 2)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.ws.iterdir()})

    def test_timeout_rejected(self):
        self.write_case("__import__('time').sleep(10)")
        self.assertFalse(tests_pass(self.ws, None, timeout_sec=.2)[0])
