"""calibrate_success и вариант emv в simulate_budget. Ожидания — из spec (specs/stage2-emv/spec.md): p веток по
итогам задач в журнале, бета-апостериор = априор + успехи / провалы; этап 2 из журнала в симуляции оплачивается,
только если при текущем λ политика его выбрала бы."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.cost_model import CostModel  # noqa: E402
from harness.success_model import SuccessModel  # noqa: E402
from tests.test_decision import COST, SUCCESS  # noqa: E402
from tools.calibrate_success import branch_outcomes, fit  # noqa: E402
from tools.cost_data import Obs  # noqa: E402
from tools.simulate_budget import make_policy, simulate  # noqa: E402


def dec(failure, choice):
    return {"failure": failure, "choice": choice}


class BranchOutcomes(unittest.TestCase):
    def test_retry_then_stage2_rescue(self):
        rec = {"type": "coding", "status": "verified", "solved_by": "stage2",
               "decisions": [dec("tests_fail", "retry"), dec("tests_fail", "stage2")]}
        self.assertEqual(branch_outcomes(rec), [("stage1", "coding", False), ("retry", "tests_fail", False),
                                                ("stage2", "tests_fail", True)])

    def test_stop_is_not_a_branch_outcome(self):
        rec = {"type": "math", "status": "unverified", "solved_by": "", "decisions": [dec("bad_format", "stop")]}
        self.assertEqual(branch_outcomes(rec), [("stage1", "math", False)])

    def test_old_log_without_solved_by(self):
        rec = {"type": "math", "status": "verified", "attempts": 2}
        self.assertEqual(branch_outcomes(rec), [("stage1", "math", False), ("retry", "", True)])

    def test_forced_stage2_does_not_count_stage1(self):
        rec = {"type": "math", "status": "verified", "solved_by": "stage2", "decisions": [dec("forced", "stage2")]}
        self.assertEqual(branch_outcomes(rec), [("stage2", "", True)])

    def test_skips_and_errors_carry_no_outcome(self):
        self.assertEqual(branch_outcomes({"type": "math", "status": "skipped_budget"}), [])


class Fit(unittest.TestCase):
    def test_posterior_is_prior_plus_counts_and_keeps_priors(self):
        priors = {"branches": {"default": {"default": {"alpha": 1, "beta": 1}},
                               "stage2": {"default": {"alpha": 3, "beta": 7}}},
                  "p_feasible": {"default": {"alpha": 1, "beta": 1}}}
        cfg = fit(priors, {"stage2": {"default": [2, 5]}}, {"unknown_value": [0, 8]})
        self.assertEqual(cfg["branches"]["stage2"]["default"], {"alpha": 5, "beta": 10, "n": 5})
        self.assertEqual(cfg["p_feasible"]["unknown_value"], {"alpha": 1, "beta": 9, "n": 8})
        self.assertEqual(cfg["priors"], priors)  # второй запуск начнёт с тех же априоров


def obs(task_id, cost=100.0, **kw):
    return Obs("run", task_id, "math", 1.0, "verified", True, cost=cost, in1=10, out1=10, attempts=1, **kw)


class SimulateEmv(unittest.TestCase):
    def run_sim(self, budget):
        rescued = obs("a", cost=1000.0, solved_by="stage2", s2_cost=900.0, s2_failure="tests_fail")
        m = CostModel(COST, 0.1)
        return simulate([rescued] + [obs(f"q{i}") for i in range(4)], budget, 1, m,
                        make_policy(m, SuccessModel(SUCCESS)))

    def test_free_tokens_keep_stage2_from_log(self):
        r = self.run_sim(10_000)
        self.assertEqual((r.solved, r.spent), (5, 1400.0))

    def test_tight_budget_drops_stage2_and_its_rescue(self):
        # λ > 0 на старте «a»: p₂ · reward = 0.3 < λ · c₂ → этап 2 не оплачивается, «a» не решена
        r = self.run_sim(300)
        self.assertEqual((r.solved, r.spent, r.overrun), (2, 300.0, False))


if __name__ == "__main__":
    unittest.main()
