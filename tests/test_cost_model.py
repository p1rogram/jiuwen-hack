"""Модель расхода и допуск задач под бюджет. Ожидания — из spec (specs/cost-model/spec.md): формула оценки,
пара «допустить / отклонить» по резерву; числа калибровки — из определения (отношение сумм, сглаживание +0.5/+1)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.budget import BudgetLedger  # noqa: E402
from harness.cost_model import CostModel  # noqa: E402
from harness.planner import TaskPlanner  # noqa: E402
from harness.prompting import Prepared  # noqa: E402
from harness.settings import load_config  # noqa: E402
from harness.tasks import Task  # noqa: E402
from tools.calibrate_cost import fit  # noqa: E402
from tools.cost_data import Obs  # noqa: E402
from tools.simulate_budget import simulate  # noqa: E402


def stats(out_mean, out_p90=None, cpt=4.0, p_retry=0.0):
    return {"chars_per_token": cpt, "out_mean": out_mean, "out_p90": out_p90 or out_mean, "p_retry": p_retry}


def model(types, prior=5.0, retry_chars=0):
    return CostModel({"stage1": {"retry_prompt_chars": retry_chars, "prior_weight": prior,
                                 "types": {"default": stats(100), **types}}}, input_weight=0.1)


def prepared(task_id, task_type, reward, chars=0):
    return Prepared(Task(task_id, task_type, "", reward, "", Path(".")), [{"role": "user", "content": "x" * chars}])


class Estimate(unittest.TestCase):
    def test_input_from_chars_plus_output(self):
        # 4000 символов / 4 = 1000 входных = 100 выходных-эквивалентов, + выход 200
        e = model({"math": stats(200, 300)}).estimate("math", 4000)
        self.assertAlmostEqual(e.mean, 300.0)
        self.assertAlmostEqual(e.high, 400.0)

    def test_retry_adds_expected_second_call(self):
        # второй вызов: вход 1000 + выход 200 + 400 символов шаблона (=100) → 130, выход ещё 200 → 330 × 0.5
        e = model({"math": stats(200, cpt=4.0, p_retry=0.5)}, retry_chars=400).estimate("math", 4000)
        self.assertAlmostEqual(e.mean, 300.0 + 0.5 * 330.0)

    def test_unknown_type_uses_default(self):
        self.assertAlmostEqual(model({}).estimate("translation", 0).mean, 100.0)

    def test_observed_output_moves_estimate(self):
        m = model({"coding": stats(400)}, prior=5.0)
        for _ in range(5):
            m.observe("coding", 4000, 1000, 800)
        # (5×400 + 5×800) / 10 = 600 выхода; вход как предсказан → множитель 1
        self.assertAlmostEqual(m.estimate("coding", 4000).mean, 100.0 + 600.0)

    def test_observed_input_ratio_moves_input(self):
        m = model({"search": stats(0)}, prior=1.0)
        m.observe("search", 4000, 3000, 0)  # факт втрое больше предсказанных 1000
        # множитель (1 + 3) / (1 + 1) = 2 → вход 2000 токенов = 200
        self.assertAlmostEqual(m.estimate("search", 4000).mean, 200.0)

    def test_shipped_config_loads(self):
        m = CostModel(load_config("cost_model.json"), 0.1)
        for t in ("coding", "data_analysis", "math", "search", "unknown"):
            e = m.estimate(t, 3000)
            self.assertGreater(e.high, e.mean - 1e-9)
            self.assertGreater(e.mean, 0)


class Admission(unittest.TestCase):
    TYPES = {"big": stats(600), "small": stats(300)}

    def planner(self, budget):
        return TaskPlanner(model(self.TYPES), BudgetLedger(budget))

    def test_both_fit_when_sum_of_high_fits(self):
        pl = self.planner(1000)
        a, b = prepared("A", "big", 10), prepared("B", "small", 10)
        self.assertIsNotNone(pl.admit(a))
        self.assertIsNotNone(pl.admit(b))

    def test_only_better_one_when_tight(self):
        pl = self.planner(800)
        a, b = prepared("A", "big", 10), prepared("B", "small", 10)
        first, second = pl.order([a, b])
        self.assertEqual(first.task.task_id, "B")
        self.assertIsNotNone(pl.admit(first))
        self.assertIsNone(pl.admit(second))  # 800 − 300 в резерве < 600

    def test_reserved_budget_blocks_start(self):
        ledger = BudgetLedger(1000)
        pl = TaskPlanner(model(self.TYPES), ledger)
        self.assertTrue(ledger.reserve("other", 500))
        self.assertGreaterEqual(ledger.remaining, 600)  # без учёта резерва задача бы стартовала
        self.assertIsNone(pl.admit(prepared("A", "big", 10)))

    def test_charges_consume_reservation_and_release_frees_it(self):
        ledger = BudgetLedger(1000)
        ledger.reserve("A", 600)
        ledger.charge(250, "A")
        self.assertAlmostEqual(ledger.available, 1000 - 250 - 350)
        ledger.release("A")
        self.assertAlmostEqual(ledger.available, 750)


def obs(tid, ttype, chars, in1, out1, attempts=1, cost=None, reward=1.0, sec=1.0):
    return Obs("run", tid, ttype, reward, "verified", True, cost if cost is not None else out1 + in1 * 0.1,
               chars, in1, out1, attempts, sec)


class Calibration(unittest.TestCase):
    def test_ratio_of_sums_and_smoothed_retry(self):
        cfg = fit([obs("a", "math", 1000, 250, 100), obs("b", "math", 3000, 750, 300, attempts=2)], 5.0)
        t = cfg["stage1"]["types"]["math"]
        self.assertAlmostEqual(t["chars_per_token"], 4.0)
        self.assertAlmostEqual(t["out_mean"], 200.0)
        self.assertAlmostEqual(t["p_retry"], (1 + 0.5) / (2 + 1), places=4)
        self.assertIn("default", cfg["stage1"]["types"])

    def test_stage2_by_type_when_enough_tasks(self):
        # run17/run19: цена шага этапа 2 у search в ~10 раз выше data_analysis — общий default её смазывает
        def s2(tid, ttype, outs):
            return Obs("run", tid, ttype, 1.0, "verified", True, 100.0, 1000, 250, 100, 1, 1.0,
                       s2_outs=outs, s2_obs_chars=(300,) * len(outs))
        runs = [s2(f"s{i}", "search", (500, 500, 500)) for i in range(5)] + \
               [s2(f"d{i}", "data_analysis", (100,)) for i in range(3)]
        st = fit(runs, 5.0)["stage2"]["types"]
        self.assertEqual((st["search"]["steps_mean"], st["search"]["step_out_mean"]), (3.0, 500.0))
        self.assertNotIn("data_analysis", st)  # 3 задачи — мало для своего p90
        self.assertEqual(st["default"]["n"], 8)


class Simulation(unittest.TestCase):
    def test_reservation_prevents_overrun_that_constants_cause(self):
        # три одновременные задачи по 600 при бюджете 1000; старая константа 300 пускает все три —
        # вторая завершённая переходит бюджет, запуск обрывается
        data = [obs(f"s{i}", "search", 4000, 1000, 500, cost=600.0) for i in range(3)]
        cfg = fit(data, 5.0)
        old = simulate(data, 1000, 5, None)
        new = simulate(data, 1000, 5, CostModel(cfg, 0.1))
        self.assertTrue(old.overrun)
        self.assertFalse(new.overrun)
        self.assertGreaterEqual(new.reward, old.reward)
        self.assertLessEqual(new.spent, 1000)


if __name__ == "__main__":
    unittest.main()
