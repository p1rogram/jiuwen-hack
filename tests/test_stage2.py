"""Stage2Solver с поддельным шлюзом. Ожидания — из spec (specs/stage2-emv/spec.md): ответ блоком FINAL проверяет
тот же Verifier; стоп по лимиту шагов, по бюджету задачи и по повтору команды или ошибки; вызовы — stage2.<шаг>."""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.budget import Usage  # noqa: E402
from harness.cost_model import CostModel  # noqa: E402
from harness.executor import RunResult  # noqa: E402
from harness.llm import LlmReply  # noqa: E402
from harness.outcome import Outcome  # noqa: E402
from harness.prompting import Prepared  # noqa: E402
from harness.stage2 import Stage2Solver, error_signature  # noqa: E402
from harness.tasks import Task  # noqa: E402
from harness.verifier import SCRIPT_ERROR, Verdict, Verifier  # noqa: E402
from tests.test_decision import COST  # noqa: E402

CFG = {"max_steps": 6, "max_step_tokens": 512, "script_timeout_sec": 30, "obs_tail_chars": 500}
FAILED = Verdict(False, "", "script failed (exit 1):\nKeyError: 'price'", SCRIPT_ERROR)


def code(body):
    return f"```python\n{body}\n```"


class FakeGateway:
    """Отдаёт заготовленные ответы по очереди; каждый вызов стоит cost."""

    def __init__(self, replies, cost=10.0):
        self.replies, self.cost, self.stages, self.messages = list(replies), cost, [], []

    async def ask(self, model, messages, *, task_id, stage, max_tokens=None):
        self.stages.append(stage)
        self.messages.append(list(messages))
        return LlmReply(self.replies.pop(0), "", Usage(100, int(self.cost), 0), self.cost, 0.0)


class Stage2Loop(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def solve(self, replies, check="exact_int", budget=10_000, failed=FAILED):
        gw = FakeGateway(replies)
        solver = Stage2Solver(gw, Verifier(500), CostModel(COST, 0.1), "m", CFG)
        task = Task("t", "math", "", 1, check, self.ws)
        out = Outcome("t", messages=[{"role": "user", "content": "task"}])
        verdict = asyncio.run(solver.solve(Prepared(task, out.messages), out, failed, budget))
        return verdict, out, gw

    def test_script_then_final_answer_is_verified(self):
        v, out, gw = self.solve([code("print(6 * 7)"), "It prints 42.\nFINAL: 42"])
        self.assertEqual((v.ok, v.answer, out.stage2_stop), (True, "42", "verified"))
        self.assertEqual(gw.stages, ["stage2.1", "stage2.2"])
        self.assertEqual(out.cost, 20.0)

    def test_same_error_twice_stops_before_step_limit(self):
        # должно быть отклонено: вторая такая же ошибка → стоп на 2-м шаге из 6
        boom = "raise ValueError('boom')"
        v, out, gw = self.solve([code(boom), code("x = 1\n" + boom), "FINAL: 1"])
        self.assertEqual((v.ok, out.stage2_stop, len(gw.stages)), (False, "repeat_error", 2))

    def test_stage1_error_counts_as_first_occurrence(self):
        v, out, gw = self.solve([code("raise KeyError('price')"), "FINAL: 1"])
        self.assertEqual((out.stage2_stop, len(gw.stages)), ("repeat_error", 1))

    def test_different_errors_do_not_stop(self):
        v, out, gw = self.solve([code("raise ValueError('a')"), code("raise TypeError('b')"), "FINAL: 3"])
        self.assertEqual((v.ok, len(gw.stages)), (True, 3))

    def test_same_command_twice_stops(self):
        v, out, gw = self.solve([code("print('look')"), code("print('look')"), "FINAL: 1"])
        self.assertEqual((out.stage2_stop, len(gw.stages)), ("repeat_command", 2))

    def test_same_print_of_valid_answer_is_accepted(self):
        # журнал run13 (math): модель дважды печатает число вместо FINAL — это ответ по протоколу этапа 1
        v, out, gw = self.solve([code("print(941)"), code("print(941)")])
        self.assertEqual((v.ok, v.answer, out.stage2_stop, len(gw.stages)), (True, "941", "repeat_answer", 2))

    def test_same_file_dump_is_not_an_answer(self):
        # повтор «распечатать файл»: последняя строка формально ответ для exact_str, но это не ответ модели
        dump = code("print('Offices:\\nTokyo\\nOsaka')")
        v, out, gw = self.solve([dump, dump], check="exact_str")
        self.assertEqual((v.ok, out.stage2_stop), (False, "repeat_command"))

    def _buggy_lib(self):
        (self.ws / "mylib.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        (self.ws / "test_mylib.py").write_text(
            "import unittest, mylib\nclass T(unittest.TestCase):\n    def test_f(self):\n"
            "        self.assertEqual(mylib.f(), 2)\nunittest.main()\n", encoding="utf-8")
        failed = asyncio.run(Verifier(500).check(Task("t", "coding", "", 1, "unittest", self.ws),
                                                 RunResult(0, "", "", False, 0.0)))
        self.assertFalse(failed.ok)
        return failed

    def test_reading_files_is_not_a_repeat_of_stage1_test_failure(self):
        # журнал run12 (coding): шаг «прочитать mylib.py» не меняет файлы — тесты падают так же, это не повтор
        failed = self._buggy_lib()
        fix = "open('mylib.py', 'w').write('def f():\\n    return 2\\n')"
        v, out, gw = self.solve([code("print(open('mylib.py').read())"), code(fix)], check="unittest", failed=failed)
        self.assertEqual((v.ok, out.stage2_stop, len(gw.stages)), (True, "verified", 2))

    def test_edit_with_same_test_failure_still_stops(self):
        failed = self._buggy_lib()
        same = "open('mylib.py', 'w').write('def f():\\n    # fixed\\n    return 1\\n')"
        v, out, gw = self.solve([code(same), "FINAL: DONE"], check="unittest", failed=failed)
        self.assertEqual((v.ok, out.stage2_stop, len(gw.stages)), (False, "repeat_error", 1))

    def test_step_limit(self):
        v, out, gw = self.solve([code(f"print({i})") for i in range(7)])
        self.assertEqual((v.ok, out.stage2_stop, len(gw.stages)), (False, "max_steps", 6))

    def test_no_step_beyond_task_budget(self):
        # высокая оценка шага = выход p90 100 + вход: бюджет 50 не пускает ни одного вызова
        v, out, gw = self.solve([code("print(1)")], budget=50)
        self.assertEqual((out.stage2_stop, gw.stages, out.cost), ("budget", [], 0.0))

    def test_wrong_final_gets_feedback_and_retries(self):
        v, out, gw = self.solve(["FINAL: unknown", "FINAL: 7"])
        self.assertEqual((v.ok, v.answer, len(gw.stages)), (True, "7", 2))

    def test_green_tests_finish_without_final(self):
        (self.ws / "mylib.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        (self.ws / "test_mylib.py").write_text(
            "import unittest, mylib\nclass T(unittest.TestCase):\n"
            "    def test_fixed(self): self.assertEqual(mylib.f(), 2)\n", encoding="utf-8")
        fix = "open('mylib.py', 'w').write('def f():\\n    return 2\\n')"
        failed = Verdict(False, "DONE", "`python3 test_mylib.py` still fails", "tests_fail")
        v, out, gw = self.solve([code(fix)], check="unittest", failed=failed)
        self.assertEqual((v.ok, v.answer, out.stage2_stop, len(gw.stages)), (True, "DONE", "verified", 1))


FEEDBACK_CFG = {**CFG, "test_feedback": True}
FINAL_HINT = "If this output is the final answer"


class TestFeedback(unittest.TestCase):
    """specs/test-feedback/spec.md: после правки с красными тестами модель видит их провал, а не пустой вывод правки;
    шаг без правок и задачи не на тесты — прежнее наблюдение."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)
        (self.ws / "mylib.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        (self.ws / "test_mylib.py").write_text(
            "import unittest, mylib\nclass T(unittest.TestCase):\n    def test_f(self):\n"
            "        self.assertEqual(mylib.f(), 4)\nunittest.main()\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def solve(self, replies, check="unittest", cfg=FEEDBACK_CFG):
        gw = FakeGateway(replies)
        verifier = Verifier(500, compact_tests=True)
        solver = Stage2Solver(gw, verifier, CostModel(COST, 0.1), "m", cfg, ["coding"])
        task = Task("t", "coding", "", 1, check, self.ws)
        failed = asyncio.run(verifier.check(task, RunResult(0, "", "", False, 0.0)))
        out = Outcome("t", messages=[{"role": "user", "content": "task"}])
        verdict = asyncio.run(solver.solve(Prepared(task, out.messages), out, failed, 10_000))
        return verdict, gw

    @staticmethod
    def patch(value):
        return code(f"# file: mylib.py\ndef f():\n    return {value}")

    def test_patch_with_failing_tests_shows_the_failure(self):
        v, gw = self.solve([self.patch(3), self.patch(4)])
        feedback = gw.messages[1][-1]["content"]
        self.assertTrue(v.ok)
        self.assertIn("3 != 4", feedback)
        self.assertNotIn(FINAL_HINT, feedback)

    def test_patch_without_flag_keeps_old_observation(self):
        v, gw = self.solve([self.patch(3), self.patch(4)], cfg=CFG)
        self.assertNotIn("3 != 4", gw.messages[1][-1]["content"])
        self.assertIn(FINAL_HINT, gw.messages[1][-1]["content"])

    def test_step_without_changes_keeps_old_observation(self):
        v, gw = self.solve([code("print(open('mylib.py').read())"), self.patch(4)])
        feedback = gw.messages[1][-1]["content"]
        self.assertIn(FINAL_HINT, feedback)
        self.assertNotIn("tests still fail", feedback)

    def test_non_test_task_keeps_old_observation(self):
        v, gw = self.solve([code("print(1)"), "FINAL: 7"], check="exact_int")
        self.assertIn(FINAL_HINT, gw.messages[1][-1]["content"])

    def test_test_run_printed_by_script_is_compacted(self):
        run_tests = ("import subprocess, sys\n"
                     "r = subprocess.run([sys.executable, 'test_mylib.py'], capture_output=True, text=True)\n"
                     "print(r.stderr)")
        v, gw = self.solve([code(run_tests), self.patch(4)])
        feedback = gw.messages[1][-1]["content"]
        self.assertIn("AssertionError: 1 != 4", feedback)
        self.assertNotIn("Traceback", feedback)


class ErrorSignature(unittest.TestCase):
    """«Та же ошибка» — та же строка исключения (комментарий EXC_RE), в сырой и в сжатой форме вывода."""

    def test_compact_failure_signature_ignores_line_numbers(self):
        a = "FAIL: test_f\n  test_mylib.py:4 in test_f: self.assertEqual(mylib.f(), 4)\n  AssertionError: 3 != 4\n"
        b = "FAIL: test_f\n  test_mylib.py:9 in test_f: self.assertEqual(mylib.f(), 4)\n  AssertionError: 3 != 4\n"
        c = "FAIL: test_f\n  test_mylib.py:4 in test_f: self.assertEqual(mylib.f(), 4)\n  AssertionError: 2 != 4\n"
        self.assertEqual(error_signature(a), error_signature(b))
        self.assertEqual(error_signature(a), "AssertionError: 3 != 4")
        self.assertNotEqual(error_signature(a), error_signature(c))

    def test_indented_source_line_is_not_an_exception(self):
        text = 'Traceback (most recent call last):\n  File "m.py", line 3, in f\n    raise ValueError("bad")\n'
        self.assertIn("File", error_signature(text))


if __name__ == "__main__":
    unittest.main()
