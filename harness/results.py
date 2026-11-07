"""Запись ответов: answer.txt в папку задачи и общий файл OUT (атомарно после каждой задачи)."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from harness.tasks import Task


class ResultsWriter:
    def __init__(self, out_path: Path | None):
        self._out = out_path
        self._answers: dict[str, str] = {}
        self._lock = threading.Lock()

    def record(self, task: Task, answer: str) -> None:
        # В регламенте «папка задачи», в инструкции «текущая папка» — пишем в обе, пока не уточнили.
        for folder in {task.task_dir, task.workspace}:
            (folder / "answer.txt").write_text(answer + "\n", encoding="utf-8")
        with self._lock:
            self._answers[task.task_id] = answer
            self._flush()

    def _flush(self) -> None:
        if self._out is None:
            return
        self._out.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._out.with_name(self._out.name + ".tmp")
        tmp.write_text(json.dumps(self._answers, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self._out)
