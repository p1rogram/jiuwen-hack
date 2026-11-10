"""Сигнал детектора невыполнимости и общие текстовые помощники."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from harness.tasks import Task

HARD, SOFT, INFO = "hard", "soft", "info"
SENTENCE_RE = re.compile(r"(?<=[.?!])\s+")


@dataclass(frozen=True)
class Signal:
    code: str
    severity: str  # hard — доказано, что задача невыполнима; soft — спорно; info — служебное
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "severity": self.severity, "detail": self.detail}


def questions(instruction: str) -> list[str]:
    return [s for s in SENTENCE_RE.split(instruction) if s.rstrip().endswith("?")]


def roots(task: Task) -> list[Path]:
    """Где искать файлы задачи: workspace/ и сама папка задачи."""
    return list(dict.fromkeys([task.workspace, task.task_dir]))


def find_file(task: Task, rel: str) -> Path | None:
    """Путь от корня задачи, иначе — тот же путь глубже: «The directory data/ contains sales.csv» лежит в data/."""
    for root in roots(task):
        path = root / rel
        if path.exists():
            return path
    parts = Path(rel).parts
    for root in roots(task):
        if not root.is_dir():
            continue
        for path in sorted(root.rglob(parts[-1])):
            if path.parts[-len(parts):] == parts:
                return path
    return None
