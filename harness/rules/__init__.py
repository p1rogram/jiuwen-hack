"""Этап 0 без LLM: задача, условие которой целиком разобрано по шаблону, решается кодом; иначе — None и обычный путь."""
from __future__ import annotations

from typing import Any

from harness.rules.data_rules import DataRules
from harness.rules.math_rules import MathRules
from harness.rules.search_rules import SearchRules
from harness.feasibility.model import Signal
from harness.tasks import Task


class RulesSolver:
    def __init__(self, cfg: dict[str, Any]):
        self._by_type = {"math": MathRules(cfg["math"]), "data_analysis": DataRules(cfg["data"]),
                         "search": SearchRules(cfg["search"])}
        self._soft_ok = set(cfg["soft_ok"])

    def accepts(self, signals: list[Signal]) -> bool:
        """Сигналы детектора, при которых задачу всё равно решают правила: шаблон проверяет то же строже
        (data: месяцы за 12, значения фильтра нет в CSV — ответ «0», как у LLM); любой другой — обычный путь."""
        return all(s.code in self._soft_ok for s in signals)

    def solve(self, task: Task) -> str | None:
        solver = self._by_type.get(task.task_type)
        if solver is None:
            return None
        try:
            return solver.solve(task)
        except Exception:  # noqa: BLE001 — сбой разбора: задачу решит обычный путь
            return None
