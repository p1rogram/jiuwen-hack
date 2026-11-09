"""answer.txt: только рядом с task.json; у задачи без принятого ответа файла нет. Ожидания — из письменного обзора
вопросов организаторов (чат, 07.11.2026): «answer.txt — рядом с task.json», «невыполнимая — не создавайте файл»."""
import asyncio
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


if __name__ == "__main__":
    unittest.main()
