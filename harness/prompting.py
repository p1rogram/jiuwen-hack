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

    visible_tests: tuple[tuple[str, bytes], ...] = field(init=False, repr=False)

    def __post_init__(self):
        self.visible_tests = tuple((p.name, p.read_bytes()) for p in sorted(self.task.workspace.glob("test_*.py")))

    @property
    def prompt_chars(self) -> int:
        return prompt_chars(self.messages)


class Stage1Prompt:
    def __init__(self, snapshot: WorkspaceSnapshot, unsolvable_marker: str | None = None,
                 tail_prompts: dict[str, str] | None = None, patch_types: list[str] | None = None,
                 patch_prompts: dict[str, str] | None = None):
        self._snapshot = snapshot
        # правило формы ответа — в конце сообщения задачи: из системного промпта модель его не выполняет (замер 2026-11-09)
        self._tails = {t: load_prompt(name).strip() for t, name in (tail_prompts or {}).items()}
        rule = load_prompt("system_unsolvable.txt").format(marker=unsolvable_marker) if unsolvable_marker else ""
        self._system = self._with_rule(load_prompt("system.txt"), rule)
        self._retry_tpl = load_prompt("stage1_retry.txt")
        # типы с ответом-правкой (только изменённые функции, вклейку делает харнесс): свои система и повтор
        self._patch_types = set(patch_types or [])
        if self._patch_types:
            self._patch_system = self._with_rule(load_prompt(patch_prompts["system"]), rule)
            self._patch_retry_tpl = load_prompt(patch_prompts["retry"])
        self._user_tpl = load_prompt("stage1_user.txt")

    @staticmethod
    def _with_rule(system: str, rule: str) -> str:
        return system.rstrip("\n") + "\n" + rule if rule else system

    def is_patch(self, task_type: str) -> bool:
        return task_type in self._patch_types

    async def build(self, task: Task) -> Prepared:
        snap = await self._snapshot.build(task)
        user = self._user_tpl.format(instruction=task.instruction, snapshot=snap)
        tail = self._tails.get(task.task_type)
        if tail:
            user = user.rstrip() + "\n\n" + tail
        system = self._patch_system if self.is_patch(task.task_type) else self._system
        return Prepared(task, [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ])

    def retry(self, problem: str, task_type: str = "") -> dict[str, str]:
        tpl = self._patch_retry_tpl if self.is_patch(task_type) else self._retry_tpl
        return {"role": "user", "content": tpl.format(problem=problem)}
