"""Агент прогона — наследник `BaseAgent` openjiuwen: точка входа и события фреймворка; решение задач — в `Harness`."""
from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator, Protocol

import harness.jiuwen_logging  # noqa: F401  — до openjiuwen: тихая консоль
from openjiuwen.core.single_agent import AgentCard
from openjiuwen.core.single_agent.base import BaseAgent
from openjiuwen.core.single_agent.rail.base import AgentCallbackEvent, InvokeInputs

from harness.budget import BudgetLedger
from harness.calllog import JsonlLog
from harness.llm import LlmGateway
from harness.model_call import ModelCall
from harness.rails import TokenBudgetRail
from harness.tasks import Task, load_tasks


class TaskRunner(Protocol):
    ledger: BudgetLedger
    log: JsonlLog
    gateway: LlmGateway

    async def run(self, tasks: list[Task]) -> list[dict]: ...


def agent_card(cfg: dict[str, Any]) -> AgentCard:
    return AgentCard(id=cfg["id"], name=cfg["name"], description=cfg["description"])


class HarnessAgent(BaseAgent):
    """`invoke({"tasks_dir": ..., "only": [...]})` — весь прогон каталога задач; итог — записи задач.

    Тонкая обёртка: порядок, бюджет и развилки остаются в `Harness`, здесь — загрузка задач и события агента.
    """

    def __init__(self, runner: TaskRunner, card: AgentCard):
        super().__init__(card)
        self._runner = runner

    def configure(self, config: Any) -> "HarnessAgent":
        self._config = config
        return self

    async def invoke(self, inputs: dict[str, Any], session: Any = None) -> dict[str, Any]:
        tasks = load_tasks(Path(inputs["tasks_dir"]))
        only = inputs.get("only")
        if only:
            tasks = [t for t in tasks if t.task_id in set(only)]
        query = str(inputs["tasks_dir"])
        await self._execute_callbacks(AgentCallbackEvent.BEFORE_INVOKE, InvokeInputs(query=query))
        result = {"results": await self._runner.run(tasks)}
        await self._execute_callbacks(AgentCallbackEvent.AFTER_INVOKE, InvokeInputs(query=query, result=result))
        return result

    async def before_model_call(self, call: ModelCall) -> None:
        await self._execute_callbacks(AgentCallbackEvent.BEFORE_MODEL_CALL, call)

    async def after_model_call(self, call: ModelCall) -> None:
        await self._execute_callbacks(AgentCallbackEvent.AFTER_MODEL_CALL, call)

    async def stream(self, inputs: dict[str, Any], session: Any = None,
                     stream_modes: Any = None) -> AsyncIterator[dict[str, Any]]:
        yield await self.invoke(inputs, session)


async def build_agent(runner: TaskRunner, card: AgentCard) -> HarnessAgent:
    """Агент с рельсой учёта токенов; вызовы модели шлюза идут через события агента."""
    agent = HarnessAgent(runner, card)
    await agent.register_rail(TokenBudgetRail(runner.ledger, runner.log))
    runner.gateway.hooks = agent
    return agent
