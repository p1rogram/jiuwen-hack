"""Потолок выхода этапа 1 по типу задачи и остановка при обрыве.

Ожидания — из specs/optimization-research/findings.md (идея 2): у выполнимых math ответ не длиннее 189 токенов,
у невыполнимых 363–2 048 (медиана 487); у data невыполнимые короткие (медиана 84) — там потолок не экономит; ответ, упёршийся в потолок, — провал `truncated`: скрипт не исполняется, ни повтора,
ни этапа 2, ни answer.txt. Модели перекрёстной проверки потолок не ограничивает.
"""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.budget import Usage  # noqa: E402
from harness.llm import LlmReply  # noqa: E402
from harness.outcome import Outcome  # noqa: E402
from harness.prompting import Prepared, Stage1Prompt  # noqa: E402
from harness.settings import load_settings_raw  # noqa: E402
from harness.solver import Stage1Solver  # noqa: E402
from harness.tasks import Task  # noqa: E402
from harness.verifier import TRUNCATED, Verdict, Verifier  # noqa: E402
from tests.test_decision import prep  # noqa: E402
from tests.test_harness_emv import harness, run  # noqa: E402
from tests.test_prompt_tail import FakeSnapshot  # noqa: E402
from tests.test_unsolvable import RecordingWriter  # noqa: E402

S1 = load_settings_raw()["stage1"]
SCRIPT = "```python\nopen('ran.txt', 'w').write('1')\nprint(42)\n```"


class CapGateway:
    def __init__(self, out_tokens):
        self.out, self.max_tokens = out_tokens, []

    async def ask(self, model, messages, *, task_id, stage, max_tokens=None):
        self.max_tokens.append(max_tokens)
        return LlmReply(SCRIPT, "", Usage(100, self.out, 0), self.out + 10.0, 0.0)


class Stage1Cap(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def solve(self, out_tokens, task_type="math", stage="stage1"):
        gw = CapGateway(out_tokens)
        p = Stage1Prompt(FakeSnapshot(), None, S1["tail_prompts"], S1["patch_types"], S1["patch_prompts"])
        task = Task("t", task_type, "How many?", 1, "exact_int", self.ws)
        prepared = asyncio.run(p.build(task))
        solver = Stage1Solver(gw, p, Verifier(500), "m", S1, stage=stage)
        return gw, asyncio.run(solver.first(prepared, Outcome("t")))

    def test_reply_at_cap_is_truncated_and_not_run(self):
        cap = S1["max_output_tokens"]["math"]
        gw, verdict = self.solve(cap)
        self.assertEqual((verdict.ok, verdict.kind, verdict.answer), (False, TRUNCATED, ""))
        self.assertEqual(gw.max_tokens, [cap])
        self.assertFalse((self.ws / "ran.txt").exists())

    def test_reply_below_cap_runs_as_before(self):
        # должно пройти: ответ короче потолка — обычный путь, скрипт исполняется
        gw, verdict = self.solve(S1["max_output_tokens"]["math"] - 1)
        self.assertEqual((verdict.ok, verdict.answer), (True, "42"))
        self.assertTrue((self.ws / "ran.txt").exists())

    def test_cap_differs_by_type(self):
        caps = S1["max_output_tokens"]
        # math: выполнимые ≤ 189, невыполнимые — медиана 487; у остальных типов потолок — только страховка
        self.assertLessEqual(caps["math"], 400)
        self.assertGreaterEqual(min(caps["search"], caps["data_analysis"], caps["coding"]), 1024)
        gw, _ = self.solve(10, task_type="search")
        self.assertEqual(gw.max_tokens, [caps["search"]])

    def test_crosscheck_models_are_not_capped(self):
        gw, verdict = self.solve(5000, stage="crosscheck2")
        self.assertEqual(gw.max_tokens, [None])
        self.assertTrue(verdict.ok)


class TruncatingStage1:
    def __init__(self, ledger):
        self.ledger, self.calls = ledger, 0

    def can_retry(self, outcome):
        return outcome.attempts < 2

    async def first(self, prepared, outcome):
        self.calls += 1
        self.ledger.charge(330, prepared.task.task_id)
        outcome.cost += 330
        outcome.attempts += 1
        outcome.calls.append({"stage": f"stage1.{outcome.attempts}", "in": 0, "out": 320})
        return Verdict(False, "", "reply hit the 320-token output cap", TRUNCATED)

    async def retry(self, prepared, outcome, failed):
        return await self.first(prepared, outcome)


class TruncationStopsTask(unittest.TestCase):
    def test_truncated_has_no_retry_no_stage2_no_answer(self):
        # при λ = 0 обычный провал ушёл бы в повтор или этап 2 (test_harness_emv), обрыв по потолку — нет
        h = harness(100_000)
        h.stage1 = TruncatingStage1(h.ledger)
        h.solver._s1 = h.stage1
        h.writer = RecordingWriter()
        rec = run(h, [prep("a")])["a"]
        self.assertEqual((rec["status"], rec["failure"]), ("unverified", TRUNCATED))
        self.assertEqual((h.stage1.calls, h.stage2.tasks, rec["decisions"]), (1, [], []))
        self.assertEqual((h.writer.recorded, h.writer.discarded), ([], ["a"]))


if __name__ == "__main__":
    unittest.main()
