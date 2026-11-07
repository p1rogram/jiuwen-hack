"""Сообщения этапа 1 без LLM: системный промпт, задача + срез папки, шаблон повтора."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from harness.settings import load_prompt
from harness.snapshot import WorkspaceSnapshot
from harness.tasks import Task


def prompt_chars(messages: list[dict[str, str]]) -> int:
    return sum(len(m["content"]) for m in messages)


@dataclass
class Prepared:
    task: Task
    messages: list[dict[str, str]]
    signals: list[Any] = field(default_factory=list)  # soft-сигналы детектора — в журнал задачи

    @property
    def prompt_chars(self) -> int:
        return prompt_chars(self.messages)


class Stage1Prompt:
    def __init__(self, snapshot: WorkspaceSnapshot):
        self._snapshot = snapshot
        self._system = load_prompt("system.txt")
        self._user_tpl = load_prompt("stage1_user.txt")
        self._retry_tpl = load_prompt("stage1_retry.txt")

    async def build(self, task: Task) -> Prepared:
        snap = await self._snapshot.build(task)
        return Prepared(task, [
            {"role": "system", "content": self._system},
            {"role": "user", "content": self._user_tpl.format(instruction=task.instruction, snapshot=snap)},
        ])

    def retry(self, problem: str) -> dict[str, str]:
        return {"role": "user", "content": self._retry_tpl.format(problem=problem)}
