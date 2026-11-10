"""Контракт specs/test-execution: успех требует реально запущенных зелёных тестов."""
import asyncio
import tempfile
import unittest
from pathlib import Path

from harness.executor import RunResult
from harness.tasks import Task
from harness.verifier import TESTS_FAIL, Verifier


class TestExecution(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ws = Path(self.tmp.name)

    def write_test(self, value=1, main=False):
        text = ('import unittest\n'
                'class Example(unittest.TestCase):\n'
                '    def test_value(self):\n'
                f'        self.assertEqual({value}, 1)\n')
        if main:
            text += "if __name__ == '__main__': unittest.main()\n"
        (self.ws / 'test_mylib.py').write_text(text, encoding='utf-8')

    def verdict(self):
        task = Task('t', 'coding', '', 1, 'unittest', self.ws)
        return asyncio.run(Verifier(1500).check(task, RunResult(0, '', '', False)))

    def test_red_without_main_is_rejected(self):
        self.write_test(2)
        result = self.verdict()
        self.assertFalse(result.ok)
        self.assertEqual(result.kind, TESTS_FAIL)
        self.assertIn('2 != 1', result.problem)

    def test_green_with_and_without_main_runs_once(self):
        from harness.test_runner import run_tests
        for main in (False, True):
            with self.subTest(main=main):
                self.write_test(main=main)
                result = asyncio.run(run_tests(self.ws, 'test_mylib.py', 10))
                self.assertTrue(result.ok, result.stderr)
                self.assertIn('Ran 1 test', result.stderr)

    def test_empty_test_module_is_rejected(self):
        (self.ws / 'test_mylib.py').write_text('import unittest\n', encoding='utf-8')
        self.assertFalse(self.verdict().ok)

    def test_no_test_files_is_not_verified(self):
        self.assertFalse(self.verdict().ok)

    def test_import_failure_is_reported(self):
        (self.ws / 'test_mylib.py').write_text('import missing_test_dependency_123\n', encoding='utf-8')
        result = self.verdict()
        self.assertFalse(result.ok)
        self.assertIn('missing_test_dependency_123', result.problem)

    def test_early_success_exit_is_not_verified(self):
        (self.ws / 'test_mylib.py').write_text('raise SystemExit(0)\n', encoding='utf-8')
        self.assertFalse(self.verdict().ok)

    def test_snapshot_contains_actual_failure_without_main(self):
        import json
        from harness.snapshot import WorkspaceSnapshot
        self.write_test(2)
        cfg = json.loads((Path(__file__).resolve().parents[1] / 'config/harness.json').read_text())
        task = Task('t', 'coding', '', 1, 'unittest', self.ws)
        text = asyncio.run(WorkspaceSnapshot(cfg['snapshot']).build(task))
        self.assertIn('2 != 1', text)

    def test_fresh_module_after_same_size_edit(self):
        self.write_test()
        lib = self.ws / 'mylib.py'
        lib.write_text('value = 1\n', encoding='utf-8')
        test = self.ws / 'test_mylib.py'
        test.write_text('import unittest, mylib\nclass T(unittest.TestCase):\n'
                        '    def test_value(self): self.assertEqual(mylib.value, 1)\n', encoding='utf-8')
        self.assertTrue(self.verdict().ok)
        lib.write_text('value = 2\n', encoding='utf-8')
        self.assertFalse(self.verdict().ok)

    def test_skipped_tests_do_not_confirm_execution(self):
        self.write_test()
        p = self.ws / 'test_mylib.py'
        p.write_text(p.read_text().replace('class Example', "@unittest.skip('unavailable')\nclass Example"),
                     encoding='utf-8')
        self.assertFalse(self.verdict().ok)

    def test_syntax_error_is_reported(self):
        (self.ws / 'test_mylib.py').write_text('def broken(:\n', encoding='utf-8')
        self.assertIn('SyntaxError', self.verdict().problem)

    def test_timeout_is_a_failure(self):
        from harness.test_runner import run_tests
        (self.ws / 'test_mylib.py').write_text('import time\ntime.sleep(10)\n', encoding='utf-8')
        result = asyncio.run(run_tests(self.ws, 'test_mylib.py', 0.1))
        self.assertTrue(result.timed_out)
        self.assertFalse(result.ok)

    def test_parallel_workspaces_are_isolated(self):
        from harness.test_runner import run_tests
        self.write_test()
        other = self.ws / 'other'
        other.mkdir()
        (other / 'test_mylib.py').write_text('raise RuntimeError("other workspace")\n', encoding='utf-8')

        async def both():
            return await asyncio.gather(run_tests(self.ws, 'test_mylib.py', 10),
                                        run_tests(other, 'test_mylib.py', 10))

        green, red = asyncio.run(both())
        self.assertTrue(green.ok, green.stderr)
        self.assertFalse(red.ok)
        self.assertIn('other workspace', red.stderr)
