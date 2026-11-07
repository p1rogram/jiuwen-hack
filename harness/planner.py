"""Порядок решения задач: reward / оценка расхода; задачи дороже остатка бюджета пропускаются."""
from __future__ import annotations

from typing import Any

from harness.tasks import Task


class TaskPlanner:
    def __init__(self, cost_by_type: dict[str, Any]):
        self._cost = cost_by_type

    def estimate(self, task: Task) -> float:
        return float(self._cost.get(task.task_type, self._cost["default"]))

    def order(self, tasks: list[Task]) -> list[Task]:
        return sorted(tasks, key=lambda t: -(t.reward / max(self.estimate(t), 1.0)))
