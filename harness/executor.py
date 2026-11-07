"""Запуск Python-кода в папке задачи с таймаутом (без LLM)."""
from __future__ import annotations

import asyncio
import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RunResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    started: float = 0.0  # time.time() перед запуском: файлы старше — от прошлых запусков

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


async def run_python(args: list[str], cwd: Path, timeout_sec: float) -> RunResult:
    env = dict(os.environ)
    # ключ API не должен попадать в процессы, которые пишет модель
    for name in ("LLM_API_KEY", "NEURALDEEP_API_KEY", "OPENAI_API_KEY", "API_KEY"):
        env.pop(name, None)
    env["PYTHONIOENCODING"] = "utf-8"
    started = time.time()
    # своя группа процессов: по таймауту убиваем и то, что скрипт запустил сам, иначе communicate() ждёт их вывода
    group = {"creationflags": 0x00000200} if os.name == "nt" else {"start_new_session": True}
    proc = await asyncio.create_subprocess_exec(
        sys.executable, *args, cwd=str(cwd), env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **group,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
    except asyncio.TimeoutError:
        _kill_group(proc)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=5)
        except asyncio.TimeoutError:  # потомок держит трубы: ответа нет, задача не должна висеть
            out, err = b"", b""
        return RunResult(-1, out.decode("utf-8", "replace"), err.decode("utf-8", "replace"), True, started)
    return RunResult(proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace"), False, started)


def _kill_group(proc: asyncio.subprocess.Process) -> None:
    try:
        if os.name == "nt":
            proc.kill()  # Windows: дерево процессов не убить без taskkill; локальные прогоны — без потомков
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


async def run_script(code: str, cwd: Path, timeout_sec: float, name: str = "_agent_script.py") -> RunResult:
    script = cwd / name
    script.write_text(code, encoding="utf-8")
    try:
        return await run_python([name], cwd, timeout_sec)
    finally:
        script.unlink(missing_ok=True)
