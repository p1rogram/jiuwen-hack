"""Harness с деревом решений: ключевой критерий парой из spec (specs/stage2-emv/spec.md) на уровне прогона —
статусы задач, вызовы этапа 2 и токены. Этапы поддельные, политика, цены и λ — настоящие."""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.budget import BudgetLedger  # noqa: E402
from harness.cost_model import CostModel  # noqa: E402
from harness.decision import DecisionPolicy  # noqa: E402
from harness.pipeline import TaskPipeline  # noqa: E402
from harness.outcome import Outcome  # noqa: E402
from harness.planner import TaskPlanner  # noqa: E402
from harness.success_model import SuccessModel  # noqa: E402
from harness.verifier import TESTS_FAIL, Verdict  # noqa: E402
from run_agent import Harness  # noqa: E402
from tests.test_decision import COST, SUCCESS, prep  # noqa: E402
from tests.test_harness_budget import FakeLog, FakePrompt, FakeWriter  # noqa: E402


class FailingStage1:
    """Каждая задача стоит 100 и проваливается с tests_fail."""

    def __init__(self, ledger):
        self.ledger, self.tasks = ledger, []

    def can_retry(self, outcome):
        return outcome.attempts < 2

    async def first(self, prepared, outcome):
        await asyncio.sleep(0.01)
        self.tasks.append(prepared.task.task_id)
        self.ledger.charge(100, prepared.task.task_id)
        outcome.cost += 100
        outcome.attempts += 1
        outcome.calls.append({"stage": f"stage1.{outcome.attempts}", "in": 0, "out": 100})
        return Verdict(False, "", "tests fail", TESTS_FAIL)

    async def retry(self, prepared, outcome, failed):
        return await self.first(prepared, outcome)


class CountingStage2:
    def __init__(self):
        self.tasks = []

    async def solve(self, prepared, outcome, failed, budget):
        self.tasks.append(prepared.task.task_id)
        return Verdict(True, "42")


def harness(budget):
    h = Harness.__new__(Harness)
    h.ledger = BudgetLedger(budget)
    h.log, h.writer, h.prompt = FakeLog(), FakeWriter(), FakePrompt()
    cost, success = CostModel(COST, 0.1), SuccessModel(SUCCESS)
    h.policy = DecisionPolicy(success, cost, h.ledger)
    h.stage1, h.stage2 = FailingStage1(h.ledger), CountingStage2()
    h.solver = TaskPipeline(h.stage1, h.stage2, h.policy, success, h.lam)
    h.planner = TaskPlanner(cost, h.ledger, h.policy.priority)
    h.pending, h.parallel, h.detector, h.skip_severities = {}, 1, None, ["hard"]
    return h


def run(h, prepared):
    async def prep_fake(task):
        return next(p for p in prepared if p.task is task)

    h._prepare = prep_fake
    return {r["task_id"]: r for r in asyncio.run(h.run([p.task for p in prepared]))}


class HarnessEmv(unittest.TestCase):
    def test_free_tokens_rescue_failed_task_by_stage2(self):
        # должно пройти: бюджета хватает на всё (λ = 0), tests_fail → stage2; soft-задача при λ = 0 решается
        h = harness(100_000)
        recs = run(h, [prep("a"), prep("s", soft="unknown_value")])
        self.assertEqual(sorted(h.stage2.tasks), ["a", "s"])
        self.assertEqual((recs["a"]["status"], recs["a"]["solved_by"], recs["a"]["lambda"]), ("verified", "stage2", 0))
        self.assertEqual(recs["a"]["decisions"][0]["choice"], "stage2")

    def test_tight_budget_stops_without_stage2(self):
        # должно быть отклонено: p₂ · reward < λ · c₂ → stop, ни одного вызова stage2, статус unverified
        h = harness(400)  # 5 задач по E = 123: на всё не хватает
        recs = run(h, [prep(f"q{i}") for i in range(5)])
        self.assertEqual(h.stage2.tasks, [])
        first = recs[h.stage1.tasks[0]]
        self.assertEqual((first["status"], first["decisions"][0]["choice"]), ("unverified", "stop"))
        self.assertGreater(first["decisions"][0]["lambda"], 0)

    def test_unlikely_soft_task_skipped_with_zero_tokens(self):
        # должно быть отклонено: soft, p_feasible = 0.1, тесный бюджет → skipped_low_value, 0 токенов
        h = harness(400)
        recs = run(h, [prep(f"q{i}") for i in range(4)] + [prep("s", soft="unknown_value")])
        self.assertEqual(recs["s"]["status"], "skipped_low_value")
        self.assertNotIn("s", h.stage1.tasks)
        self.assertNotIn("cost", recs["s"])


class PassingStage1(FailingStage1):
    async def first(self, prepared, outcome):
        await super().first(prepared, outcome)
        return Verdict(True, "DONE")


class ForcedBranch(unittest.TestCase):
    """specs/coding-ab, пункт в): --force-branch берёт только заданную ветку при любом λ."""

    def solve(self, branch, stage1_cls=FailingStage1):
        ledger = BudgetLedger(100_000)
        success = SuccessModel(SUCCESS)
        policy = DecisionPolicy(success, CostModel(COST, 0.1), ledger)
        stage1, stage2, log = stage1_cls(ledger), CountingStage2(), FakeLog()
        # λ огромна: без режима замера политика выбрала бы stop
        pipe = TaskPipeline(stage1, stage2, policy, success, lambda: 1e9, None, log, branch)
        p = prep("a")
        ledger.reserve("a", 1000)
        outcome = asyncio.run(pipe.solve(p, Outcome("a")))
        return outcome, stage1, stage2

    def test_forced_retry_then_stage2(self):
        outcome, stage1, stage2 = self.solve("retry")
        self.assertEqual([d["choice"] for d in outcome.decisions], ["retry", "stage2"])
        self.assertEqual(outcome.decisions[0]["lambda"], 0)
        self.assertEqual(len(stage1.tasks), 2)
        self.assertEqual((stage2.tasks, outcome.solved_by), (["a"], "stage2"))

    def test_forced_stage2_skips_retry(self):
        outcome, stage1, stage2 = self.solve("stage2")
        self.assertEqual([d["choice"] for d in outcome.decisions], ["stage2"])
        self.assertEqual((len(stage1.tasks), stage2.tasks), (1, ["a"]))

    def test_no_decision_when_stage1_passes(self):
        outcome, _, stage2 = self.solve("retry", PassingStage1)
        self.assertEqual((outcome.decisions, stage2.tasks, outcome.solved_by), ([], [], "stage1"))

    def test_without_mode_huge_lambda_stops(self):
        # должно быть отклонено без режима: та же λ даёт stop
        outcome, _, stage2 = self.solve(None)
        self.assertEqual(([d["choice"] for d in outcome.decisions], stage2.tasks), (["stop"], []))


if __name__ == "__main__":
    unittest.main()
