"""Отказ модели на этапе 1 (spec token-cuts, шаг 2): маркер → статус unsolvable, без повтора и этапа 2, без answer.txt.

Ожидания — из spec `specs/token-cuts/spec.md` (шаг 2), не из кода.
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.executor import RunResult  # noqa: E402
from harness.prompting import Stage1Prompt  # noqa: E402
from harness.tasks import Task  # noqa: E402
from harness.verifier import BAD_FORMAT, UNSOLVABLE, Verdict, Verifier  # noqa: E402
from tests.test_decision import prep  # noqa: E402
from tests.test_harness_emv import harness, run  # noqa: E402

MARK = "UNSOLVABLE"


def check(stdout: str, check_kind: str = "exact_str", marker: str | None = MARK) -> Verdict:
    task = Task("t", "x", "", 1, check_kind, Path("."))
    return asyncio.run(Verifier(500, marker).check(task, RunResult(0, stdout, "", False)))


class MarkerVerdict(unittest.TestCase):
    def test_marker_line_is_unsolvable(self):
        v = check("files read\nUNSOLVABLE\n")
        self.assertEqual((v.ok, v.answer, v.kind), (False, "", UNSOLVABLE))

    def test_marker_for_int_and_unittest_checks(self):
        self.assertEqual(check("UNSOLVABLE\n", "exact_int").kind, UNSOLVABLE)
        self.assertEqual(check("UNSOLVABLE\n", "unittest").kind, UNSOLVABLE)

    def test_marker_disabled_is_a_plain_answer(self):
        # флаг выключен — поведение как до шага 2: строка — обычный ответ
        v = check("UNSOLVABLE\n", marker=None)
        self.assertEqual((v.ok, v.answer), (True, "UNSOLVABLE"))

    def test_marker_inside_answer_is_not_a_refusal(self):
        # должно быть отклонено: маркер — только вся последняя строка, а не часть ответа
        self.assertEqual(check("UNSOLVABLE_COUNT\n").answer, "UNSOLVABLE_COUNT")
        self.assertEqual(check("UNSOLVABLE\n", "exact_int").kind, UNSOLVABLE)
        self.assertEqual(check("no number here\n", "exact_int").kind, BAD_FORMAT)


class MarkerPrompt(unittest.TestCase):
    def test_rule_added_only_when_enabled(self):
        on = Stage1Prompt(None, MARK)._system
        off = Stage1Prompt(None, None)._system
        self.assertIn(MARK, on)
        self.assertNotIn(MARK, off)


class RefusingStage1:
    def __init__(self, ledger):
        self.ledger, self.calls = ledger, 0

    def can_retry(self, outcome):
        return outcome.attempts < 2

    async def first(self, prepared, outcome):
        self.calls += 1
        self.ledger.charge(100, prepared.task.task_id)
        outcome.cost += 100
        outcome.attempts += 1
        outcome.calls.append({"stage": f"stage1.{outcome.attempts}", "in": 0, "out": 100})
        return Verdict(False, "", "model reported the task unsolvable", UNSOLVABLE)

    async def retry(self, prepared, outcome, failed):
        return await self.first(prepared, outcome)


class RecordingWriter:
    def __init__(self):
        self.recorded, self.discarded = [], []

    def record(self, task, answer):
        self.recorded.append(task.task_id)

    def discard(self, task):
        self.discarded.append(task.task_id)


class RefusalStopsTask(unittest.TestCase):
    def test_refusal_has_no_retry_no_stage2_no_answer(self):
        # должно пройти: при λ = 0 обычный провал ушёл бы в этап 2 (test_harness_emv), отказ — нет
        h = harness(100_000)
        h.stage1 = RefusingStage1(h.ledger)
        h.solver._s1 = h.stage1
        h.writer = RecordingWriter()
        rec = run(h, [prep("a")])["a"]
        self.assertEqual(rec["status"], "unsolvable")
        self.assertEqual((h.stage1.calls, h.stage2.tasks, rec["decisions"]), (1, [], []))
        self.assertEqual((h.writer.recorded, h.writer.discarded), ([], ["a"]))


if __name__ == "__main__":
    unittest.main()
