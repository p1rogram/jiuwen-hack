"""Выбор задач под бюджет: порядок по ожидаемой выгоде на токен, допуск по высокой оценке с резервом."""
from __future__ import annotations

from typing import Callable

from harness.budget import BudgetLedger
from harness.cost_model import CostModel, Estimate
from harness.prompting import Prepared


class TaskPlanner:
    def __init__(self, model: CostModel, ledger: BudgetLedger, priority: Callable[[Prepared], float] | None = None):
        """priority — DecisionPolicy.priority (reward · p / E[цена]); без неё — reward / mean."""
        self._model = model
        self._ledger = ledger
        self._priority = priority or (lambda p: p.task.reward / max(self.estimate(p).mean, 1.0))

    def estimate(self, p: Prepared) -> Estimate:
        return self._model.estimate(p.task.task_type, p.prompt_chars)

    def order(self, prepared: list[Prepared]) -> list[Prepared]:
        return sorted(prepared, key=lambda p: -self._priority(p))

    def admit(self, p: Prepared) -> Estimate | None:
        """Оценка на момент старта (с поправкой по уже решённым); None — не помещается, задачу пропускаем.

        Допуск по high, а не по mean: перерасход бюджета обрывает весь запуск, а не одну задачу.
        """
        est = self.estimate(p)
        return est if self._ledger.reserve(p.task.task_id, est.high) else None

    def finish(self, p: Prepared, first_call: dict | None) -> None:
        self._ledger.release(p.task.task_id)
        if first_call:
            self._model.observe(p.task.task_type, p.prompt_chars, first_call["in"], first_call["out"])
