"""Harness: резерв снимается на любом исходе, отложенные задачи получают второй проход. Ожидания — из spec
(specs/cost-model/spec.md: допуск по high с резервом, `skipped_budget` не блокирует остальные) и ревью."""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.budget import BudgetLedger  # noqa: E402
from harness.decision import DecisionPolicy  # noqa: E402
from harness.llm import BudgetExhausted  # noqa: E402
from harness.planner import TaskPlanner  # noqa: E402
from harness.prompting import Prepared  # noqa: E402
from harness.success_model import SuccessModel  # noqa: E402
from harness.tasks import Task  # noqa: E402
from run_agent import Harness  # noqa: E402
from tests.test_cost_model import model, stats  # noqa: E402


class FakeLog:
    def __init__(self):
        self.records = []

    def write(self, rec):
        self.records.append(rec)


class FakeWriter:
    def record(self, task, answer):
        pass

    def discard(self, task):
        pass


class FakePrompt:
    async def build(self, task):
        return Prepared(task, [{"role": "user", "content": ""}])


class FakeSolver:
    """Списывает cost с бюджета как шлюз; raise_after — бросить исключение после оплаченного вызова."""

    def __init__(self, ledger, cost, raise_exc=None):
        self.ledger, self.cost, self.raise_exc = ledger, cost, raise_exc

    async def solve(self, prepared, outcome):
        await asyncio.sleep(0.01)
        self.ledger.charge(self.cost, prepared.task.task_id)
        outcome.cost += self.cost
        outcome.calls.append({"model": "m", "in": 0, "out": int(self.cost), "reasoning": 0})
        if self.raise_exc:
            raise self.raise_exc
        outcome.answer, outcome.verified, outcome.attempts = "1", True, 1
        return outcome


SUCCESS = {"branches": {"default": {"default": {"alpha": 1, "beta": 0}}}, "p_feasible": {"default": {"alpha": 1, "beta": 1}}}


def harness(budget, cost_per_task, types, raise_exc=None, parallel=5):
    h = Harness.__new__(Harness)
    h.ledger = BudgetLedger(budget)
    h.log, h.writer, h.prompt = FakeLog(), FakeWriter(), FakePrompt()
    h.solver = FakeSolver(h.ledger, cost_per_task, raise_exc)
    cost = model(types)
    h.policy = DecisionPolicy(SuccessModel(SUCCESS), cost, h.ledger)
    h.planner = TaskPlanner(cost, h.ledger, h.policy.priority)
    h.pending = {}
    h.parallel, h.detector, h.skip_severities = parallel, None, ["hard"]
    return h


def tasks(n, task_type="t", reward=1.0):
    return [Task(f"{task_type}{i}", task_type, "", reward, "", Path(".")) for i in range(n)]


class HarnessBudget(unittest.TestCase):
    def test_reservation_released_after_budget_exhausted(self):
        h = harness(1000, 100, {"t": stats(500)}, raise_exc=BudgetExhausted("x"))
        recs = asyncio.run(h.run(tasks(1)))
        self.assertEqual(recs[0]["status"], "budget_exhausted")
        self.assertEqual(recs[0]["cost"], 100)  # оплаченный вызов виден в журнале
        self.assertAlmostEqual(h.ledger.available, 900)

    def test_reservation_released_after_error(self):
        h = harness(1000, 100, {"t": stats(500)}, raise_exc=ValueError("boom"))
        recs = asyncio.run(h.run(tasks(1)))
        self.assertEqual(recs[0]["status"], "error")
        self.assertAlmostEqual(h.ledger.available, 900)

    def test_deferred_task_runs_when_reservations_settle(self):
        # high 500 у каждой, бюджет 1000, факт 100: в первом проходе 3-я не помещается (2 × 500 в резерве),
        # после завершения первых двух остаток 800 — помещается
        h = harness(1000, 100, {"t": stats(500)})
        recs = asyncio.run(h.run(tasks(3)))
        self.assertEqual(sorted(r["status"] for r in recs), ["verified"] * 3)
        self.assertAlmostEqual(h.ledger.spent, 300)

    def test_skipped_budget_only_after_second_pass(self):
        h = harness(1000, 600, {"t": stats(600)})
        recs = asyncio.run(h.run(tasks(2)))
        self.assertEqual(sorted(r["status"] for r in recs), ["skipped_budget", "verified"])
        self.assertEqual(sum(r["kind"] == "task" for r in h.log.records), 2)



class BrokenDetector:
    def check(self, task):
        raise RuntimeError("detector boom")


class BrokenPrompt:
    async def build(self, task):
        raise RuntimeError("prompt boom")


class DetectorFailure(unittest.TestCase):
    """spec robustness, шаг 3: сбой детектора невыполнимых не отбрасывает задачу."""

    def test_detector_error_does_not_drop_task(self):
        # должно пройти: детектор бросает → задача решена без сигналов, в журнале — событие detector_error
        h = harness(10_000, 100, {"t": stats(100)})
        h.detector = BrokenDetector()
        recs = asyncio.run(h.run(tasks(1)))
        self.assertEqual([r["status"] for r in recs], ["verified"])
        errors = [r for r in h.log.records if r["kind"] == "detector_error"]
        self.assertEqual(len(errors), 1)
        self.assertIn("detector boom", errors[0]["error"])

    def test_prompt_error_is_still_task_error(self):
        # должно быть отклонено: без сообщения задачи решать нечего — по-прежнему status error
        h = harness(10_000, 100, {"t": stats(100)})
        h.detector, h.prompt = BrokenDetector(), BrokenPrompt()
        recs = asyncio.run(h.run(tasks(1)))
        self.assertEqual([r["status"] for r in recs], ["error"])
        self.assertIn("prompt boom", recs[0]["error"])


class BrokenPlanner(TaskPlanner):
    def admit(self, prepared):
        if prepared.task.task_id == "t0":
            raise RuntimeError("admit boom")
        return super().admit(prepared)


class PhaseBFailures(unittest.TestCase):
    """Аудит edc1c14, A10–A11: без ключа API и при сбое планировщика одной задачи остальные не теряются."""

    def test_admit_error_stays_with_its_task(self):
        # должно пройти: исключение в admit у t0 → error только у неё, t1 и t2 решены
        h = harness(10_000, 100, {"t": stats(100)})
        h.planner = BrokenPlanner(h.planner._model, h.ledger, h.policy.priority)
        recs = {r["task_id"]: r["status"] for r in asyncio.run(h.run(tasks(3)))}
        self.assertEqual(recs, {"t0": "error", "t1": "verified", "t2": "verified"})

    def test_without_key_llm_tasks_skipped_without_calls(self):
        # должно быть отклонено: без ключа к LLM не обращаемся — задача skipped_no_key, бюджет не тронут
        h = harness(10_000, 100, {"t": stats(100)})
        h.llm_ready = False
        recs = asyncio.run(h.run(tasks(2)))
        self.assertEqual([r["status"] for r in recs], ["skipped_no_key"] * 2)
        self.assertEqual(h.ledger.spent, 0)


if __name__ == "__main__":
    unittest.main()
