"""Нормализация ответа. Ожидаемые значения — из task.json примеров (expected), а не из кода."""
import asyncio
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.executor import RunResult  # noqa: E402
from harness.tasks import Task  # noqa: E402
from harness.verifier import BAD_FORMAT, SCRIPT_ERROR, TESTS_FAIL, Verifier  # noqa: E402


def task(check: str) -> Task:
    return Task("t", "x", "", 1, check, Path("."))


def verdict(check: str, stdout: str):
    return asyncio.run(Verifier(500).check(task(check), RunResult(0, stdout, "", False)))


class AnswerNormalization(unittest.TestCase):
    def test_label_prefix_is_dropped_for_strings(self):
        # search_0005: модель напечатала «Headquarters: Osaka», expected = "Osaka"
        self.assertEqual(verdict("exact_str", "Headquarters: Osaka\n").answer, "Osaka")

    def test_plain_string_kept(self):
        self.assertEqual(verdict("exact_str", "Osaka\n").answer, "Osaka")

    def test_time_like_string_kept(self):
        self.assertEqual(verdict("exact_str", "12:30\n").answer, "12:30")

    def test_long_label_with_dot_is_dropped(self):
        # gen3/search_0008: модель напечатала «Answer written to answer.txt: Utrecht», expected = "Utrecht"
        self.assertEqual(verdict("exact_str", "Answer written to answer.txt: Utrecht\n").answer, "Utrecht")

    def test_integral_float_is_an_int(self):
        # gen3/data_analysis_0017: скрипт напечатал «8825.0» (price как float), expected = "8825"
        v = verdict("exact_int", "8825.0\n")
        self.assertEqual((v.ok, v.answer), (True, "8825"))

    def test_fractional_number_is_not_accepted_as_int(self):
        self.assertFalse(verdict("exact_int", "12.5\n").ok)

    def test_thousands_separator_kept(self):
        self.assertEqual(verdict("exact_int", "Total: 1,234\n").answer, "1234")

    def test_int_takes_last_number(self):
        # math_0000: «After final split into 3: 389», expected = "389"
        self.assertEqual(verdict("exact_int", "After final split into 3: 389\n").answer, "389")


class FailureKinds(unittest.TestCase):
    """Виды провала — из spec (specs/stage2-emv/spec.md): исключение/таймаут, тесты, нет числа / пустой вывод."""

    def check(self, t, run):
        return asyncio.run(Verifier(500).check(t, run))

    def test_exception_and_timeout_are_script_error(self):
        self.assertEqual(self.check(task("exact_int"), RunResult(1, "", "Traceback", False)).kind, SCRIPT_ERROR)
        self.assertEqual(self.check(task("exact_int"), RunResult(-1, "", "", True)).kind, SCRIPT_ERROR)

    def test_no_number_and_empty_output_are_bad_format(self):
        self.assertEqual(verdict("exact_int", "done\n").kind, BAD_FORMAT)
        self.assertEqual(verdict("exact_str", "").kind, BAD_FORMAT)

    def test_failing_tests_are_tests_fail(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "test_x.py").write_text("raise SystemExit(1)\n", encoding="utf-8")
            v = self.check(Task("t", "coding", "", 1, "unittest", Path(d)), RunResult(0, "", "", False))
        self.assertEqual((v.ok, v.kind), (False, TESTS_FAIL))

    def test_answer_file_written_by_this_run_is_the_answer(self):
        # повтор search_0001: скрипт записал answer.txt и ничего не напечатал — ответ есть, повтор не нужен
        # mtime на Windows отстаёт от time.time() до тика таймера (замер: до 15,7 мс) — файл этого запуска
        # бывает «старше» started; воспроизводим отставание явно, а не надеемся на часы
        with tempfile.TemporaryDirectory() as d:
            started = time.time()
            Path(d, "answer.txt").write_text("Utrecht\n", encoding="utf-8")
            os.utime(Path(d, "answer.txt"), (started - 0.016, started - 0.016))
            v = self.check(Task("t", "search", "", 1, "exact_str", Path(d)), RunResult(0, "", "", False, started))
        self.assertEqual((v.ok, v.answer), (True, "Utrecht"))

    def test_answer_file_of_previous_attempt_is_ignored(self):
        # между попытками всегда есть вызов LLM (≥ 1 с): файл прошлой попытки за секунду до запуска — не ответ
        with tempfile.TemporaryDirectory() as d:
            started = time.time()
            Path(d, "answer.txt").write_text("Old\n", encoding="utf-8")
            os.utime(Path(d, "answer.txt"), (started - 1.0, started - 1.0))
            v = self.check(Task("t", "search", "", 1, "exact_str", Path(d)), RunResult(0, "", "", False, started))
        self.assertEqual((v.ok, v.kind), (False, BAD_FORMAT))

    def test_stale_answer_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "answer.txt").write_text("Old\n", encoding="utf-8")
            os.utime(Path(d, "answer.txt"), (time.time() - 60, time.time() - 60))
            v = self.check(Task("t", "search", "", 1, "exact_str", Path(d)), RunResult(0, "", "", False, time.time()))
        self.assertEqual((v.ok, v.kind), (False, BAD_FORMAT))


if __name__ == "__main__":
    unittest.main()
