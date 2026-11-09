"""answer.txt: только рядом с task.json; у задачи без принятого ответа файла нет. Ожидания — из письменного обзора
вопросов организаторов (чат, 07.11.2026): «answer.txt — рядом с task.json», «невыполнимая — не создавайте файл»."""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.llm import BudgetExhausted  # noqa: E402
from harness.results import ResultsWriter  # noqa: E402
from harness.tasks import Task  # noqa: E402
from tests.test_cost_model import stats  # noqa: E402
from tests.test_harness_budget import harness  # noqa: E402


def make_task(root: Path, name: str, with_workspace: bool) -> Task:
    task_dir = root / name
    task_dir.mkdir()
    if with_workspace:
        (task_dir / "workspace").mkdir()
    return Task(name, "t", "", 1.0, "exact_int", task_dir)


class ModelWritesFileSolver:
    """Скрипт модели пишет answer.txt в рабочую папку (у math без workspace/ это папка задачи), харнесс ответ не принял."""

    def __init__(self, raise_exc=None):
        self.raise_exc = raise_exc

    async def solve(self, prepared, outcome):
        (prepared.task.workspace / "answer.txt").write_text("42\n", encoding="utf-8")
        if self.raise_exc:
            raise self.raise_exc
        return outcome


class ResultsWriterFiles(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_answer_written_next_to_task_json_only(self):
        task = make_task(self.root, "data_0", with_workspace=True)
        ResultsWriter(None).record(task, "17")
        self.assertEqual((task.task_dir / "answer.txt").read_text(encoding="utf-8").strip(), "17")
        self.assertFalse((task.workspace / "answer.txt").exists())

    def test_discard_removes_file_and_tolerates_missing(self):
        task = make_task(self.root, "math_0", with_workspace=False)
        (task.task_dir / "answer.txt").write_text("42\n", encoding="utf-8")
        writer = ResultsWriter(None)
        writer.discard(task)
        self.assertFalse((task.task_dir / "answer.txt").exists())
        writer.discard(task)

    def _run(self, solver) -> Task:
        task = make_task(self.root, "math_0", with_workspace=False)
        h = harness(10_000, 100, {"t": stats(100)})
        h.writer, h.solver = ResultsWriter(None), solver
        asyncio.run(h.run([task]))
        return task

    def test_no_answer_leaves_no_file_from_model_script(self):
        task = self._run(ModelWritesFileSolver())
        self.assertFalse((task.task_dir / "answer.txt").exists())

    def test_solver_error_leaves_no_file(self):
        task = self._run(ModelWritesFileSolver(RuntimeError("boom")))
        self.assertFalse((task.task_dir / "answer.txt").exists())

    def test_budget_exhausted_leaves_no_file(self):
        task = self._run(ModelWritesFileSolver(BudgetExhausted("x")))
        self.assertFalse((task.task_dir / "answer.txt").exists())



class AnswerSolver:
    """Решатель, принявший ответ 7."""

    async def solve(self, prepared, outcome):
        outcome.answer, outcome.verified = "7", True
        return outcome


class WriteFailures(unittest.TestCase):
    """spec robustness, шаг 1: ошибка записи одного файла не обрывает прогон и не теряет ответы в OUT."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.out = self.root / "out" / "answers.json"

    def tearDown(self):
        self._tmp.cleanup()

    def out_answers(self) -> dict:
        return json.loads(self.out.read_text(encoding="utf-8"))

    @staticmethod
    def block_answer_file(task: Task) -> None:
        # каталог на месте answer.txt — запись и удаление файла падают с OSError на любой ОС
        (task.task_dir / "answer.txt").mkdir()

    def test_out_is_valid_json_from_start(self):
        writer = ResultsWriter(self.out)
        self.assertEqual(writer.init_error, "")
        self.assertEqual(self.out_answers(), {})

    def test_unwritable_task_dir_keeps_answer_in_out(self):
        # должно пройти: ответ задачи с недоступной папкой — в OUT, следующая задача пишется как обычно
        bad, good = make_task(self.root, "bad", False), make_task(self.root, "good", False)
        self.block_answer_file(bad)
        writer = ResultsWriter(self.out)
        self.assertIn("answer.txt", writer.record(bad, "1"))
        self.assertEqual(writer.record(good, "2"), "")
        self.assertEqual(self.out_answers(), {"bad": "1", "good": "2"})
        self.assertEqual((good.task_dir / "answer.txt").read_text(encoding="utf-8").strip(), "2")

    def test_unwritable_out_still_writes_answer_file(self):
        (self.root / "file").write_text("x", encoding="utf-8")
        task = make_task(self.root, "t", False)
        writer = ResultsWriter(self.root / "file" / "answers.json")
        self.assertIn("OUT", writer.init_error)
        self.assertIn("OUT", writer.record(task, "5"))
        self.assertEqual((task.task_dir / "answer.txt").read_text(encoding="utf-8").strip(), "5")

    def test_discard_error_is_returned(self):
        task = make_task(self.root, "t", False)
        self.block_answer_file(task)
        self.assertIn("answer.txt", ResultsWriter(None).discard(task))

    def _run(self, solver, tasks) -> list[dict]:
        h = harness(10_000, 100, {"t": stats(100)})
        h.writer, h.solver = ResultsWriter(self.out), solver
        return asyncio.run(h.run(tasks))

    def test_run_continues_and_logs_write_error(self):
        bad, good = make_task(self.root, "bad", False), make_task(self.root, "good", False)
        self.block_answer_file(bad)
        recs = {r["task_id"]: r for r in self._run(AnswerSolver(), [bad, good])}
        self.assertIn("answer.txt", recs["bad"]["write_error"])
        self.assertNotIn("write_error", recs["good"])
        self.assertEqual(self.out_answers(), {"bad": "7", "good": "7"})

    def test_task_without_answer_is_not_in_out(self):
        # должно быть отклонено: без принятого ответа — ни answer.txt, ни записи в OUT (обзор организаторов, 07.11)
        task = make_task(self.root, "t", False)
        self._run(ModelWritesFileSolver(), [task])
        self.assertFalse((task.task_dir / "answer.txt").exists())
        self.assertEqual(self.out_answers(), {})


if __name__ == "__main__":
    unittest.main()
