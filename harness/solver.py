"""Этап 1: вызов модели → Python-скрипт → запуск (или вклейка правки) → локальная проверка. Повтор — по решению TaskPipeline."""
from __future__ import annotations

from typing import Any

from harness.answer_shape import answer_shape
from harness.calllog import JsonlLog
from harness.executor import run_script
from harness.llm import LlmGateway
from harness.outcome import Outcome
from harness.patching import CODE_RE, PatchApplier, find_patch
from harness.prompting import Prepared, Stage1Prompt
from harness.tasks import Task
from harness.verifier import NO_CODE, TRUNCATED, Verdict, Verifier

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
                 model: str, cfg: dict[str, Any], log: JsonlLog | None = None, stage: str = "stage1"):
        self._gw = gateway
        self._stage = stage  # имя этапа в журнале: модель расхода калибруется только по stage1.*
        self._log = log
        self._prompt = prompt
        self._verifier = verifier
        self._model = model
        self._attempts = int(cfg["attempts"])
        # потолок выхода по типу задачи — только у основного этапа 1: модели перекрёстной проверки бывают с
        # рассуждением, его токены входят в потолок; тип без потолка — общий llm.max_output_tokens шлюза
        caps = cfg.get("max_output_tokens", {}) if stage == "stage1" else {}
        self._caps = {t: int(v) for t, v in caps.items()}
        self._timeout = float(cfg["script_timeout_sec"])
        self._patcher = PatchApplier()

    def can_retry(self, outcome: Outcome) -> bool:
        return outcome.attempts < self._attempts

    async def first(self, prepared: Prepared, outcome: Outcome) -> Verdict:
        outcome.messages = list(prepared.messages)
        return await self._attempt(prepared, outcome)

    async def retry(self, prepared: Prepared, outcome: Outcome, failed: Verdict) -> Verdict:
        outcome.messages.append(self._prompt.retry(failed.problem, prepared.task.task_type))
        return await self._attempt(prepared, outcome)

    async def _attempt(self, prepared: Prepared, outcome: Outcome) -> Verdict:
        task = prepared.task
        stage = f"{self._stage}.{outcome.attempts + 1}"
        cap = self._caps.get(task.task_type)
        reply = await self._gw.ask(self._model, outcome.messages, task_id=task.task_id, stage=stage, max_tokens=cap)
        outcome.attempts += 1
        outcome.add_call(self._model, stage, reply)
        outcome.messages.append({"role": "assistant", "content": reply.text})
        if cap is not None and reply.usage.output_tokens >= cap:
            # не исполнять обрезанный ответ: в нём догадка вместо данных, а этап 2 на таких задачах не помогал
            return Verdict(False, "", f"reply hit the {cap}-token output cap", TRUNCATED)
        # правка (блоки с `# file:` или одни функции модуля) — только у типов patch_types; иной ответ — как скрипт
        patch_type = self._prompt.is_patch(task.task_type)
        blocks = find_patch(reply.text, task.workspace) if patch_type else None
        code = None if blocks else extract_code(reply.text)
        if patch_type:
            # до вклейки: после неё исходник уже изменён и сравнивать функции не с чем
            self._shape(task, stage, reply.text, blocks, "patch" if blocks else "script" if code is not None else "none")
        if blocks:
            outcome.applied = "patch"
            return await self._verifier.check(task, self._patcher.apply(blocks, task.workspace), visible_tests=prepared.visible_tests, allow_unchecked=True)
        if code is None:
            return Verdict(False, "", "no ```python code block in the reply", NO_CODE)
        outcome.applied = "script"
        run = await run_script(code, task.workspace, self._timeout)
        return await self._verifier.check(task, run, visible_tests=prepared.visible_tests, allow_unchecked=True)

    def _shape(self, task: Task, stage: str, text: str, blocks: list | None, applied: str) -> None:
        if self._log is None:
            return
        try:
            shape = answer_shape(text, blocks, task.workspace)
        except Exception as exc:  # noqa: BLE001 — замер не должен ронять попытку
            shape = {"error": f"{type(exc).__name__}: {exc}"[:200]}
        self._log.write({"kind": "answer_shape", "task_id": task.task_id, "stage": stage, "applied": applied, **shape})
