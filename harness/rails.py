"""Рельсы агента openjiuwen: учёт токенов прогона на событиях вызова модели."""
from __future__ import annotations

import harness.jiuwen_logging  # noqa: F401  — до openjiuwen: тихая консоль
from openjiuwen.core.runner.callback import AbortError
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentRail

from harness.budget import BudgetLedger
from harness.calllog import JsonlLog
from harness.model_call import BudgetExhausted, ModelCall, admit, settle


class TokenBudgetRail(AgentRail):
    """До вызова — допуск по остатку бюджета, после — списание цены и запись `llm_call` в журнал.

    Останов — `AbortError` с причиной `BudgetExhausted`: прочие исключения рельс фреймворк глушит,
    и вызов модели прошёл бы при исчерпанном бюджете.
    """

    def __init__(self, ledger: BudgetLedger, log: JsonlLog):
        super().__init__()
        self._ledger = ledger
        self._log = log

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        call = ctx.inputs
        if not isinstance(call, ModelCall):
            return
        try:
            admit(call, self._ledger)
        except BudgetExhausted as exc:
            raise AbortError("token budget exhausted", cause=exc, is_error=False) from exc

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        call = ctx.inputs
        if isinstance(call, ModelCall) and not call.charged:
            settle(call, self._ledger, self._log)
