"""Настройки харнесса: config/harness.json + переменные окружения (+ .env локально)."""
from __future__ import annotations

import json
import math
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "harness.json"
PROMPTS_DIR = ROOT / "prompts"
PROFILES_DIR = CONFIG_PATH.parent / "profiles"
PROFILE_ENV = "HARNESS_PROFILE"
PROFILE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
# серверы vLLM без ключа принимают любое значение, а клиент OpenAI пустое не пропускает
NO_KEY_PLACEHOLDER = "EMPTY"


@dataclass(frozen=True)
class Settings:
    raw: dict[str, Any]
    base_url: str
    api_key: str
    out_path: Path | None
    log_dir: Path
    budget: float
    profile: str | None = None
    llm_ready: bool = True  # ключ API найден; иначе api_key — заглушка и отвечает только этап 0

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


def merge_profile(base: dict[str, Any], overlay: dict[str, Any], path: str = "") -> dict[str, Any]:
    """Профиль поверх базы. Ключ, которого нет в базе, — ошибка (опечатка не должна молча пропасть).
    Пустой словарь в базе (`model_map`) — свободная таблица: профиль задаёт её целиком."""
    out = dict(base)
    for key, value in overlay.items():
        if key not in base:
            raise SystemExit(f"profile key {path}{key!r} is not in config/harness.json")
        if isinstance(base[key], dict) and base[key]:
            if not isinstance(value, dict):
                raise SystemExit(f"profile key {path}{key!r} must be an object")
            out[key] = merge_profile(base[key], value, f"{path}{key}.")
        else:
            out[key] = value
    return out


def resolve_profile(profile: str | None) -> str | None:
    name = profile if profile is not None else os.environ.get(PROFILE_ENV)
    if not name:
        return None
    if not PROFILE_NAME_RE.match(name):
        raise SystemExit(f"bad profile name {name!r}: lowercase letters, digits, '-', '_'")
    if not (PROFILES_DIR / f"{name}.json").is_file():
        known = sorted(f.stem for f in PROFILES_DIR.glob("*.json"))
        raise SystemExit(f"profile {name!r} not found in config/profiles {known}")
    return name


def load_settings_raw(profile: str | None = None) -> dict[str, Any]:
    """config/harness.json (+ профиль стенда, если задан), без ключа API — для офлайн-инструментов."""
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if profile:
        overlay = json.loads((PROFILES_DIR / f"{profile}.json").read_text(encoding="utf-8"))
        overlay.pop("_comment", None)
        raw = merge_profile(raw, overlay)
    return raw


def parse_budget(value: str | None, default: float) -> float:
    """BUDGET из окружения: «100000», «100 000», «100_000», «100k»; не разобралось — умолчание со строкой в stderr
    (падение до старта стоило бы всех задач)."""
    if not value:
        return default
    text = value.strip().lower().replace(" ", "").replace("_", "").replace(",", "")
    scale = 1000.0 if text.endswith("k") else 1.0
    try:
        result = float(text.removesuffix("k")) * scale
    except ValueError:
        result = -1.0
    if not math.isfinite(result) or result <= 0:
        print(f"BUDGET={value!r} not understood, using {default:g}", file=sys.stderr)
        return default
    return result


def load_settings(budget: float | None = None, out: str | None = None, profile: str | None = None) -> Settings:
    """profile: имя из config/profiles; None — из env HARNESS_PROFILE, пусто — без профиля."""
    name = resolve_profile(profile)
    raw = load_settings_raw(name)
    dotenv = _dotenv_values()
    base_url = _find_key(raw["base_url_env_names"], dotenv) or raw["llm_base_url_default"]
    api_key = _find_key(raw["api_key_env_names"], dotenv)
    if not api_key and raw["api_key_optional"]:
        api_key = NO_KEY_PLACEHOLDER
    llm_ready = bool(api_key)
    if not api_key:
        # без ключа этап 0 (math/data/search по шаблону) всё равно отвечает; задачи для LLM харнесс пропустит.
        # Заглушка, а не пустая строка: клиент openjiuwen без api_key не создаётся
        api_key = NO_KEY_PLACEHOLDER
        names = sorted(dotenv)  # только имена, без значений
        state = f".env has vars: {names}" if (ROOT / ".env").exists() else ".env not found"
        print(f"API key not found in env {raw['api_key_env_names']}; {state}; LLM tasks will be skipped",
              file=sys.stderr)
    out_value = out or os.environ.get("OUT")
    log_dir = Path(os.environ.get("HARNESS_LOG_DIR") or (ROOT / raw["log_dir_default"]))
    env_budget = os.environ.get("BUDGET") or os.environ.get("TOKEN_BUDGET")
    final_budget = budget if budget is not None else parse_budget(env_budget, float(raw["budget_default"]))
    return Settings(raw=raw, base_url=base_url.rstrip("/"), api_key=api_key,
                    out_path=Path(out_value) if out_value else None,
                    log_dir=log_dir, budget=final_budget, profile=name, llm_ready=llm_ready)


def load_config(name: str) -> dict[str, Any]:
    return json.loads((CONFIG_PATH.parent / name).read_text(encoding="utf-8"))


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")
