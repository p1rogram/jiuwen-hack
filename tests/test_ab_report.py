"""Парный отчёт A/B. Ожидаемые итоги посчитаны вручную по фикстуре ниже (specs/coding-ab, пункт б)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.ab_report import build, merge_run, read_run  # noqa: E402


def make_run(root: Path, answers: dict[str, str], log: list[dict], labels: dict | None = None) -> tuple[Path, Path]:
    """Задачи с проверкой по строке: ответ "ok" — решена."""
    root.mkdir(parents=True)
    for folder, answer in answers.items():
        d = root / folder
        d.mkdir()
        tid = folder.split("_", 1)[1] if folder.startswith("gen3_") else folder
        (d / "task.json").write_text(json.dumps({"task_id": tid, "task_type": "coding", "reward": 1,
                                                 "expected": "ok", "check": "exact"}), encoding="utf-8")
        if answer:
            (d / "answer.txt").write_text(answer, encoding="utf-8")
    if labels:
        (root / "_labels.json").write_text(json.dumps(labels), encoding="utf-8")
    path = root.parent / f"{root.name}.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in log) + "\n", encoding="utf-8")
    return root, path


def task(tid, solved_by="stage1", attempts=1):
    return {"kind": "task", "task_id": tid, "solved_by": solved_by, "attempts": attempts}


def call(tid, stage, cost, out):
    return {"kind": "llm_call", "task_id": tid, "stage": stage, "cost": cost, "output_tokens": out}


class AbReport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        labels = {"c1": {"feasible": True, "variant": "base"}, "c2": {"feasible": True, "variant": "hard"}}
        # база: c1 с первой попытки (100), c2 решена этапом 2 (100 + 300), c3 не решена (50)
        self.a = make_run(root / "a", {"c1": "ok", "c2": "ok", "c3": "bad"}, [
            task("c1"), call("c1", "stage1.1", 100, 80),
            task("c2", "stage2", 1), call("c2", "stage1.1", 100, 90), call("c2", "stage2.1", 300, 200),
            task("c3", ""), call("c3", "stage1.1", 50, 40),
        ], labels)
        # вариант: c1 не решена (регрессия), c2 с первой попытки, c3 решена повтором; c4 есть только здесь
        self.b = make_run(root / "b", {"c1": "bad", "c2": "ok", "c3": "ok", "c4": "ok"}, [
            task("c1", ""), call("c1", "stage1.1", 60, 30),
            task("c2"), call("c2", "stage1.1", 70, 35),
            task("c3", "retry", 2), call("c3", "stage1.1", 40, 20), call("c3", "stage1.2", 40, 20),
            {"kind": "answer_shape", "task_id": "c3", "stage": "stage1.1", "applied": "patch", "before_chars": 10,
             "after_chars": 0, "changed": 1, "unchanged": 1, "defs": 2, "whole_file": True},
            {"kind": "answer_shape", "task_id": "c3", "stage": "stage1.2", "applied": "patch", "before_chars": 99},
            task("c4"), call("c4", "stage1.1", 10, 5),
        ], labels)

    def tearDown(self):
        self.tmp.cleanup()

    def report(self):
        return build({"A": read_run(*self.a, "-"), "B": read_run(*self.b, "-")})

    def test_paired_totals(self):
        rep = self.report()
        self.assertEqual(rep["paired"], 3)
        a, b = rep["arms"]["A"], rep["arms"]["B"]
        self.assertEqual((a["solved"], a["first_try"], a["cost"], a["out11_mean"]), (2, 1, 550.0, 70.0))
        self.assertEqual((b["solved"], b["first_try"], b["cost"]), (2, 1, 210.0))
        self.assertEqual(b["cost_vs_base"], round(210 / 550 - 1, 3))
        self.assertEqual((b["regressions"], b["improvements"]), (["c1"], ["c3"]))

    def test_by_variant_and_shape(self):
        b = self.report()["arms"]["B"]
        self.assertEqual(set(b["by_variant"]), {"base", "hard", "example"})
        self.assertEqual(b["by_variant"]["hard"]["first_try"], 1)
        # форма — только stage1.1
        self.assertEqual(b["shape"]["before_chars"], 10)
        self.assertEqual(b["shape"]["whole_file_share"], 1.0)
        self.assertEqual(b["shape"]["applied"], {"patch": 1})

    def test_prefix_and_override(self):
        root = Path(self.tmp.name)
        later = make_run(root / "c", {"c1": "ok"}, [task("c1"), call("c1", "stage1.1", 5, 5)])
        arm = read_run(*self.b, "-")
        merge_run(arm, read_run(*later, "-"))
        self.assertTrue(arm["c1"]["ok"])
        # перепрогон без _labels.json сохраняет разметку раннего прогона
        self.assertEqual(arm["c1"]["variant"], "base")
        self.assertEqual(set(read_run(*self.a, "gen3_")), {"gen3_c1", "gen3_c2", "gen3_c3"})

    def test_duplicate_task_id_rejected(self):
        root = Path(self.tmp.name)
        dup = make_run(root / "d", {"c1": "ok", "gen3_c1": "ok"}, [task("c1")])
        with self.assertRaises(SystemExit):
            read_run(*dup, "-")


if __name__ == "__main__":
    unittest.main()
