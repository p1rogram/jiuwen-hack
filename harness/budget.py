"""Учёт бюджета в выходных токенах: cost = output (+reasoning) + input * input_weight."""
from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int


class TokenPricing:
    def __init__(self, input_weight: float, reasoning_in_completion: bool):
        self._input_weight = input_weight
        self._reasoning_in_completion = reasoning_in_completion

    def cost(self, usage: Usage) -> float:
        out = usage.output_tokens
        if not self._reasoning_in_completion:
            out += usage.reasoning_tokens
        return out + usage.input_tokens * self._input_weight


class BudgetLedger:
    """Общий счётчик на все сессии; потокобезопасен.

    Резерв: задача в работе держит оценку своей цены, пока её вызовы не спишут столько же, —
    иначе параллельные задачи по отдельности «помещаются» в остаток, а вместе — нет.
    """

    def __init__(self, total: float):
        self.total = total
        self._spent = 0.0
        self._held: dict[str, float] = {}
        self._lock = threading.Lock()

    def charge(self, cost: float, task_id: str | None = None) -> None:
        with self._lock:
            self._spent += cost
            if task_id in self._held:
                self._held[task_id] = max(0.0, self._held[task_id] - cost)

    def reserve(self, task_id: str, amount: float) -> bool:
        """Держит amount под задачу, если он помещается в остаток за вычетом чужих резервов."""
        with self._lock:
            if self.total - self._spent - sum(self._held.values()) < amount:
                return False
            self._held[task_id] = amount
            return True

    def release(self, task_id: str) -> None:
        with self._lock:
            self._held.pop(task_id, None)

    @property
    def spent(self) -> float:
        return self._spent

    @property
    def remaining(self) -> float:
        return self.total - self._spent

    @property
    def available(self) -> float:
        with self._lock:
            return self.total - self._spent - sum(self._held.values())
