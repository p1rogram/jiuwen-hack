"""Общее для генераторов: описание задачи, запись на диск, словари и шаблоны."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

CONTENT = Path(__file__).resolve().parent / "content"


@lru_cache(maxsize=None)
def content(name: str) -> dict[str, Any]:
    return json.loads((CONTENT / name).read_text(encoding="utf-8"))


def templates() -> dict[str, Any]:
    return content("templates.json")


def vocab() -> dict[str, Any]:
    return content("vocab.json")


@dataclass
class GenTask:
    task_type: str
    instruction: str
    check: str
    expected: Any                      # None у невыполнимых
    files: dict[str, str]              # путь внутри workspace → текст
    variant: str                       # base | hard | infeasible
    kind: str = "ok"                   # вид невыполнимости
    severity: str = ""                 # какую тяжесть сигнала ждём от детектора
    has_workspace: bool = True         # у math в примерах папки workspace нет
    reference: dict[str, str] = field(default_factory=dict)  # эталонные файлы (coding), только для тестов

    @property
    def feasible(self) -> bool:
        return self.variant != "infeasible"


def write_task(root: Path, task_id: str, task: GenTask, reward: int) -> dict[str, Any]:
    folder = root / task_id
    folder.mkdir(parents=True)
    data = {"task_id": task_id, "task_type": task.task_type, "instruction": task.instruction,
            "reward": reward, "expected": task.expected, "check": task.check}
    (folder / "task.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if task.has_workspace:
        ws = folder / "workspace"
        ws.mkdir()
        for rel, text in task.files.items():
            path = ws / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
    return {"type": task.task_type, "feasible": task.feasible, "variant": task.variant, "kind": task.kind,
            "severity": task.severity, "expected": task.expected, "reward": reward}
