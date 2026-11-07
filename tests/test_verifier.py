"""Нормализация ответа. Ожидаемые значения — из task.json примеров (expected), а не из кода."""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.executor import RunResult  # noqa: E402
from harness.tasks import Task  # noqa: E402
from harness.verifier import Verifier  # noqa: E402


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

    def test_int_takes_last_number(self):
        # math_0000: «After final split into 3: 389», expected = "389"
        self.assertEqual(verdict("exact_int", "After final split into 3: 389\n").answer, "389")


if __name__ == "__main__":
    unittest.main()
