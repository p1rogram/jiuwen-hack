"""Сборка команды tools/docker_run и проверка перед стартом (spec spark-build, шаг 5). Docker не нужен.

Ожидания — из spec: ключ только по имени, сеть хоста для loopback-сервера, Apptainer с --pwd /tmp и изоляцией,
проверка перед стартом останавливает прогон, если модели этапов нет на сервере.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from harness import settings as settings_mod
from harness.settings import Settings, load_settings_raw
from tools import docker_run

KEY = "laika-secret-key-777"
FIXTURES = Path(__file__).parent / "fixtures" / "profiles"  # профиль стенда для тестов; настоящие профили — локальные


def settings(profile: str | None, base_url: str, key: str = KEY) -> Settings:
    with mock.patch.object(settings_mod, "PROFILES_DIR", FIXTURES):
        raw = load_settings_raw(profile)
    return Settings(raw=raw, base_url=base_url, api_key=key, out_path=None,
                    log_dir=Path("logs"), budget=100_000, profile=profile)


def args(*extra: str):
    return docker_run.parse_args(["tasks", "out", *extra])


class DockerCommandTest(unittest.TestCase):
    def test_stand_docker_uses_host_network_and_names_only(self):
        cmd, env = docker_run.build_command(args("--budget", "1000000"),
                                            settings("stand", "http://127.0.0.1:22500/v1"))
        self.assertIn("--network", cmd)
        self.assertEqual(cmd[cmd.index("--network") + 1], "host")
        for name in ("LLM_API_KEY", "LLM_BASE_URL", "HARNESS_PROFILE", "BUDGET"):
            self.assertIn(name, cmd)
        self.assertFalse(any(KEY in part for part in cmd))
        self.assertEqual((env["LLM_API_KEY"], env["HARNESS_PROFILE"], env["BUDGET"]), (KEY, "stand", "1000000"))
        self.assertIn("--read-only", cmd)

    def test_proxy_run_unchanged(self):
        cmd, env = docker_run.build_command(args(), settings(None, "https://api.neuraldeep.ru/v1"))
        self.assertNotIn("--network", cmd)
        self.assertNotIn("HARNESS_PROFILE", cmd)
        self.assertEqual(cmd[-4:], docker_run.RUN)


class ApptainerCommandTest(unittest.TestCase):
    def test_apptainer_isolated_and_key_via_env(self):
        cmd, env = docker_run.build_command(args("--runtime", "apptainer", "--image", "jiuwen-harness.sif"),
                                            settings("stand", "http://127.0.0.1:22500/v1"))
        self.assertEqual(cmd[:2], ["apptainer", "exec"])
        for flag in ("--containall", "--cleanenv", "--no-home", "--writable-tmpfs"):
            self.assertIn(flag, cmd)
        self.assertEqual(cmd[cmd.index("--pwd") + 1], "/tmp")
        binds = [cmd[i + 1] for i, c in enumerate(cmd) if c == "--bind"]
        self.assertEqual([b.rsplit(":", 1)[1] for b in binds], ["/data/tasks", "/data/out"])
        self.assertFalse(any(KEY in part for part in cmd))
        self.assertEqual(env["APPTAINERENV_LLM_API_KEY"], KEY)
        self.assertEqual(env["APPTAINERENV_HARNESS_PROFILE"], "stand")
        self.assertEqual(cmd[cmd.index("jiuwen-harness.sif") + 1:], docker_run.RUN)


class PreflightTest(unittest.TestCase):
    def test_passes_when_served_model_listed(self):
        with mock.patch.object(docker_run, "list_models", return_value=["served/qwen3.6-35b-a3b"]) as lm:
            docker_run.preflight(settings("stand", "http://127.0.0.1:22500/v1"))
        self.assertEqual(lm.call_args.args[1], KEY)

    def test_rejects_when_other_model_running(self):
        with mock.patch.object(docker_run, "list_models", return_value=["served/qwen3.8-27b"]), \
                self.assertRaises(SystemExit) as cm:
            docker_run.preflight(settings("stand", "http://127.0.0.1:22500/v1"))
        self.assertIn("served/qwen3.6-35b-a3b", str(cm.exception))

    def test_rejects_when_server_down(self):
        with self.assertRaises(SystemExit) as cm:
            docker_run.preflight(settings("stand", "http://127.0.0.1:9/v1"))
        self.assertIn("not reachable", str(cm.exception))

    def test_placeholder_key_not_sent(self):
        with mock.patch.object(docker_run, "list_models", return_value=["served/qwen3.6-35b-a3b"]) as lm:
            docker_run.preflight(settings("stand", "http://127.0.0.1:22500/v1", key="EMPTY"))
        self.assertEqual(lm.call_args.args[1], "")


if __name__ == "__main__":
    unittest.main()
