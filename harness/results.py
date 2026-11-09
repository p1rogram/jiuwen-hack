"""Запись ответов: answer.txt в папку задачи и общий файл OUT (атомарно после каждой задачи).

Ошибка записи не бросается наружу, а возвращается текстом: один недоступный файл не должен оборвать прогон
и лишить жюри ответов остальных задач (spec robustness, шаг 1)."""
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
        # OUT есть с начала прогона: даже если ни одна задача не будет принята, жюри получит валидный JSON
        self.init_error = self._flush()

    def record(self, task: Task, answer: str) -> str:
        """Пустая строка — всё записано; иначе текст ошибки для журнала. OUT и answer.txt пишутся независимо."""
        errors = []
        try:
            # только рядом с task.json: так ответили организаторы (обзор вопросов, 07.11); копия в workspace не нужна
            (task.task_dir / "answer.txt").write_text(answer + "\n", encoding="utf-8")
        except OSError as exc:
            errors.append(f"answer.txt: {exc}")
        with self._lock:
            self._answers[task.task_id] = answer
            if err := self._flush():
                errors.append(err)
        return "; ".join(errors)

    @staticmethod
    def discard(task: Task) -> str:
        """Задача без принятого ответа — без answer.txt («невыполнимая — не создавайте файл»). Нужно, потому что
        скрипт модели работает в task.workspace, а у задач без workspace/ (math) это папка самой задачи."""
        try:
            (task.task_dir / "answer.txt").unlink(missing_ok=True)
        except OSError as exc:
            return f"answer.txt: {exc}"
        return ""

    def _flush(self) -> str:
        if self._out is None:
            return ""
        try:
            self._out.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._out.with_name(self._out.name + ".tmp")
            tmp.write_text(json.dumps(self._answers, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self._out)
        except OSError as exc:
            return f"OUT: {exc}"
        return ""
