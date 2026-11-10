"""CrossCheck: ответ этапа 1 меняется только по большинству из трёх решений; сбой, исчерпанный бюджет и малый остаток
оставляют ответ этапа 1. Ожидания — из spec `specs/openjiuwen-agent/spec.md` (B: второе независимое решение,
при расхождении — третий вызов-арбитр; пре-мортем: проверка не должна перебивать верный ответ без большинства)."""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.budget import BudgetLedger, Usage  # noqa: E402
from harness.crosscheck import CrossCheck, normalize  # noqa: E402
from harness.llm import BudgetExhausted, LlmReply  # noqa: E402
from harness.outcome import Outcome  # noqa: E402
from harness.prompting import Prepared  # noqa: E402
from harness.tasks import Task  # noqa: E402
from harness.verifier import Verdict  # noqa: E402

CFG = {"types": ["math", "data_analysis", "search"], "min_available_tokens": 1000}
REPLY = LlmReply("", "", Usage(100, 50, 0), 60.0, 0.1)


class FakeSolver:
    def __init__(self, answer=None, exc=None):
        self.answer, self.exc, self.calls = answer, exc, 0

    async def first(self, prepared, outcome):
        self.calls += 1
        if self.exc:
            raise self.exc
        outcome.add_call("m", "crosscheck2.1", REPLY)
        return Verdict(True, self.answer) if self.answer is not None else Verdict(False, "", "err", "script_error")


class Log:
    def __init__(self):
        self.records = []

    def write(self, rec):
        self.records.append(rec)


def run(second, arbiter, task_type="math", answer="42", verified=True, budget=100_000):
    log = Log()
    cc = CrossCheck(second, arbiter, CFG, BudgetLedger(budget), log)
    prepared = Prepared(Task("t1", task_type, "", 1, "exact_int", Path(".")), [])
    outcome = Outcome("t1", answer=answer, verified=verified, cost=10.0)
    asyncio.run(cc.review(prepared, outcome))
    return outcome, log.records


class CrossCheckTest(unittest.TestCase):
    def test_agreement_keeps_answer_and_pays_one_call(self):
        second, arbiter = FakeSolver("42.0"), FakeSolver("7")
        outcome, log = run(second, arbiter)
        self.assertEqual((outcome.answer, second.calls, arbiter.calls), ("42", 1, 0))
        self.assertEqual(outcome.cost, 70.0)
        self.assertEqual(log[0]["result"], "agree")

    def test_majority_against_first_replaces(self):
        outcome, log = run(FakeSolver("Porto"), FakeSolver(" porto"), "search", "Hamburg")
        self.assertEqual(outcome.answer, "Porto")
        self.assertEqual(log[0]["result"], "replaced")
        self.assertEqual(outcome.cost, 130.0)

    def test_no_majority_keeps_first(self):
        for third in ("Hamburg", "Oslo", None):
            outcome, _ = run(FakeSolver("Porto"), FakeSolver(third), "search", "Hamburg")
            self.assertEqual(outcome.answer, "Hamburg", third)

    def test_failed_second_opinion_keeps_first_without_arbiter(self):
        arbiter = FakeSolver("7")
        outcome, log = run(FakeSolver(None), arbiter)
        self.assertEqual((outcome.answer, arbiter.calls), ("42", 0))
        self.assertEqual(log[0]["result"], "second_failed")

    def test_budget_exhausted_and_errors_keep_first(self):
        for exc in (BudgetExhausted("x"), RuntimeError("boom")):
            outcome, log = run(FakeSolver(exc=exc), FakeSolver("7"))
            self.assertEqual(outcome.answer, "42")
            self.assertIn(log[0]["result"], ("budget_exhausted", "error"))

    def test_skipped_for_coding_unverified_and_low_budget(self):
        for kw in ({"task_type": "coding", "answer": "DONE"}, {"verified": False}, {"budget": 500}):
            second = FakeSolver("1")
            outcome, log = run(second, FakeSolver("1"), **kw)
            self.assertEqual((second.calls, log), (0, []), kw)

    def test_normalize(self):
        self.assertEqual(normalize(" 1,200 "), "1200")
        self.assertEqual(normalize("42.0"), "42")
        self.assertEqual(normalize("Osaka "), "osaka")


if __name__ == "__main__":
    unittest.main()
