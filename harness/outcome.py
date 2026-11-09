"""Итог задачи, который заполняют этапы по ходу: при исключении в нём остаются уже оплаченные вызовы."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Outcome:
    task_id: str
    answer: str = ""
    verified: bool = False
    attempts: int = 0  # вызовы этапа 1
    cost: float = 0.0
    problem: str = ""
    failure: str = ""  # вид последнего провала (verifier.FAILURE_KINDS); у решённой пусто
    solved_by: str = ""  # stage1 | retry | stage2
    stage2_steps: int = 0
    stage2_stop: str = ""  # почему цикл этапа 2 остановился
    applied: str = ""  # как применён последний ответ этапа 1: patch | script
    calls: list[dict[str, Any]] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    # переписка этапа 1 вместе с последним ответом модели — старт контекста этапа 2; в журнал не пишется
    messages: list[dict[str, str]] = field(default_factory=list)

    def add_call(self, model: str, stage: str, reply: Any) -> None:
        self.cost += reply.cost
        self.calls.append({"model": model, "stage": stage, "in": reply.usage.input_tokens,
                           "out": reply.usage.output_tokens, "reasoning": reply.usage.reasoning_tokens})

    def first_stage1_call(self) -> dict[str, Any] | None:
        """Сырьё поправки модели расхода этапа 1; в --force-stage2 этапа 1 нет — None."""
        return next((c for c in self.calls if c.get("stage", "stage1.1") == "stage1.1"), None)
