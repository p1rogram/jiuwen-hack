"""Неопределённый расход входа ошибочной попытки; не фактический usage сервера."""
import math

from harness.budget import BudgetLedger, TokenPricing, Usage
from harness.calllog import JsonlLog
from harness.model_call import ModelCall


def charge_failed_attempt(call: ModelCall, attempt: int, chars_per_token: float,
                          pricing: TokenPricing, ledger: BudgetLedger, log: JsonlLog) -> None:
    tokens = math.ceil(call.prompt_chars / chars_per_token)
    cost = pricing.cost(Usage(tokens, 0, 0))
    ledger.charge(cost, call.task_id)
    # Списание до журнала: сбой диска не должен превращаться в повтор API-попытки.
    log.write({
        "kind": "llm_attempt_error", "task_id": call.task_id, "stage": call.stage,
        "model": call.model,
        **({"served_model": call.served_model} if call.served_model else {}),
        "attempt": attempt, "prompt_chars": call.prompt_chars, "input_tokens": tokens,
        "cost": cost, "budget_spent": ledger.spent, "usage_estimated": True,
        "estimate_scope": "input_only", "estimate_basis": "prompt_chars",
    })
