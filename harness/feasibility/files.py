"""Общая проверка: файлы и папки, названные в инструкции, существуют."""
from __future__ import annotations

import re
from typing import Any

from harness.feasibility.model import HARD, SOFT, Signal, find_file
from harness.tasks import Task

DIR_RE = re.compile(r"(?<![\w./-])([A-Za-z_][\w-]*)/(?![\w/])")
SENTENCE_START_RE = re.compile(r"[.?!]\s")


class ReferencedFilesCheck:
    """hard — только когда файл или папка названы как вход задачи («The file X has», «from X», «directory X/»):
    тогда без них задача невыполнима. Любое другое упоминание (выход, пример, «no X is provided») — soft."""

    def __init__(self, cfg: dict[str, Any]):
        exts = "|".join(map(re.escape, cfg["extensions"]))
        self._file_re = re.compile(rf"(?<![\w/.-])((?:[\w-]+/)*[\w-]+\.(?:{exts}))\b")
        self._ignore = set(cfg["ignore"])
        self._verbs = cfg["output_verbs"]
        self._window = int(cfg["output_window_chars"])
        self._soft_ext = {"." + e for e in cfg["soft_extensions"]}
        self._input_before = re.compile(cfg["input_before_re"], re.IGNORECASE)
        self._input_after = re.compile(cfg["input_after_re"], re.IGNORECASE)
        self._dir_before = re.compile(cfg["dir_before_re"], re.IGNORECASE)
        self._dir_after = re.compile(cfg["dir_after_re"], re.IGNORECASE)

    def check(self, task: Task) -> list[Signal]:
        text = task.instruction
        signals = []
        for m in self._file_re.finditer(text):
            name = m.group(1)
            if name in self._ignore or self._is_output(text, m.start()) or find_file(task, name):
                continue
            # код агент может написать сам (например, mylib.py по тестам) — это спорно, а не доказано
            is_input = self._input_before.search(text[:m.start()]) or self._input_after.match(text[m.end():])
            severity = HARD if is_input and not any(name.endswith(e) for e in self._soft_ext) else SOFT
            signals.append(Signal("missing_file", severity, name))
        for m in DIR_RE.finditer(text):
            name = m.group(1)
            if self._is_output(text, m.start()):
                continue
            path = find_file(task, name)
            if path is None or not path.is_dir():
                is_dir = self._dir_before.search(text[:m.start()]) or self._dir_after.match(text[m.end():])
                signals.append(Signal("missing_dir", HARD if is_dir else SOFT, name + "/"))
        return signals

    def _is_output(self, text: str, pos: int) -> bool:
        """Глагол записи перед именем в том же предложении: «write … to the file result.txt» — выход, не вход."""
        before = text[max(0, pos - self._window):pos]
        starts = list(SENTENCE_START_RE.finditer(before))
        before = (before[starts[-1].end():] if starts else before).lower()
        return any(re.search(rf"\b{v}\b", before) for v in self._verbs)
