"""Набор вариантов (spec `specs/openjiuwen-agent/spec.md`, критерий B): ≥ 60 задач 4 категорий, у coding видимые тесты
падают до правки и проходят с эталоном, скрытые ловят баг; эталоны data/search видны в файлах задачи; оценка
различает верный, неверный ответ и ответ на невыполнимую."""
import csv
import io
import json
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.gen.content.variants_pool import DATA_INFEASIBLE  # noqa: E402
from tools.gen.score_variants import score  # noqa: E402
from tools.gen.variants import build, write  # noqa: E402

TASKS = build(1)


def run_tests(files: dict[str, str], hidden: str | None = None) -> tuple[bool, bool | None]:
    with tempfile.TemporaryDirectory() as tmp:
        for name, text in files.items():
            Path(tmp, name).write_text(text, encoding="utf-8")

        def ok(name):
            return subprocess.run([sys.executable, name], cwd=tmp, capture_output=True, timeout=60).returncode == 0

        visible = all(ok(n) for n in files if n.startswith("test_"))
        if hidden is None:
            return visible, None
        Path(tmp, "test_hidden.py").write_text(hidden, encoding="utf-8")
        return visible, ok("test_hidden.py")


class VariantSet(unittest.TestCase):
    def test_size_and_categories(self):
        self.assertGreaterEqual(len(TASKS), 60)
        feasible = Counter(t.task_type for t in TASKS if t.feasible)
        self.assertEqual(set(feasible), {"math", "data_analysis", "search", "coding"})
        self.assertTrue(all(n >= 14 for n in feasible.values()), feasible)
        self.assertEqual(len({t.task_id for t in TASKS}), len(TASKS))
        self.assertEqual([t.instruction for t in build(1)], [t.instruction for t in TASKS])

    def test_coding_fails_before_fix_and_passes_with_reference(self):
        for t in (t for t in TASKS if t.task_type == "coding" and t.feasible):
            visible, hidden = run_tests(t.files, t.hidden)
            self.assertFalse(visible, t.task_id)
            self.assertFalse(hidden, t.task_id)
            visible, hidden = run_tests({**t.files, **t.reference}, t.hidden)
            self.assertTrue(visible and hidden, t.task_id)

    def test_expected_is_grounded_in_task_files(self):
        for t in TASKS:
            if not t.feasible or t.task_type == "coding":
                continue
            if t.check == "exact_int":
                int(t.expected)
            else:  # имя, город, отдел — должно встречаться в файлах задачи
                self.assertTrue(any(t.expected in body for body in t.files.values()), t.task_id)

    def test_infeasible_data_declares_missing_column(self):
        for t in TASKS:
            if t.task_type != "data_analysis" or t.feasible:
                continue
            (name, body), = t.files.items()
            header = next(csv.reader(io.StringIO(body)))
            spec = next(s for s in DATA_INFEASIBLE if name == f"{s['table']}.csv")
            self.assertIn("," + spec["declared"], t.instruction)
            self.assertNotIn(spec["declared"], header)


class Scoring(unittest.TestCase):
    def test_verdicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "var"
            write(root, TASKS)
            math_ok, math_wrong = [t for t in TASKS if t.task_type == "math" and t.feasible][:2]
            infeasible = next(t for t in TASKS if t.task_type == "search" and not t.feasible)
            code = next(t for t in TASKS if t.task_type == "coding" and t.feasible)
            (root / math_ok.task_id / "answer.txt").write_text(f" {math_ok.expected}\n", encoding="utf-8")
            (root / math_wrong.task_id / "answer.txt").write_text("-1", encoding="utf-8")
            (root / infeasible.task_id / "answer.txt").write_text("1799", encoding="utf-8")
            for name, body in code.reference.items():
                (root / code.task_id / "workspace" / name).write_text(body, encoding="utf-8")
            log = Path(tmp) / "run.jsonl"
            log.write_text(json.dumps({"kind": "task", "task_id": math_ok.task_id, "status": "verified",
                                       "cost": 12.5}) + "\n", encoding="utf-8")
            res = score(root, log)
            verdicts = {k: v["verdict"] for k, v in res["tasks"].items()}
            self.assertEqual(verdicts[math_ok.task_id], "OK")
            self.assertEqual(verdicts[math_wrong.task_id], "WRONG")
            self.assertEqual(verdicts[infeasible.task_id], "ANSWERED-INFEASIBLE")
            self.assertEqual(verdicts[code.task_id], "OK")
            self.assertEqual(res["total"]["ok"], 2)
            self.assertEqual(res["total"]["cost"], 12.5)


if __name__ == "__main__":
    unittest.main()
