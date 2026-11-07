"""Настройки харнесса: config/harness.json + переменные окружения (+ .env локально)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "harness.json"
PROMPTS_DIR = ROOT / "prompts"


@dataclass(frozen=True)
class Settings:
    raw: dict[str, Any]
    base_url: str
    api_key: str
    out_path: Path | None
    log_dir: Path
    budget: float

    def section(self, name: str) -> dict[str, Any]:
        return self.raw[name]


def _dotenv_values() -> dict[str, str]:
    # .env есть только на машине разработчика; в контейнере ключ приходит через env.
    env_file = ROOT / ".env"
    if not env_file.exists():
        return {}
    from dotenv import dotenv_values
    # utf-8-sig: Блокнот может сохранить файл с BOM
    return {k: v for k, v in dotenv_values(env_file, encoding="utf-8-sig").items() if v}


def _find_key(names: list[str], dotenv: dict[str, str]) -> str:
    for name in names:
        value = os.environ.get(name) or dotenv.get(name)
        if value:
            return value.strip()
    return ""


def load_settings(budget: float | None = None, out: str | None = None) -> Settings:
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    dotenv = _dotenv_values()
    base_url = _find_key(raw["base_url_env_names"], dotenv) or raw["llm_base_url_default"]
    api_key = _find_key(raw["api_key_env_names"], dotenv)
    if not api_key:
        names = sorted(dotenv)  # только имена, без значений
        state = f".env has vars: {names}" if (ROOT / ".env").exists() else ".env not found"
        raise SystemExit(f"API key not found in env {raw['api_key_env_names']}; {state}")
    out_value = out or os.environ.get("OUT")
    log_dir = Path(os.environ.get("HARNESS_LOG_DIR") or (ROOT / raw["log_dir_default"]))
    env_budget = os.environ.get("BUDGET") or os.environ.get("TOKEN_BUDGET")
    final_budget = budget if budget is not None else float(env_budget or raw["budget_default"])
    return Settings(raw=raw, base_url=base_url.rstrip("/"), api_key=api_key,
                    out_path=Path(out_value) if out_value else None,
                    log_dir=log_dir, budget=final_budget)


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")
