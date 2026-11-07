"""Журнал вызовов LLM и итогов задач (JSONL) — сырьё для модели расхода."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any


class JsonlLog:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._lock = threading.Lock()

    def write(self, record: dict[str, Any]) -> None:
        record = {"ts": round(time.time(), 3), **record}
        line = json.dumps(record, ensure_ascii=False)
        with self._lock, self._path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
