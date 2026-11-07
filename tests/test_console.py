"""Консоль прогона (specs/console/spec.md). Ожидаемые строки — примеры из критериев spec; поля развилки —
Decision.as_dict из harness/decision.py."""
import asyncio
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.budget import BudgetLedger  # noqa: E402
from harness.calllog import JsonlLog  # noqa: E402
from harness.console import ConsoleView  # noqa: E402
from harness.pipeline import TaskPipeline  # noqa: E402
from harness.settings import load_config  # noqa: E402
from tests.test_decision import prep  # noqa: E402
from tests.test_harness_emv import harness as emv_harness  # noqa: E402

CFG = load_config("console.json")


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class Tty(io.StringIO):
    def isatty(self):
        return True


def view(stream=None, budget=100_000, clock=None):
    stream = stream if stream is not None else io.StringIO()
    return ConsoleView(CFG, BudgetLedger(budget), stream, clock or Clock()), stream


def branch(name, emv, fits=None):
    return {"name": name, "p": 0.5, "cost": 100.0, "high": 300.0, "emv": emv, "fits": fits}


def decision(choice, *branches, failure="tests_fail"):
    return {"kind": "decision", "task_id": "coding_0396", "failure": failure, "lambda": 0.0, "choice": choice,
            "branches": list(branches)}


def lines(stream):
    return stream.getvalue().splitlines()


class DecisionLine(unittest.TestCase):
    def test_chosen_then_best_rejected(self):
        v, s = view()
        v(decision("stage2", branch("stage2", 1.5, True), branch("retry", 1.0)))
        self.assertEqual(lines(s), ["coding_0396: tests_fail → stage2 (EMV 1,5 > retry 1,0)"])

    def test_rejected_that_does_not_fit_is_marked(self):
        v, s = view()
        v(decision("retry", branch("stage2", 1.5, False), branch("retry", 1.0, True)))
        self.assertEqual(lines(s), ["coding_0396: tests_fail → retry (EMV 1,0; stage2 1,5 не влезает)"])

    def test_single_branch(self):
        v, s = view()
        v(decision("stage2", branch("stage2", 1.5, True)))
        self.assertEqual(lines(s), ["coding_0396: tests_fail → stage2 (EMV 1,5, одна ветка)"])

    def test_stop_shows_best_branch(self):
        v, s = view()
        v(decision("stop", branch("stage2", 1.5, False), branch("retry", -0.2)))
        self.assertEqual(lines(s), ["coding_0396: tests_fail → stop (лучшая stage2 EMV 1,5 не влезает)"])


class EventLines(unittest.TestCase):
    def test_rate_limit_line_without_prompt(self):
        v, s = view()
        v({"kind": "rate_limit", "task_id": "search_0122", "model": "m", "wait_sec": 61.0})
        self.assertEqual(lines(s), ["429: пауза 61 с (задача search_0122)"])
        self.assertEqual(v.rate_limits, 1)

    def test_task_line_status_cost_answer_stage2(self):
        v, s = view()
        v({"kind": "task", "task_id": "coding_0007", "status": "verified", "cost": 1255.04, "answer": "DONE",
           "solved_by": "stage2", "stage2_steps": 3, "stage2_stop": "verified"})
        self.assertEqual(lines(s), ["coding_0007: решена, цена 1 255, ответ 'DONE', этап 2: шагов 3, стоп verified"])

    def test_error_task_prints_status_not_error_text(self):
        v, s = view()
        v({"kind": "task", "task_id": "t1", "status": "error", "error": "429 prompt: secret task text", "cost": 0})
        self.assertEqual(lines(s), ["t1: ошибка, цена 0, ответ -"])

    def test_llm_call_prints_nothing(self):
        v, s = view()
        v({"kind": "llm_call", "task_id": "t1", "cost": 10})
        self.assertEqual(s.getvalue(), "")


class Progress(unittest.TestCase):
    def scenario(self, v):
        v({"kind": "run_start", "tasks": 163, "budget": 100_000})
        for i in range(40):
            v({"kind": "task_start", "task_id": f"t{i}", "lambda": 0.0})
        for i in range(37):
            v({"kind": "task", "task_id": f"t{i}", "status": "verified", "cost": 1})
        v({"kind": "rate_limit", "task_id": "t39", "wait_sec": 61})
        v._ledger.charge(21_480)
        v._ledger.reserve("t38", 2_950)

    def test_numbers_match_spec_example(self):
        v, _ = view()
        self.scenario(v)
        self.assertEqual(v.progress_text(),
                         "[ 37/163 | в работе 3 | осталось 123 ]  токены 21 480 / резерв 2 950 / 100 000  λ 0  429: 1")

    def test_plain_stream_has_no_cr_and_progress_every_interval(self):
        clock = Clock()
        v, s = view(clock=clock)
        self.scenario(v)
        for step in range(0, 61, 5):  # 0..60 с, тик каждые 5 с
            clock.t = float(step)
            v.tick()
        out = s.getvalue()
        self.assertNotIn("\r", out)
        progress = [x for x in lines(s) if x.startswith("[")]
        self.assertEqual(len(progress), 3)  # 0, 30, 60 при plain_every_sec = 30

    def test_terminal_live_line_redrawn_not_more_often_than_refresh(self):
        clock = Clock()
        v, s = view(Tty(), clock=clock)
        self.scenario(v)
        v.tick()
        v.tick()  # то же время — не перерисовывается
        self.assertEqual(s.getvalue().count("\r"), 1)
        clock.t += CFG["refresh_sec"]
        v.tick()
        self.assertEqual(s.getvalue().count("\r"), 2)

    def test_event_line_in_terminal_clears_and_restores_live_line(self):
        v, s = view(Tty())
        self.scenario(v)
        v.tick()
        v({"kind": "rate_limit", "task_id": "t1", "wait_sec": 5})
        out = s.getvalue()
        self.assertIn("\r429: пауза 5 с (задача t1)\n", out)
        self.assertTrue(out.endswith("429: 2"))  # живая строка снова внизу, с этим 429
        v.close()
        self.assertTrue(s.getvalue().endswith("\r"))

    def test_summary(self):
        v, s = view()
        v({"kind": "run_start", "tasks": 3, "budget": 1000})
        v({"kind": "decision", "task_id": "a", "choice": "stage2", "lambda": 0, "failure": "tests_fail",
           "branches": [branch("stage2", 1.0, True)]})
        v({"kind": "task", "task_id": "a", "status": "verified", "solved_by": "stage2", "cost": 5})
        v({"kind": "task", "task_id": "b", "status": "verified", "solved_by": "stage1", "cost": 5})
        v({"kind": "task", "task_id": "c", "status": "skipped_infeasible"})
        v._ledger.charge(250)
        v.summary()
        self.assertEqual(lines(s)[-1],
                         "Итог: 3/3 задач — решена 2, невыполнима 1; токены 250 из 1 000; этап 2 спас 1 из 1")


class BrokenStream(unittest.TestCase):
    def test_stream_error_disables_console_after_first_write(self):
        class Broken(io.StringIO):
            writes = 0

            def write(self, text):
                Broken.writes += 1
                raise OSError("closed")

        v, s = view(Broken())
        v({"kind": "rate_limit", "task_id": "t1", "wait_sec": 5})
        v({"kind": "rate_limit", "task_id": "t2", "wait_sec": 5})
        v.tick()
        v.summary()
        self.assertFalse(v.enabled)
        self.assertEqual(Broken.writes, 1)

    def test_malformed_event_disables_console_not_run(self):
        v, s = view()
        v({"kind": "decision", "task_id": "t1", "choice": "stage2", "branches": [{"name": "stage2"}]})  # нет emv
        self.assertFalse(v.enabled)
        v({"kind": "rate_limit", "task_id": "t2", "wait_sec": 5})
        v.summary()
        self.assertEqual(s.getvalue(), "")

    def test_ticker_failure_does_not_replace_run_result(self):
        v, _ = view(Tty())
        v({"kind": "run_start", "tasks": 1, "budget": 10})
        v.progress_text = lambda: 1 / 0

        async def go():
            async with v.live():
                await asyncio.sleep(0.01)
                return "done"

        self.assertEqual(asyncio.run(go()), "done")
        self.assertFalse(v.enabled)

    def test_control_chars_in_task_id_are_replaced(self):
        v, s = view()
        v({"kind": "rate_limit", "task_id": "t\x1b[2K\rx", "wait_sec": 5})
        self.assertEqual(lines(s), ["429: пауза 5 с (задача t?[2K?x)"])

    def test_cp1251_stream_gets_replacement_chars_and_stays_on(self):
        raw = io.BytesIO()
        s = io.TextIOWrapper(raw, encoding="cp1251", errors="strict")
        v, _ = view(s)
        v(decision("stage2", branch("stage2", 1.5, True), branch("retry", 1.0)))
        s.flush()
        self.assertTrue(v.enabled)
        self.assertEqual(raw.getvalue().decode("cp1251").splitlines(),
                         ["coding_0396: tests_fail -> stage2 (EMV 1,5 > retry 1,0)"])


class RaisingStream(io.StringIO):
    def __init__(self, exc):
        super().__init__()
        self.exc, self.writes = exc, 0

    def write(self, text):
        self.writes += 1
        raise self.exc


def run_pair(stream):
    """Прогон поддельного Harness (этапы поддельные, политика и бюджет настоящие); stream None — без консоли.
    Бюджет 1000: часть задач уходит в этап 2, часть останавливается или не допускается."""
    h = emv_harness(1_000)
    h.log = JsonlLog(Path(tempfile.mkdtemp()) / "run.jsonl")
    h.solver = TaskPipeline(h.stage1, h.stage2, h.policy, h.solver._success, h.lam, log=h.log)
    console = None
    if stream is not None:
        console = ConsoleView(CFG, h.ledger, stream, Clock())
        h.log.subscribe(console)

    async def go():
        prepared = [prep(f"q{i}") for i in range(6)] + [prep("s", soft="unknown_value")]

        async def prep_fake(task):
            return next(p for p in prepared if p.task is task)

        h._prepare = prep_fake
        if console is None:
            return await h.run([p.task for p in prepared])
        async with console.live():
            return await h.run([p.task for p in prepared])

    asyncio.run(go())
    if console is not None:
        console.summary()
    recs = [json.loads(x) for x in h.log._path.read_text(encoding="utf-8").splitlines()]
    tasks = {r["task_id"]: {k: v for k, v in r.items() if k not in ("ts", "sec")} for r in recs if r["kind"] == "task"}
    return tasks, h.ledger.spent, console


class KeyPair(unittest.TestCase):
    def test_console_does_not_change_records_or_spent(self):
        # должно пройти: с консолью те же записи задач (статусы, ответы, цены, развилки) и тот же spent
        base, spent, _ = run_pair(None)
        out = Tty()
        with_console, spent_c, console = run_pair(out)
        self.assertGreater(len({r["status"] for r in base.values()}), 1)  # сценарий не вырожден
        self.assertEqual(with_console, base)
        self.assertEqual(spent_c, spent)
        self.assertTrue(console.enabled)
        self.assertEqual(sum(1 for x in out.getvalue().split("\n") if x.split("\r")[-1].startswith(("q", "s"))),
                         len(base) + sum(len(r.get("decisions", [])) for r in base.values()))

    def test_broken_stream_does_not_break_run(self):
        # должно быть отклонено (не сломать прогон): поток бросает на каждой записи — те же записи, консоль выключена
        base, spent, _ = run_pair(None)
        for exc in (OSError("closed"), UnicodeEncodeError("cp1251", "→", 0, 1, "no")):
            stream = RaisingStream(exc)
            recs, spent_b, console = run_pair(stream)
            self.assertEqual(recs, base)
            self.assertEqual(spent_b, spent)
            self.assertFalse(console.enabled)
            self.assertEqual(stream.writes, 1)


if __name__ == "__main__":
    unittest.main()
