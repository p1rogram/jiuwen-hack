"""Этап 1: вызов модели → Python-скрипт → запуск → локальная проверка. Повтор — по решению TaskPipeline."""
from __future__ import annotations

import re
from typing import Any

from harness.executor import run_script
from harness.llm import LlmGateway
from harness.outcome import Outcome
from harness.prompting import Prepared, Stage1Prompt
from harness.tasks import Task
from harness.verifier import NO_CODE, Verdict, Verifier

CODE_RE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL)

__all__ = ["Outcome", "Stage1Solver", "extract_code"]


def extract_code(text: str) -> str | None:
    blocks = CODE_RE.findall(text)
    if blocks:
        return blocks[-1]
    # незакрытый блок (обрезано по max_tokens) или код без разметки
    if "```" in text:
        tail = text.split("```", 1)[1]
        return tail.split("\n", 1)[1] if "\n" in tail else None
    return None


class Stage1Solver:
    def __init__(self, gateway: LlmGateway, prompt: Stage1Prompt, verifier: Verifier,
                 model: str, cfg: dict[str, Any]):
        self._gw = gateway
        self._prompt = prompt
        self._verifier = verifier
        self._model = model
        self._attempts = int(cfg["attempts"])
        self._timeout = float(cfg["script_timeout_sec"])

    def can_retry(self, outcome: Outcome) -> bool:
        return outcome.attempts < self._attempts

    async def first(self, prepared: Prepared, outcome: Outcome) -> Verdict:
        outcome.messages = list(prepared.messages)
        return await self._attempt(prepared.task, outcome)

    async def retry(self, prepared: Prepared, outcome: Outcome, failed: Verdict) -> Verdict:
        outcome.messages.append(self._prompt.retry(failed.problem))
        return await self._attempt(prepared.task, outcome)

    async def _attempt(self, task: Task, outcome: Outcome) -> Verdict:
        stage = f"stage1.{outcome.attempts + 1}"
        reply = await self._gw.ask(self._model, outcome.messages, task_id=task.task_id, stage=stage)
        outcome.attempts += 1
        outcome.add_call(self._model, stage, reply)
        outcome.messages.append({"role": "assistant", "content": reply.text})
        code = extract_code(reply.text)
        if code is None:
            return Verdict(False, "", "no ```python code block in the reply", NO_CODE)
        run = await run_script(code, task.workspace, self._timeout)
        return await self._verifier.check(task, run)
