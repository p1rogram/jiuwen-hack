"""Этап 1: вызов модели → Python-скрипт → запуск → локальная проверка (с ограниченным повтором)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from harness.executor import run_script
from harness.llm import LlmGateway
from harness.settings import load_prompt
from harness.snapshot import WorkspaceSnapshot
from harness.tasks import Task
from harness.verifier import Verdict, Verifier

CODE_RE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL)


@dataclass
class Outcome:
    task_id: str
    answer: str = ""
    verified: bool = False
    attempts: int = 0
    cost: float = 0.0
    problem: str = ""
    calls: list[dict[str, Any]] = field(default_factory=list)


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
    def __init__(self, gateway: LlmGateway, snapshot: WorkspaceSnapshot, verifier: Verifier,
                 model: str, cfg: dict[str, Any]):
        self._gw = gateway
        self._snapshot = snapshot
        self._verifier = verifier
        self._model = model
        self._attempts = int(cfg["attempts"])
        self._timeout = float(cfg["script_timeout_sec"])
        self._system = load_prompt("system.txt")
        self._user_tpl = load_prompt("stage1_user.txt")
        self._retry_tpl = load_prompt("stage1_retry.txt")

    async def solve(self, task: Task) -> Outcome:
        outcome = Outcome(task.task_id)
        snap = await self._snapshot.build(task)
        messages = [
            {"role": "system", "content": self._system},
            {"role": "user", "content": self._user_tpl.format(instruction=task.instruction, snapshot=snap)},
        ]
        for attempt in range(1, self._attempts + 1):
            reply = await self._gw.ask(self._model, messages, task_id=task.task_id, stage=f"stage1.{attempt}")
            outcome.attempts = attempt
            outcome.cost += reply.cost
            outcome.calls.append({"model": self._model, "in": reply.usage.input_tokens,
                                  "out": reply.usage.output_tokens, "reasoning": reply.usage.reasoning_tokens})
            verdict = await self._run(task, reply.text)
            if verdict.answer:
                outcome.answer = verdict.answer
            if verdict.ok:
                outcome.verified = True
                outcome.problem = ""
                return outcome
            outcome.problem = verdict.problem
            messages += [
                {"role": "assistant", "content": reply.text},
                {"role": "user", "content": self._retry_tpl.format(problem=verdict.problem)},
            ]
        return outcome

    async def _run(self, task: Task, text: str) -> Verdict:
        code = extract_code(text)
        if code is None:
            return Verdict(False, "", "no ```python code block in the reply")
        run = await run_script(code, task.workspace, self._timeout)
        return await self._verifier.check(task, run)
