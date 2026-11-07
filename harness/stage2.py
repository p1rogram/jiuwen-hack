"""Этап 2: короткий ReAct после провала этапа 1 — модель запускает скрипты в папке задачи и сдаёт ответ блоком FINAL.

Свой цикл через LlmGateway, а не ReActAgent openjiuwen: учёт, семафор и журнал — те же, что у этапа 1,
а стоп по бюджету задачи и по повтору нужен до каждого вызова, чего агентный цикл пакета не даёт.
"""
from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path
from typing import Any

from harness.cost_model import CostModel
from harness.executor import RunResult, run_script
from harness.llm import LlmGateway
from harness.outcome import Outcome
from harness.prompting import Prepared, prompt_chars
from harness.settings import load_prompt
from harness.solver import extract_code
from harness.verifier import NO_CODE, Verdict, Verifier

FINAL_RE = re.compile(r"^\s*FINAL:\s*(.+?)\s*$", re.MULTILINE)
# «та же ошибка» — та же строка исключения: путь кода к ней и номера строк после правки меняются
EXC_RE = re.compile(r"^[\w.]*(?:Error|Exception|Exit|Interrupt)\b.*$", re.MULTILINE)
NOISE_RE = re.compile(r"0x[0-9a-fA-F]+|\s+")


def error_signature(text: str) -> str:
    found = EXC_RE.findall(text)
    return NOISE_RE.sub(" ", found[-1] if found else text[-400:]).strip()


def workspace_state(root: Path) -> str:
    """Отпечаток содержимого файлов задачи: изменил ли шаг что-нибудь (кэш байткода не в счёт)."""
    h = hashlib.sha1()
    for f in sorted(root.rglob("*")):
        if f.is_file() and "__pycache__" not in f.parts:
            h.update(f.relative_to(root).as_posix().encode() + b"\0" + f.read_bytes() + b"\0")
    return h.hexdigest()


def final_answer(text: str) -> str | None:
    found = FINAL_RE.findall(text)
    return found[-1].strip().strip("`").strip() if found else None


class Stage2Solver:
    def __init__(self, gateway: LlmGateway, verifier: Verifier, cost: CostModel, model: str, cfg: dict[str, Any]):
        self._gw = gateway
        self._verifier = verifier
        self._cost = cost
        self._model = model
        self._max_steps = int(cfg["max_steps"])
        self._max_tokens = int(cfg["max_step_tokens"])
        self._timeout = float(cfg["script_timeout_sec"])
        self._tail = int(cfg["obs_tail_chars"])
        self._start = load_prompt("stage2_start.txt")
        self._obs = load_prompt("stage2_obs.txt")
        self._check = load_prompt("stage2_check.txt")
        self._format = load_prompt("stage2_format.txt")

    async def solve(self, prepared: Prepared, outcome: Outcome, failed: Verdict, budget: float) -> Verdict:
        """budget — потолок расхода этапа 2 (high ветки): шаг, который может его превысить, не начинается."""
        task = prepared.task
        messages = list(outcome.messages) + [
            {"role": "user", "content": self._start.format(problem=failed.problem, max_steps=self._max_steps)}]
        seen_code: dict[str, RunResult] = {}
        seen_errors = {error_signature(failed.problem)}
        spent_before = outcome.cost
        verdict = failed
        for step in range(1, self._max_steps + 1):
            if outcome.cost - spent_before + self._cost.stage2_step(task.task_type, prompt_chars(messages)) > budget:
                return self._stop(outcome, "budget", verdict)
            stage = f"stage2.{step}"
            reply = await self._gw.ask(self._model, messages, task_id=task.task_id, stage=stage,
                                       max_tokens=self._max_tokens)
            outcome.add_call(self._model, stage, reply)
            outcome.stage2_steps = step
            messages.append({"role": "assistant", "content": reply.text})
            code, final = extract_code(reply.text), final_answer(reply.text)
            if code is not None:
                if code.strip() in seen_code:
                    return await self._repeated(task, outcome, seen_code[code.strip()], verdict)
                before = workspace_state(task.workspace) if task.check == "unittest" else ""
                run = await run_script(code, task.workspace, self._timeout)
                seen_code[code.strip()] = run
                error = run.stderr if not run.ok else ""
                if task.check == "unittest":
                    # зелёные тесты — настоящая проверка, FINAL ради неё не нужен
                    verdict = await self._verifier.check(task, run)
                    if verdict.ok:
                        return self._stop(outcome, "verified", verdict)
                    # шаг без правок (прочитать файл) оставляет прежний провал тестов — это не повтор ошибки
                    if workspace_state(task.workspace) != before:
                        error = error or verdict.problem
                feedback = self._observation(run)
            elif final is not None:
                verdict = await self._verifier.check(task, RunResult(0, final + "\n", "", False, time.time()))
                if verdict.ok:
                    return self._stop(outcome, "verified", verdict)
                error, feedback = verdict.problem, self._check.format(problem=verdict.problem)
            else:
                verdict = Verdict(False, verdict.answer, "no code block and no FINAL line", NO_CODE)
                error, feedback = verdict.problem, self._format
            if error:
                sig = error_signature(error)
                if sig in seen_errors:
                    return self._stop(outcome, "repeat_error", verdict)
                seen_errors.add(sig)
            messages.append({"role": "user", "content": feedback})
        return self._stop(outcome, "max_steps", verdict)

    async def _repeated(self, task, outcome: Outcome, run: RunResult, verdict: Verdict) -> Verdict:
        """Тот же код второй раз: модель считает его вывод ответом (протокол этапа 1) — принимаем, если он из одной
        строки и проходит проверку; распечатка файла и вывод тестов ответом не бывают."""
        if task.check != "unittest" and run.ok and len(run.stdout.strip().splitlines()) == 1:
            answered = await self._verifier.check(task, run)
            if answered.ok:
                return self._stop(outcome, "repeat_answer", answered)
        return self._stop(outcome, "repeat_command", verdict)

    def _observation(self, run: RunResult) -> str:
        return self._obs.format(code=run.returncode, timeout=" (timed out)" if run.timed_out else "",
                                stdout=run.stdout[-self._tail:] or "(empty)", stderr=run.stderr[-self._tail:] or "(empty)")

    @staticmethod
    def _stop(outcome: Outcome, reason: str, verdict: Verdict) -> Verdict:
        outcome.stage2_stop = reason
        return verdict
