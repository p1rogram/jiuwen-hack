"""Общая проверка: файлы и папки, названные в инструкции, существуют."""
from __future__ import annotations

import re
from typing import Any

from harness.feasibility.model import HARD, SOFT, Signal, find_file
from harness.tasks import Task

DIR_RE = re.compile(r"(?<![\w./-])([A-Za-z_][\w-]*)/(?![\w/])")


class ReferencedFilesCheck:
    def __init__(self, cfg: dict[str, Any]):
        exts = "|".join(map(re.escape, cfg["extensions"]))
        self._file_re = re.compile(rf"(?<![\w/.-])((?:[\w-]+/)*[\w-]+\.(?:{exts}))\b")
        self._ignore = set(cfg["ignore"])
        self._verbs = cfg["output_verbs"]
        self._window = int(cfg["output_window_chars"])
        self._soft_ext = {"." + e for e in cfg["soft_extensions"]}

    def check(self, task: Task) -> list[Signal]:
        text = task.instruction
        signals = []
        for m in self._file_re.finditer(text):
            name = m.group(1)
            if name in self._ignore or self._is_output(text, m.start()) or find_file(task, name):
                continue
            # код агент может написать сам (например, mylib.py по тестам) — это спорно, а не доказано
            severity = SOFT if any(name.endswith(e) for e in self._soft_ext) else HARD
            signals.append(Signal("missing_file", severity, name))
        for m in DIR_RE.finditer(text):
            name = m.group(1)
            if self._is_output(text, m.start()):
                continue
            path = find_file(task, name)
            if path is None or not path.is_dir():
                signals.append(Signal("missing_dir", HARD, name + "/"))
        return signals

    def _is_output(self, text: str, pos: int) -> bool:
        before = text[max(0, pos - self._window):pos].lower()
        return any(re.search(rf"\b{v}\b", before) for v in self._verbs)
