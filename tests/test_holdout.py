"""Состав, эталоны, воспроизводимость и утечки независимого набора."""
import asyncio
import json
import tempfile
import unittest
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from harness.settings import load_settings_raw
from harness.snapshot import WorkspaceSnapshot
from harness.tasks import load_tasks

from tools.gen.holdout import build, inventory, write
from tools.gen.holdout_oracles import answer
from tools.gen.holdout_validate import validate
from tools.gen.score_variants import verdict


class HoldoutTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "holdout"

    def test_composition_and_independent_oracles(self):
        cases = build()
        self.assertEqual(len(cases), 40)
        self.assertEqual(Counter((c.category, c.feasible) for c in cases),
                         Counter({(t, f): 8 if f else 2 for t in
                                  ("math", "data_analysis", "search", "coding") for f in (True, False)}))
        families = Counter((c.category, c.family) for c in cases if c.feasible)
        self.assertEqual(len(families), 16)
        self.assertEqual(set(families.values()), {2})
        for case in cases:
            self.assertTrue(case.evidence)
            if case.feasible and case.category != "coding":
                self.assertEqual(answer(case), case.expected)

    def test_reproducible_and_no_leaks(self):
        write(self.root)
        other = Path(self.tmp.name) / "second"
        write(other)
        self.assertEqual(inventory(self.root), inventory(other))
        self.assertEqual((self.root / "manifest.json").read_bytes(), (other / "manifest.json").read_bytes())
        for path in (self.root / "tasks").rglob("*"):
            if path.is_file():
                self.assertNotIn(path.name, {"manifest.json", "test_hidden.py", "tasks-expected.json"})
                self.assertNotIn("references", path.parts)
                self.assertNotIn("partial", path.parts)

    def test_concurrent_generation_rejects_second_writer(self):
        def attempt(_):
            try:
                write(self.root)
                return "ok"
            except FileExistsError:
                return "exists"
        with ThreadPoolExecutor(2) as pool:
            self.assertCountEqual(pool.map(attempt, range(2)), ["ok", "exists"])
        before = inventory(self.root)
        with self.assertRaises(FileExistsError):
            write(self.root)
        self.assertEqual(before, inventory(self.root))

    def test_all_coding_references_originals_and_partials(self):
        write(self.root)
        self.assertEqual(validate(self.root), dict(version="holdout-v1", tasks=40, feasible=32,
                                                    infeasible=8, coding_references=8))

    def test_corruption_or_incomplete_generation_is_rejected(self):
        write(self.root)
        (self.root / "tasks-expected.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "hashes"):
            validate(self.root)

    def test_scorer_requires_hidden_for_holdout(self):
        write(self.root)
        expected = json.loads((self.root / "tasks-expected.json").read_text(encoding="utf-8"))
        task_id, exp = next((k, v) for k, v in expected.items() if v["type"] == "coding" and v["feasible"])
        solution = self.root / "tasks" / task_id / "workspace" / "solution.py"
        solution.write_bytes((self.root / "references" / task_id / "solution.py").read_bytes())
        (self.root / "tasks-hidden" / task_id / "test_hidden.py").unlink()
        self.assertEqual(verdict(self.root / "tasks", task_id, exp)[0], "VISIBLE-ONLY")

    def test_snapshot_cannot_see_private_artifacts(self):
        write(self.root)
        task = next(t for t in load_tasks(self.root / "tasks") if t.task_type == "coding")
        snapshot = asyncio.run(WorkspaceSnapshot(load_settings_raw()["snapshot"]).build(task))
        self.assertIn("solution.py", snapshot)
        for private in ["test_hidden.py", "manifest.json", "tasks-expected.json", "'incomplete'"]:
            self.assertNotIn(private, snapshot)

    def test_hidden_rejects_input_mutation(self):
        write(self.root)
        expected = json.loads((self.root / "tasks-expected.json").read_text(encoding="utf-8"))
        task_id, exp = next((k, v) for k, v in expected.items() if v["family_id"] == "merge_intervals")
        source = (self.root / "references" / task_id / "solution.py").read_text(encoding="utf-8")
        source = source.replace("    result = []", "    intervals.sort()\n    result = []")
        (self.root / "tasks" / task_id / "workspace" / "solution.py").write_text(source, encoding="utf-8")
        self.assertEqual(verdict(self.root / "tasks", task_id, exp)[0], "VISIBLE-ONLY")
