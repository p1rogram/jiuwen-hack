"""DecisionPolicy и TaskPipeline. Ожидания — из spec (specs/stage2-emv/spec.md, «Ключевой критерий парой»):
λ = 0 при бюджете на всё; ветка по максимуму p·reward − λ·цена; soft-задача пропускается только при тесном бюджете.
Числа цен — из формул CostModel (вход 0 символов: цена = выход + 0.1 · рост контекста)."""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.budget import BudgetLedger  # noqa: E402
from harness.cost_model import CostModel  # noqa: E402
from harness.decision import STOP, DecisionPolicy  # noqa: E402
from harness.feasibility import SOFT, Signal  # noqa: E402
from harness.outcome import Outcome  # noqa: E402
from harness.pipeline import TaskPipeline  # noqa: E402
from harness.prompting import Prepared  # noqa: E402
from harness.success_model import RETRY, STAGE2, SuccessModel  # noqa: E402
from harness.tasks import Task  # noqa: E402
from harness.verifier import TESTS_FAIL, Verdict  # noqa: E402
from tests.test_cost_model import stats  # noqa: E402


def ab(a, b):
    return {"alpha": a, "beta": b}


SUCCESS = {"branches": {"default": {"default": ab(1, 1)}, "stage1": {"default": ab(9, 1)},
                        "retry": {"default": ab(2, 8)}, "stage2": {"default": ab(3, 7)}},
           "p_feasible": {"default": ab(1, 1), "unknown_value": ab(1, 9)}}
# этап 1: 100; повтор: 0.1·100 + 100 = 110; этап 2: base 100, 2 шага по 100 → 0.1·(200 + 100) + 200 = 230,
# high — 3 шага: 0.1·(300 + 300) + 300 = 360
COST = {"stage1": {"retry_prompt_chars": 0, "prior_weight": 5.0, "types": {"default": stats(100)}},
        "stage2": {"types": {"default": {"steps_mean": 2, "steps_high": 3, "step_out_mean": 100,
                                         "step_out_p90": 100, "obs_chars": 0}}}}
E_COST = 100 + 0.1 * 230  # этап 1 + (1 − p₁) · этап 2
VALUE = 0.9 + 0.1 * 0.3  # p₁ + (1 − p₁) · p₂ при reward 1


def policy(budget=10_000):
    ledger = BudgetLedger(budget)
    return DecisionPolicy(SuccessModel(SUCCESS), CostModel(COST, 0.1), ledger), ledger


def prep(task_id, reward=1.0, soft=None):
    signals = [Signal(soft, SOFT, "")] if soft else []
    return Prepared(Task(task_id, "math", "", reward, "exact_int", Path(".")), [{"role": "user", "content": ""}],
                    signals)


TIGHT = [prep(f"q{i}") for i in range(3)]  # E = 123 у каждой, доступно 200: вторая уже не помещается


class Lambda(unittest.TestCase):
    def test_zero_when_budget_covers_all(self):
        pol, _ = policy()
        self.assertEqual(pol.lambda_(TIGHT, 3 * E_COST), 0.0)

    def test_ratio_of_first_task_outside_greedy_knapsack(self):
        pol, _ = policy()
        lam = pol.lambda_(TIGHT + [prep("big", reward=5.0)], 200)
        # «big» (5 · 0.93 / 123) влезает первой, следующая — обычная с выгодой 0.93 / 123
        self.assertAlmostEqual(lam, VALUE / E_COST)


class Branches(unittest.TestCase):
    def test_free_tokens_escalate_tests_fail_to_stage2(self):
        # должно пройти: λ = 0, tests_fail, p₂ = 0.3 > p_retry = 0.2 → stage2, резерв = high этапа 2
        pol, ledger = policy()
        d = pol.branch(prep("a"), TESTS_FAIL, pol.lambda_([], ledger.available), [RETRY, STAGE2])
        self.assertEqual((d.choice, d.budget), (STAGE2, 360))
        self.assertAlmostEqual(ledger.available, 10_000 - 360)

    def test_tight_budget_stops_when_tokens_worth_more(self):
        # должно быть отклонено: p₂ · reward = 0.3 < λ · c₂ = 0.0076 · 230 → stop, резерв не тронут
        pol, ledger = policy()
        lam = pol.lambda_(TIGHT, 200)
        self.assertLess(0.3, lam * 230)
        d = pol.branch(prep("a"), TESTS_FAIL, lam, [RETRY, STAGE2])
        self.assertEqual(d.choice, STOP)
        self.assertEqual(ledger.available, 10_000)
        self.assertTrue(all(b.emv < 0 for b in d.branches))

    def test_branch_that_does_not_fit_falls_back_to_next(self):
        pol, ledger = policy(budget=200)  # high этапа 2 = 360 не помещается, повтор (high 110) — да
        d = pol.branch(prep("a"), TESTS_FAIL, 0.0, [RETRY, STAGE2])
        self.assertEqual(d.choice, RETRY)
        self.assertEqual([(b.name, b.fits) for b in d.branches], [(STAGE2, False), (RETRY, True)])


class SoftSkip(unittest.TestCase):
    def test_soft_task_solved_when_tokens_are_free(self):
        pol, _ = policy()
        self.assertFalse(pol.skip_soft(prep("s", soft="unknown_value"), 0.0))

    def test_unlikely_soft_task_skipped_on_tight_budget(self):
        # должно быть отклонено: p_feasible = 0.1, тесный бюджет → пропуск
        pol, _ = policy()
        lam = pol.lambda_(TIGHT, 200)
        self.assertTrue(pol.skip_soft(prep("s", soft="unknown_value"), lam))
        self.assertFalse(pol.skip_soft(prep("plain"), lam))  # без soft-сигнала политика не пропускает

    def test_priority_accounts_for_p_feasible(self):
        pol, _ = policy()
        self.assertGreater(pol.priority(prep("plain")), pol.priority(prep("s", soft="unknown_value")))


class FakeStage1:
    def __init__(self, verdicts):
        self.verdicts = list(verdicts)

    def can_retry(self, outcome):
        return outcome.attempts < 2

    async def first(self, prepared, outcome):
        outcome.attempts += 1
        return self.verdicts.pop(0)

    async def retry(self, prepared, outcome, failed):
        return await self.first(prepared, outcome)


class FakeStage2:
    def __init__(self):
        self.calls = 0

    async def solve(self, prepared, outcome, failed, budget):
        self.calls += 1
        return Verdict(True, "42")


FAIL = Verdict(False, "", "tests fail", TESTS_FAIL)


class Pipeline(unittest.TestCase):
    def run_pipeline(self, lam):
        pol, _ = policy()
        s2 = FakeStage2()
        pipe = TaskPipeline(FakeStage1([FAIL, FAIL]), s2, pol, SuccessModel(SUCCESS), lambda: lam)
        out = asyncio.run(pipe.solve(prep("a"), Outcome("a")))
        return out, s2

    def test_free_tokens_rescue_by_stage2(self):
        out, s2 = self.run_pipeline(0.0)
        self.assertEqual((out.verified, out.solved_by, out.answer, s2.calls), (True, STAGE2, "42", 1))
        self.assertEqual(out.decisions[0]["choice"], STAGE2)

    def test_tight_budget_stops_without_stage2_or_retry(self):
        pol, _ = policy()
        out, s2 = self.run_pipeline(pol.lambda_(TIGHT, 200))
        self.assertEqual((out.verified, out.attempts, s2.calls), (False, 1, 0))
        self.assertEqual((out.decisions[0]["choice"], out.failure), (STOP, TESTS_FAIL))


if __name__ == "__main__":
    unittest.main()
