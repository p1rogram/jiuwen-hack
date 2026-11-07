"""SuccessModel: поиск ключа от частного к общему, бета-сдвиг по факту, p_feasible по коду soft-сигнала.
Ожидания — из spec (specs/stage2-emv/spec.md) и определения среднего бета-распределения a / (a + b)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.settings import load_config  # noqa: E402
from harness.success_model import RETRY, STAGE1, STAGE2, SuccessModel  # noqa: E402


def ab(a, b):
    return {"alpha": a, "beta": b}


CFG = {"branches": {"default": {"default": ab(1, 1)},
                    "retry": {"default": ab(1, 3), "tests_fail": ab(1, 9), "coding/tests_fail": ab(2, 2)},
                    "stage2": {"default": ab(3, 7)}},
       "p_feasible": {"default": ab(1, 1), "unknown_value": ab(1, 9)}}


class SuccessProbabilities(unittest.TestCase):
    def test_most_specific_key_wins(self):
        m = SuccessModel(CFG)
        self.assertAlmostEqual(m.p(RETRY, "coding", "tests_fail"), 0.5)
        self.assertAlmostEqual(m.p(RETRY, "math", "tests_fail"), 0.1)
        self.assertAlmostEqual(m.p(RETRY, "math", "no_code"), 0.25)

    def test_unknown_branch_falls_back_to_default(self):
        self.assertAlmostEqual(SuccessModel(CFG).p(STAGE1, "math"), 0.5)

    def test_observation_shifts_posterior(self):
        m = SuccessModel(CFG)
        m.observe(STAGE2, "math", "script_error", True)
        self.assertAlmostEqual(m.p(STAGE2, "math", "script_error"), 4 / 11)
        self.assertAlmostEqual(m.p(STAGE2, "math", "tests_fail"), 0.3)  # чужой ключ не сдвинут

    def test_p_feasible_by_soft_code(self):
        m = SuccessModel(CFG)
        self.assertAlmostEqual(m.p_feasible([]), 1.0)
        self.assertAlmostEqual(m.p_feasible(["unknown_value"]), 0.1)
        self.assertAlmostEqual(m.p_feasible(["missing_file", "unknown_value"]), 0.1)
        self.assertAlmostEqual(m.p_feasible(["missing_file"]), 0.5)

    def test_shipped_config_loads(self):
        m = SuccessModel(load_config("success_model.json"))
        self.assertGreater(m.p(STAGE1, "coding"), 0.5)
        self.assertLess(m.p(RETRY, "coding", "tests_fail"), m.p(STAGE2, "coding", "tests_fail"))


if __name__ == "__main__":
    unittest.main()
