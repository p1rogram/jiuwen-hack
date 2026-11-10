"""tools.suite score: вердикты по эталону набора (метки gen, ожидания вариантов, поле expected примеров), сумма токенов
по логу вызовов и сводка Markdown. Ожидания — из описания вердиктов в tools/suite_report.py и правила сравнения
организаторов (strip + lower, встреча 10.11)."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.suite import score_run, write_summary  # noqa: E402

# id -> (выполнима, эталон, ответ харнесса или None, статус в логе)
CASES = {
    "s_ok": (True, "Bergen", "bergen", "verified"),
    "s_wrong": (True, "1969", "1960", "verified"),
    "s_skip_ok": (False, None, None, "skipped_infeasible"),
    "s_answered_inf": (False, None, "1799", "verified"),
    "s_false_skip": (True, "Perth", None, "skipped_infeasible"),
}
EXPECTED_VERDICTS = {"s_ok": "OK", "s_wrong": "WRONG", "s_skip_ok": "SKIP-OK",
                     "s_answered_inf": "ANSWERED-INFEASIBLE", "s_false_skip": "FALSE-SKIP"}


def write_run(run: Path, truth: str) -> None:
    tasks = run / "tasks"
    log = []
    labels, expected = {}, {}
    for i, (tid, (feasible, exp, answer, status)) in enumerate(CASES.items()):
        d = tasks / tid
        d.mkdir(parents=True)
        data = {"task_id": tid, "task_type": "search", "reward": 2, "prompt": "q"}
        if truth == "examples":
            data["expected"] = exp
        (d / "task.json").write_text(json.dumps(data), encoding="utf-8")
        if answer is not None:
            (d / "answer.txt").write_text(answer + "\n", encoding="utf-8")
        labels[tid] = {"feasible": feasible, "expected": exp}
        expected[tid] = {"feasible": feasible, "expected": exp, "type": "search"}
        log.append({"kind": "task", "task_id": tid, "status": status})
        log.append({"kind": "llm_call", "task_id": tid, "cost": 10.0 + i, "input_tokens": 50, "output_tokens": 5})
    if truth == "labels":
        (tasks / "_labels.json").write_text(json.dumps(labels), encoding="utf-8")
    elif truth == "variants":
        (run / "tasks-expected.json").write_text(json.dumps(expected), encoding="utf-8")
    (run / "out" / "logs").mkdir(parents=True)
    (run / "out" / "logs" / "h.jsonl").write_text("\n".join(json.dumps(r) for r in log), encoding="utf-8")


class SuiteScoreTest(unittest.TestCase):
    def test_verdicts_for_each_truth_source(self):
        for truth in ("labels", "variants"):
            with self.subTest(truth=truth), tempfile.TemporaryDirectory() as tmp:
                run = Path(tmp) / "set"
                write_run(run, truth)
                row = score_run(run)
                self.assertEqual(row["truth"], truth)
                self.assertEqual({r["task_id"]: r["verdict"] for r in row["rows"]}, EXPECTED_VERDICTS)
                self.assertEqual((row["solved"], row["feasible"], row["tasks"]), (1, 3, 5))
                self.assertEqual((row["reward"], row["reward_max"]), (2.0, 6.0))
                self.assertEqual(row["cost"], 60.0)  # 10+11+12+13+14
                self.assertEqual(row["cost_on_infeasible"], 25.0)  # s_skip_ok 12 + s_answered_inf 13
                self.assertEqual((row["input_tokens"], row["output_tokens"], row["llm_calls"]), (250, 25, 5))

    def test_examples_without_labels_are_all_feasible(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "examples"
            write_run(run, "examples")
            row = score_run(run)
            self.assertEqual(row["truth"], "examples")
            self.assertEqual(row["feasible"], 5)
            verdicts = {r["task_id"]: r["verdict"] for r in row["rows"]}
            self.assertEqual(verdicts["s_ok"], "OK")
            self.assertEqual(verdicts["s_false_skip"], "FALSE-SKIP")

    def test_summary_files_and_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            write_run(out / "a", "labels")
            write_run(out / "b", "variants")
            with contextlib.redirect_stdout(io.StringIO()):
                summary = write_summary(out)
            self.assertEqual(summary["total"]["solved"], 2)
            self.assertEqual(summary["total"]["feasible"], 6)
            saved = json.loads((out / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual([r["run"] for r in saved["runs"]], ["a", "b"])
            md = (out / "summary.md").read_text(encoding="utf-8")
            self.assertIn("решено **2/6**", md)
            self.assertIn("`s_false_skip` FALSE-SKIP", md)
            self.assertNotIn("`s_ok`", md)


if __name__ == "__main__":
    unittest.main()
