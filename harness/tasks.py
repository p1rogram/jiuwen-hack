"""Загрузка задач из каталога: <task>/task.json + необязательный <task>/workspace/."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Task:
    task_id: str
    task_type: str
    instruction: str
    reward: float
    check: str
    task_dir: Path

    @property
    def workspace(self) -> Path:
        ws = self.task_dir / "workspace"
        return ws if ws.is_dir() else self.task_dir


def load_tasks(tasks_dir: Path) -> list[Task]:
    tasks = []
    for task_json in sorted(tasks_dir.glob("*/task.json")):
        data = json.loads(task_json.read_text(encoding="utf-8"))
        tasks.append(Task(
            task_id=str(data.get("task_id") or task_json.parent.name),
            task_type=str(data.get("task_type", "unknown")),
            instruction=str(data.get("instruction", "")),
            reward=float(data.get("reward", 0) or 0),
            check=str(data.get("check", "")),
            task_dir=task_json.parent,
        ))
    return tasks
