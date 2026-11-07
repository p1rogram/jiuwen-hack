"""Локальный прогон образа с ограничениями регламента.

Ключ и адрес берутся из env/.env кодом и передаются в контейнер по имени переменной (-e NAME),
поэтому значение не попадает в командную строку.
Запуск: python tools/docker_run.py <tasks-dir> <out-dir> [--image jiuwen-harness:dev] [--memory 4g] [--cpus 8]
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.settings import load_settings  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("tasks_dir", type=Path)
    p.add_argument("out_dir", type=Path)
    p.add_argument("--image", default="jiuwen-harness:dev")
    p.add_argument("--memory", default="16g")
    p.add_argument("--cpus", default="8")
    p.add_argument("--budget", default=None)
    args = p.parse_args()

    s = load_settings()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, LLM_API_KEY=s.api_key, LLM_BASE_URL=s.base_url)
    cmd = [
        "docker", "run", "--rm",
        "--read-only", "--tmpfs", "/tmp:rw,size=2g",
        "--cap-drop", "ALL", "--cpus", args.cpus, "--memory", args.memory, "--pids-limit", "512",
        "-e", "LLM_API_KEY", "-e", "LLM_BASE_URL",
        "-e", "OUT=/data/out/answers.json", "-e", "HARNESS_LOG_DIR=/data/out/logs",
        "-v", f"{args.tasks_dir.resolve().as_posix()}:/data/tasks",
        "-v", f"{args.out_dir.resolve().as_posix()}:/data/out",
    ]
    if args.budget:
        cmd += ["-e", f"BUDGET={args.budget}"]
    cmd += [args.image, "python", "/app/run_agent.py", "--tasks-dir", "/data/tasks"]
    sys.exit(subprocess.call(cmd, env=env))


if __name__ == "__main__":
    main()
