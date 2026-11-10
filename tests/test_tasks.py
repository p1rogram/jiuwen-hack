"""Загрузка задач: битый task.json не срывает загрузку, reward без поля — 1.0. Ожидания — из spec
`specs/robustness/spec.md` (шаг 2)."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.tasks import load_tasks  # noqa: E402


class LoadTasks(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def put(self, name: str, content: bytes) -> None:
        (self.root / name).mkdir()
        (self.root / name / "task.json").write_bytes(content)

    def put_json(self, name: str, data) -> None:
        self.put(name, json.dumps(data).encode())

    def load(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            tasks = {t.task_id: t for t in load_tasks(self.root)}
        return tasks, err.getvalue()

    def test_broken_task_json_is_skipped(self):
        self.put_json("ok", {"task_id": "ok", "task_type": "math", "reward": 2})
        self.put("bad_json", b"{not json")
        self.put("bad_utf8", b'{"task_id": "\xff"}')
        self.put_json("not_object", [1, 2])
        tasks, err = self.load()
        self.assertEqual(list(tasks), ["ok"])
        for name in ("bad_json", "bad_utf8", "not_object"):
            self.assertIn(name, err)

    def test_missing_or_invalid_reward_defaults_to_one(self):
        self.put_json("none", {"task_id": "none"})
        self.put_json("null", {"task_id": "null", "reward": None})
        self.put_json("text", {"task_id": "text", "reward": "abc"})
        tasks, _ = self.load()
        for name in ("none", "null", "text"):
            self.assertEqual(tasks[name].reward, 1.0, name)

    def test_explicit_zero_reward_is_kept(self):
        # должно быть отклонено: явный 0 — значение задачи, подменять его умолчанием нельзя
        self.put_json("zero", {"task_id": "zero", "reward": 0})
        self.put_json("three", {"task_id": "three", "reward": 3})
        tasks, _ = self.load()
        self.assertEqual(tasks["zero"].reward, 0.0)
        self.assertEqual(tasks["three"].reward, 3.0)

    def test_task_json_with_bom_is_loaded(self):
        # аудит edc1c14, A7: файл из Windows-редактора с BOM — обычная задача, а не битый JSON
        self.put("bom", "﻿".encode() + json.dumps({"task_id": "bom", "task_type": "math"}).encode())
        tasks, err = self.load()
        self.assertEqual(list(tasks), ["bom"])
        self.assertEqual(err, "")

    def test_tasks_one_level_deeper_are_found(self):
        # аудит edc1c14, A8: раскладка архива примеров `<dir>/task-dir/<id>/task.json` — не 0 задач
        for name in ("a", "b"):
            (self.root / "task-dir" / name).mkdir(parents=True)
            (self.root / "task-dir" / name / "task.json").write_text(json.dumps({"task_id": name}), encoding="utf-8")
        tasks, _ = self.load()
        self.assertEqual(sorted(tasks), ["a", "b"])


if __name__ == "__main__":
    unittest.main()
