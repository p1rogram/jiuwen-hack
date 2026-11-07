"""Запуск Python-кода в папке задачи с таймаутом (без LLM)."""
from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RunResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


async def run_python(args: list[str], cwd: Path, timeout_sec: float) -> RunResult:
    env = dict(os.environ)
    # ключ API не должен попадать в процессы, которые пишет модель
    for name in ("LLM_API_KEY", "NEURALDEEP_API_KEY", "OPENAI_API_KEY", "API_KEY"):
        env.pop(name, None)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = await asyncio.create_subprocess_exec(
        sys.executable, *args, cwd=str(cwd), env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
    except asyncio.TimeoutError:
        proc.kill()
        out, err = await proc.communicate()
        return RunResult(-1, out.decode("utf-8", "replace"), err.decode("utf-8", "replace"), True)
    return RunResult(proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace"), False)


async def run_script(code: str, cwd: Path, timeout_sec: float, name: str = "_agent_script.py") -> RunResult:
    script = cwd / name
    script.write_text(code, encoding="utf-8")
    try:
        return await run_python([name], cwd, timeout_sec)
    finally:
        script.unlink(missing_ok=True)
