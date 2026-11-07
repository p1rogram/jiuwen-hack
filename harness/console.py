"""Консоль прогона: подписчик журнала. Строки развилок, задач и 429 — в момент события; живая строка прогресса
(в терминале — через \\r, иначе — обычная строка раз в plain_every_sec); сводка в конце.

Только читает события и BudgetLedger; при сбое записи в поток выключается, прогон идёт дальше.
"""
from __future__ import annotations

import asyncio
import contextlib
import math
import shutil
import sys
import threading
import time
from collections import Counter
from typing import Any, Callable, TextIO

from harness.budget import BudgetLedger
from harness.console_format import LineFormat
from harness.success_model import STAGE2


class ConsoleView:
    def __init__(self, cfg: dict[str, Any], ledger: BudgetLedger, stream: TextIO | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self._cfg = cfg
        self._fmt = LineFormat(cfg)
        self._ledger = ledger
        self._stream = stream if stream is not None else sys.stderr
        self._clock = clock
        self._lock = threading.Lock()
        self.enabled = bool(cfg["enabled"])
        self._tty = self._is_tty()
        self._refresh = float(cfg["refresh_sec"])
        self._plain_every = float(cfg["plain_every_sec"])
        self._fallback = cfg["fallback_chars"]
        self._live = ""  # живая строка, которая сейчас на экране (только в терминале)
        self._last_draw = -math.inf
        self.started = False
        self.total = 0
        self.budget = ledger.total
        self.done = 0
        self.running: set[str] = set()
        self.lam = 0.0
        self.rate_limits = 0
        self.statuses: Counter[str] = Counter()
        self.stage2_tasks: set[str] = set()
        self.rescued = 0

    def _is_tty(self) -> bool:
        try:
            return bool(self._stream.isatty())
        except (AttributeError, ValueError, OSError):
            return False

    # --- события журнала (зовёт JsonlLog под своим замком, в порядке строк файла)

    def __call__(self, rec: dict[str, Any]) -> None:
        handler = getattr(self, f"_on_{rec.get('kind')}", None)
        if handler is not None and self.enabled:
            self._guarded(handler, rec)

    def _guarded(self, fn: Callable[..., None], *args: Any) -> None:
        """Любой сбой консоли (запись, неожиданная запись журнала) — консоль выключена, прогон идёт дальше."""
        try:
            fn(*args)
        except Exception:
            self.enabled = False
            self._live = ""

    def _on_run_start(self, rec: dict[str, Any]) -> None:
        self.started, self.total = True, int(rec["tasks"])
        self.budget = float(rec.get("budget", self.budget))

    def _on_task_start(self, rec: dict[str, Any]) -> None:
        self.running.add(rec["task_id"])
        self.lam = float(rec.get("lambda", self.lam))

    def _on_decision(self, rec: dict[str, Any]) -> None:
        self.lam = float(rec.get("lambda", self.lam))
        if rec.get("choice") == STAGE2:
            self.stage2_tasks.add(rec.get("task_id", ""))
        self._line(self._fmt.decision(rec))

    def _on_task(self, rec: dict[str, Any]) -> None:
        self.done += 1
        self.running.discard(rec.get("task_id", ""))
        self.statuses[rec.get("status", "")] += 1
        if rec.get("solved_by") == STAGE2:
            self.rescued += 1
        self._line(self._fmt.task(rec))

    def _on_rate_limit(self, rec: dict[str, Any]) -> None:
        self.rate_limits += 1
        self._line(self._fmt.rate_limit(rec))

    # --- прогресс

    def progress_text(self) -> str:
        spent, available = self._ledger.spent, self._ledger.available
        held = max(self._ledger.total - spent - available, 0.0)
        return self._fmt.progress(self.done, self.total, len(self.running), spent, held, self.budget,
                                  self.lam, self.rate_limits)

    def tick(self) -> None:
        """Перерисовать прогресс, если пора: в терминале — не чаще refresh_sec, иначе — раз в plain_every_sec."""
        if self.enabled and self.started:
            self._guarded(self._tick)

    def _tick(self) -> None:
        now = self._clock()
        with self._lock:
            if self._tty and now - self._last_draw >= self._refresh:
                self._last_draw = now
                self._draw_live(self._fit(self.progress_text()))
            elif not self._tty and now - self._last_draw >= self._plain_every:
                self._last_draw = now
                self._write(self.progress_text() + "\n")

    @contextlib.asynccontextmanager
    async def live(self):
        """Прогресс на время прогона; живая строка снимается и при исключении — трассировка не склеится с \\r."""
        task = asyncio.create_task(self._ticker())
        try:
            yield self
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):  # сбой тикера не заменит итог прогона
                await task
            self.close()

    async def _ticker(self) -> None:
        while self.enabled:
            self.tick()
            await asyncio.sleep(self._refresh)

    def close(self) -> None:
        with self._lock:
            self._clear_live()

    def summary(self) -> None:
        if self.enabled:
            self._guarded(self._summary)

    def _summary(self) -> None:
        text = self._fmt.summary(self.done, self.total, dict(self.statuses), self._ledger.spent, self.budget,
                                 self.rescued, len(self.stage2_tasks))
        with self._lock:
            self._clear_live()
            self._write(text + "\n")

    # --- вывод

    def _line(self, text: str) -> None:
        with self._lock:
            live = bool(self._live)
            self._clear_live()
            self._write(text + "\n")
            if live:  # живая строка снова внизу, уже с этим событием
                self._draw_live(self._fit(self.progress_text()))

    def _fit(self, text: str) -> str:
        # перенос живой строки \r уже не сотрёт
        cols = shutil.get_terminal_size((int(self._cfg["terminal_width_default"]), 20)).columns
        return text[:max(cols - 1, int(self._cfg["terminal_width_min"]))]

    def _draw_live(self, text: str) -> None:
        pad = " " * max(len(self._live) - len(text), 0)
        if self._write("\r" + text + pad):
            self._live = text

    def _clear_live(self) -> None:
        if self._live:
            self._write("\r" + " " * len(self._live) + "\r")
            self._live = ""

    def _write(self, text: str) -> bool:
        """False — запись не удалась: консоль выключена до конца прогона."""
        if not self.enabled:
            return False
        try:
            self._stream.write(self._encodable(text))
            self._stream.flush()
            return True
        except Exception:  # закрытый поток, кодировка — консоль не должна ронять прогон
            self.enabled = False
            self._live = ""
            return False

    def _encodable(self, text: str) -> str:
        """Символы, которых нет в кодировке потока (cp1251 без «→», «λ»), — замена, а не отказ от строки."""
        enc = getattr(self._stream, "encoding", None) or "utf-8"
        try:
            text.encode(enc)
            return text
        except UnicodeEncodeError:
            for a, b in self._fallback.items():
                text = text.replace(a, b)
            return text.encode(enc, "replace").decode(enc)
        except LookupError:
            return text
