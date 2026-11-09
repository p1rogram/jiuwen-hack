"""Этап 1 с ответом-правкой у типов stage1.patch_types. Ожидания — из specs/coding-patch/spec.md (п. 2):
свой системный промпт и повтор у patch-типов, ответ без `# file:` — как скрипт, после вклейки — тот же Verifier."""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.cost_model import CostModel  # noqa: E402
from harness.outcome import Outcome  # noqa: E402
from harness.prompting import Prepared, Stage1Prompt  # noqa: E402
from harness.stage2 import Stage2Solver  # noqa: E402
from harness.settings import load_prompt, load_settings_raw  # noqa: E402
from harness.solver import Stage1Solver  # noqa: E402
from harness.tasks import Task  # noqa: E402
from harness.verifier import SCRIPT_ERROR, TESTS_FAIL, Verdict, Verifier  # noqa: E402
from tests.test_decision import COST  # noqa: E402
from tests.test_prompt_tail import FakeSnapshot  # noqa: E402
from tests.test_stage2 import CFG as S2, FakeGateway  # noqa: E402

S1 = load_settings_raw()["stage1"]
LIB = "def add(a, b):\n    return a - b\n\n\ndef neg(a):\n    return -a\n"
TEST = ("import unittest\nimport mylib\n\n\nclass T(unittest.TestCase):\n"
        "    def test_add(self):\n        self.assertEqual(mylib.add(2, 3), 5)\n\n"
        "    def test_neg(self):\n        self.assertEqual(mylib.neg(2), -2)\n\n\n"
        "if __name__ == '__main__':\n    unittest.main()\n")
PATCH = "```python\n# file: mylib.py\ndef add(a, b):\n    return a + b\n```"
SCRIPT = ("```python\nsrc = open('mylib.py').read()\nopen('mylib.py', 'w').write(src.replace('a - b', 'a + b'))\n"
          "print('DONE')\n```")


def prompt(types=("coding",)) -> Stage1Prompt:
    return Stage1Prompt(FakeSnapshot(), None, S1["tail_prompts"], list(types), S1["patch_prompts"])


class PatchStage1(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)
        (self.ws / "mylib.py").write_bytes(LIB.encode())
        (self.ws / "test_mylib.py").write_bytes(TEST.encode())

    def tearDown(self):
        self.tmp.cleanup()

    def solve(self, replies, task_type="coding", types=("coding",)):
        p = prompt(types)
        task = Task("t", task_type, "Fix mylib.py", 1, "unittest", self.ws)
        prepared = asyncio.run(p.build(task))
        solver = Stage1Solver(FakeGateway(replies), p, Verifier(500), "m", S1)
        outcome = Outcome("t")
        verdict = asyncio.run(solver.first(prepared, outcome))
        if not verdict.ok and solver.can_retry(outcome):
            verdict = asyncio.run(solver.retry(prepared, outcome, verdict))
        return prepared, outcome, verdict

    def test_patch_reply_fixes_tests(self):
        prepared, outcome, verdict = self.solve([PATCH])
        self.assertTrue(verdict.ok, verdict.problem)
        self.assertEqual(verdict.answer, "DONE")
        self.assertEqual(outcome.applied, "patch")
        self.assertEqual(prepared.messages[0]["content"], load_prompt(S1["patch_prompts"]["system"]))

    def test_script_reply_still_works_for_patch_type(self):
        _, outcome, verdict = self.solve([SCRIPT])
        self.assertTrue(verdict.ok, verdict.problem)
        self.assertEqual(outcome.applied, "script")

    def test_rejected_patch_retries_in_patch_format(self):
        bad = "```python\n# file: ../mylib.py\ndef add(a, b):\n    return a + b\n```"
        _, outcome, verdict = self.solve([bad, PATCH])
        self.assertTrue(verdict.ok, verdict.problem)
        retry = outcome.messages[3]["content"]
        self.assertIn("outside the task folder", retry)
        self.assertIn("# file:", retry)

    def test_rejected_patch_is_script_error(self):
        bad = "```python\n# file: mylib.py\ndef add(a, b:\n```"
        _, outcome, verdict = self.solve([bad, bad])
        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.kind, SCRIPT_ERROR)
        self.assertEqual((self.ws / "mylib.py").read_bytes().decode(), LIB)

    def test_other_types_keep_script_protocol(self):
        # должно быть отклонено: у типа вне patch_types `# file:` не вклеивается, промпт прежний
        prepared, outcome, verdict = self.solve([PATCH, PATCH], task_type="data_analysis")
        self.assertEqual(prepared.messages[0]["content"], load_prompt("system.txt"))
        self.assertEqual(outcome.applied, "script")
        self.assertEqual((self.ws / "mylib.py").read_bytes().decode(), LIB)

    def stage2(self, replies, types):
        task = Task("t", "coding", "Fix mylib.py", 1, "unittest", self.ws)
        solver = Stage2Solver(FakeGateway(replies), Verifier(500), CostModel(COST, 0.1), "m", S2, types)
        out = Outcome("t")
        failed = Verdict(False, "DONE", "tests fail", TESTS_FAIL)
        return asyncio.run(solver.solve(Prepared(task, []), out, failed, 10_000)), out

    def test_stage2_applies_patch_blocks_for_patch_type(self):
        # проба 2026-11-10: на этапе 2 модель продолжает формат правки — запуск скриптом файл не менял
        verdict, out = self.stage2([PATCH], ["coding"])
        self.assertTrue(verdict.ok, verdict.problem)
        self.assertEqual(out.stage2_stop, "verified")

    def test_stage2_without_patch_types_runs_script(self):
        verdict, _ = self.stage2([PATCH, PATCH], [])
        self.assertFalse(verdict.ok)
        self.assertEqual((self.ws / "mylib.py").read_bytes().decode(), LIB)

    def test_empty_patch_types_restores_old_coding_prompt(self):
        prepared, outcome, _ = self.solve([PATCH, PATCH], types=())
        self.assertEqual(prepared.messages[0]["content"], load_prompt("system.txt"))
        self.assertEqual(outcome.applied, "script")


class RecordLog:
    def __init__(self):
        self.records = []

    def write(self, record):
        self.records.append(record)


class ShapeEvent(unittest.TestCase):
    """specs/coding-ab, пункт а): событие answer_shape на каждую попытку patch-типа, посчитанное до вклейки."""

    setUp = PatchStage1.setUp
    tearDown = PatchStage1.tearDown

    def run_with_log(self, replies, task_type="coding"):
        p = prompt()
        task = Task("t", task_type, "Fix mylib.py", 1, "unittest", self.ws)
        prepared = asyncio.run(p.build(task))
        log = RecordLog()
        solver = Stage1Solver(FakeGateway(replies), p, Verifier(500), "m", S1, log)
        outcome = Outcome("t")
        verdict = asyncio.run(solver.first(prepared, outcome))
        if not verdict.ok:
            asyncio.run(solver.retry(prepared, outcome, verdict))
        return [r for r in log.records if r["kind"] == "answer_shape"]

    def test_patch_attempt_logged_before_apply(self):
        # add изменена относительно исходника; после вклейки она совпала бы с файлом и стала бы unchanged
        (shape,) = self.run_with_log([PATCH])
        self.assertEqual((shape["stage"], shape["applied"]), ("stage1.1", "patch"))
        self.assertEqual((shape["changed"], shape["unchanged"]), (1, 0))

    def test_each_attempt_logged(self):
        shapes = self.run_with_log(["no code here", PATCH])
        self.assertEqual([(s["stage"], s["applied"]) for s in shapes], [("stage1.1", "none"), ("stage1.2", "patch")])
        self.assertEqual(shapes[0]["blocks"], 0)

    def test_not_logged_for_other_types(self):
        self.assertEqual(self.run_with_log([SCRIPT], task_type="data_analysis"), [])


if __name__ == "__main__":
    unittest.main()
