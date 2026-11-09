"""Загрузка задач из каталога: <task>/task.json + необязательный <task>/workspace/."""
from __future__ import annotations

import json
import math
import sys
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


# reward нет или не число: 0 сделал бы EMV любой ветки ≤ 0 — задача без повтора и этапа 2 (spec robustness, шаг 2)
DEFAULT_REWARD = 1.0


def parse_reward(value: object) -> float:
    try:
        reward = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_REWARD
    return reward if math.isfinite(reward) else DEFAULT_REWARD


def load_tasks(tasks_dir: Path) -> list[Task]:
    """Битый task.json пропускается со строкой в stderr: одна задача не должна сорвать загрузку остальных."""
    tasks = []
    for task_json in sorted(tasks_dir.glob("*/task.json")):
        try:
            data = json.loads(task_json.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("not a JSON object")
        except (OSError, ValueError) as exc:
            print(f"пропущена задача {task_json.parent.name}: task.json не читается ({exc})", file=sys.stderr)
            continue
        tasks.append(Task(
            task_id=str(data.get("task_id") or task_json.parent.name),
            task_type=str(data.get("task_type", "unknown")),
            instruction=str(data.get("instruction", "")),
            reward=parse_reward(data.get("reward")),
            check=str(data.get("check", "")),
            task_dir=task_json.parent,
        ))
    return tasks
