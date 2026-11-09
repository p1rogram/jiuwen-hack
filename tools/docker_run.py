"""Локальный прогон образа с ограничениями регламента — в Docker или Apptainer.

Ключ и адрес берутся из env/.env кодом и передаются в контейнер по имени переменной (-e NAME, APPTAINERENV_NAME),
поэтому значение не попадает в командную строку.
Запуск: python tools/docker_run.py <tasks-dir> <out-dir> [--image jiuwen-harness:dev] [--memory 16g] [--cpus 8]
        [--budget N] [--profile spark] [--runtime docker|apptainer] [--no-preflight]
На Spark: python3 tools/docker_run.py <tasks> <out> --profile spark --runtime apptainer --image jiuwen-harness.sif
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.settings import NO_KEY_PLACEHOLDER, Settings, load_settings  # noqa: E402
from tools.probe_server import ProbeError, list_models  # noqa: E402

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
RUN = ["python", "/app/run_agent.py", "--tasks-dir", "/data/tasks"]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("tasks_dir", type=Path)
    p.add_argument("out_dir", type=Path)
    p.add_argument("--image", default="jiuwen-harness:dev", help="тег Docker или путь к .sif для Apptainer")
    p.add_argument("--memory", default="16g")
    p.add_argument("--cpus", default="8")
    p.add_argument("--budget", default=None)
    p.add_argument("--profile", default=None, help="профиль стенда из config/profiles, например spark")
    p.add_argument("--runtime", choices=["docker", "apptainer"], default="docker")
    p.add_argument("--no-preflight", action="store_true", help="не проверять сервер моделей перед стартом")
    return p.parse_args(argv)


def container_env(s: Settings, budget: str | None) -> dict[str, str]:
    """Переменные для контейнера: секреты и адрес — только значениями здесь, в командной строке — лишь имена."""
    env = {"LLM_API_KEY": s.api_key, "LLM_BASE_URL": s.base_url}
    if s.profile:
        env["HARNESS_PROFILE"] = s.profile
    if budget:
        env["BUDGET"] = str(budget)
    return env


def build_command(args: argparse.Namespace, s: Settings) -> tuple[list[str], dict[str, str]]:
    """Команда запуска и env процесса, через который значения уходят в контейнер."""
    tasks, out = args.tasks_dir.resolve().as_posix(), args.out_dir.resolve().as_posix()
    passed = container_env(s, args.budget)
    fixed = {"OUT": "/data/out/answers.json", "HARNESS_LOG_DIR": "/data/out/logs"}
    host_net = (urlparse(s.base_url).hostname or "") in LOOPBACK
    if args.runtime == "apptainer":
        # Apptainer не берёт WORKDIR и монтирует $HOME: openjiuwen пишет ./logs в текущую папку → --pwd /tmp,
        # изоляция --containall, запись только в tmpfs и смонтированный выход. Сеть хоста — по умолчанию.
        cmd = ["apptainer", "exec", "--containall", "--cleanenv", "--no-home", "--writable-tmpfs", "--pwd", "/tmp",
               "--bind", f"{tasks}:/data/tasks", "--bind", f"{out}:/data/out"]
        for name, value in fixed.items():
            cmd += ["--env", f"{name}={value}"]
        env = dict(os.environ, **{f"APPTAINERENV_{k}": v for k, v in passed.items()})
        return cmd + [args.image] + RUN, env
    cmd = ["docker", "run", "--rm",
           "--read-only", "--tmpfs", "/tmp:rw,size=2g",
           "--cap-drop", "ALL", "--cpus", args.cpus, "--memory", args.memory, "--pids-limit", "512"]
    if host_net:
        cmd += ["--network", "host"]  # сервер слушает только loopback стенда
    for name in passed:
        cmd += ["-e", name]
    for name, value in fixed.items():
        cmd += ["-e", f"{name}={value}"]
    cmd += ["-v", f"{tasks}:/data/tasks", "-v", f"{out}:/data/out", args.image] + RUN
    return cmd, dict(os.environ, **passed)


def preflight(s: Settings) -> None:
    """Сервер стенда отвечает и держит модели этапов — иначе выход до первой задачи (0 токенов)."""
    key = "" if s.api_key == NO_KEY_PLACEHOLDER else s.api_key
    try:
        listed = set(list_models(s.base_url, key))
    except ProbeError as exc:
        raise SystemExit(f"preflight: model server not reachable: {exc}") from None
    model_map = s.raw["model_map"]
    need = {model_map.get(m, {}).get("model", m) for m in (s.raw["models"]["stage1"], s.raw["models"]["stage2"])}
    missing = sorted(need - listed)
    if missing:
        raise SystemExit(f"preflight: server has no {missing}; listed {sorted(listed)}. "
                         f"Start the model (laika start ...) or fix model_map in the profile.")


def main() -> None:
    args = parse_args()
    s = load_settings(profile=args.profile)
    if s.profile and not args.no_preflight:
        preflight(s)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    cmd, env = build_command(args, s)
    sys.exit(subprocess.call(cmd, env=env))


if __name__ == "__main__":
    main()
