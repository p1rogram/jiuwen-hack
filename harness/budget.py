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
    """Общий счётчик на все сессии; потокобезопасен."""

    def __init__(self, total: float):
        self.total = total
        self._spent = 0.0
        self._lock = threading.Lock()

    def charge(self, cost: float) -> None:
        with self._lock:
            self._spent += cost

    @property
    def spent(self) -> float:
        return self._spent

    @property
    def remaining(self) -> float:
        return self.total - self._spent

    def can_afford(self, estimate: float) -> bool:
        return self.remaining >= estimate
