"""Журнал вызовов LLM, развилок и итогов задач (JSONL) — сырьё для модели расхода; подписчики видят те же записи."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

Subscriber = Callable[[dict[str, Any]], None]


class JsonlLog:
    def __init__(self, path: Path, subscribers: list[Subscriber] | None = None):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._lock = threading.Lock()
        self._subscribers = list(subscribers or [])

    def subscribe(self, fn: Subscriber) -> None:
        with self._lock:
            self._subscribers.append(fn)

    def write(self, record: dict[str, Any]) -> None:
        record = {"ts": round(time.time(), 3), **record}
        line = json.dumps(record, ensure_ascii=False)
        # подписчики — под тем же замком: их порядок событий = порядок строк в файле
        with self._lock:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
            for fn in list(self._subscribers):
                try:
                    fn(record)
                except Exception:  # подписчик только смотрит: его сбой не должен останавливать прогон
                    self._subscribers.remove(fn)
