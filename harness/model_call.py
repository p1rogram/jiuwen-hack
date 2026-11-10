"""Вызов модели как событие агента: вход `BEFORE/AFTER_MODEL_CALL` openjiuwen + допуск и списание по бюджету.

Допуск и списание делает рельса `TokenBudgetRail`; шлюз повторяет их сам, только если рельса этого не сделала
(её нет или она упала): цена списывается ровно один раз.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import harness.jiuwen_logging  # noqa: F401  — до openjiuwen: тихая консоль
from openjiuwen.core.single_agent.rail.base import ModelCallInputs

from harness.budget import BudgetLedger, Usage
from harness.calllog import JsonlLog


class BudgetExhausted(RuntimeError):
    pass


@dataclass
class ModelCall(ModelCallInputs):
    task_id: str = ""
    stage: str = ""
    model: str = ""
    served_model: str | None = None  # имя на сервере стенда, если отличается от логического
    prompt_chars: int = 0
    usage: Usage | None = None
    cost: float = 0.0
    latency_sec: float = 0.0
    reasoning_chars: int = 0
    content_chars: int = 0
    usage_missing: bool = False
    admitted: bool = False
    charged: bool = False

    def journal(self, budget_spent: float) -> dict[str, Any]:
        usage = self.usage or Usage(0, 0, 0)
        return {
            "kind": "llm_call", "task_id": self.task_id, "stage": self.stage, "model": self.model,
            **({"served_model": self.served_model} if self.served_model else {}),
            "prompt_chars": self.prompt_chars, "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens, "reasoning_tokens": usage.reasoning_tokens,
            "reasoning_chars": self.reasoning_chars, "content_chars": self.content_chars,
            "cost": round(self.cost, 1), "latency_sec": round(self.latency_sec, 2),
            "budget_spent": round(budget_spent, 1),
            **({"usage_missing": True} if self.usage_missing else {}),
        }


class ModelCallHooks(Protocol):
    async def before_model_call(self, call: ModelCall) -> None: ...

    async def after_model_call(self, call: ModelCall) -> None: ...


def admit(call: ModelCall, ledger: BudgetLedger) -> None:
    if ledger.remaining <= 0:
        raise BudgetExhausted("budget exhausted")
    call.admitted = True


def settle(call: ModelCall, ledger: BudgetLedger, log: JsonlLog) -> None:
    ledger.charge(call.cost, call.task_id)
    call.charged = True  # сразу после списания: сбой записи журнала не должен вызвать второе списание
    log.write(call.journal(ledger.spent))
