"""Подписчики журнала и события run_start / task_start / decision (specs/console/spec.md, шаг 2):
подписчик видит записи в порядке строк файла, сбой подписчика прогон не останавливает."""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.calllog import JsonlLog  # noqa: E402
from harness.pipeline import TaskPipeline  # noqa: E402
from tests.test_harness_emv import harness  # noqa: E402
from tests.test_harness_budget import tasks  # noqa: E402


def temp_log(subscribers=None) -> JsonlLog:
    return JsonlLog(Path(tempfile.mkdtemp()) / "run.jsonl", subscribers)


def file_records(log: JsonlLog) -> list[dict]:
    return [json.loads(x) for x in log._path.read_text(encoding="utf-8").splitlines()]


class Subscribers(unittest.TestCase):
    def test_subscriber_sees_records_in_file_order(self):
        seen = []
        log = temp_log([seen.append])
        for i in range(5):
            log.write({"kind": "x", "i": i})
        self.assertEqual(seen, file_records(log))

    def test_failing_subscriber_is_dropped_and_writes_go_on(self):
        calls, seen = [], []

        def bad(rec):
            calls.append(rec)
            raise OSError("closed")

        log = temp_log([bad, seen.append])
        log.write({"kind": "a"})
        log.write({"kind": "b"})
        self.assertEqual(len(calls), 1)
        self.assertEqual([r["kind"] for r in seen], ["a", "b"])
        self.assertEqual(len(file_records(log)), 2)


class RunEvents(unittest.TestCase):
    def run_harness(self):
        seen = []
        h = harness(10_000)
        h.log = temp_log([seen.append])
        h.solver = TaskPipeline(h.stage1, h.stage2, h.policy, h.solver._success, h.lam, log=h.log)
        asyncio.run(h.run(tasks(2, "t")))
        return seen

    def test_run_start_first_with_task_count_and_budget(self):
        seen = self.run_harness()
        self.assertEqual(seen[0]["kind"], "run_start")
        self.assertEqual((seen[0]["tasks"], seen[0]["budget"]), (2, 10_000))

    def test_task_start_decision_task_order_per_task(self):
        seen = self.run_harness()
        for tid in ("t0", "t1"):
            kinds = [r["kind"] for r in seen if r.get("task_id") == tid]
            self.assertEqual(kinds[0], "task_start")
            self.assertEqual(kinds[-1], "task")
            self.assertIn("decision", kinds)
            # событие развилки — то же, что в итоге задачи
            task = next(r for r in seen if r.get("task_id") == tid and r["kind"] == "task")
            dec = [{k: v for k, v in r.items() if k not in ("ts", "kind", "task_id")}
                   for r in seen if r.get("task_id") == tid and r["kind"] == "decision"]
            self.assertEqual(dec, task["decisions"])


if __name__ == "__main__":
    unittest.main()
