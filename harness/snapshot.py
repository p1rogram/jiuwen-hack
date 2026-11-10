"""Этап 0: компактный срез папки задачи для промпта, без LLM."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from harness.executor import run_python
from harness.kb_slice import kb_slice
from harness.tasks import Task
from harness.test_output import compact_unittest
from harness.test_slice import drop_passing, failed_tests

SKIP_NAMES = {"answer.txt", "__pycache__", ".DS_Store"}
TEXT_SUFFIXES = {".py", ".md", ".txt", ".csv", ".json", ".tsv", ".yaml", ".yml"}


class WorkspaceSnapshot:
    def __init__(self, cfg: dict[str, Any]):
        self._max_file = cfg["max_file_chars"]
        self._max_total = cfg["max_total_chars"]
        self._csv_rows = cfg["csv_sample_rows"]
        self._test_chars = cfg["test_output_chars"]
        self._kb_min = cfg["kb_min_files"]
        self._kb_depth = cfg["kb_follow_depth"]
        self._kb_max = cfg["kb_max_files"]
        self._kb_note = cfg["kb_omitted_note"]
        self._compact = bool(cfg.get("compact_test_output", False))
        self._trim_note = cfg.get("passing_tests_note") if cfg.get("trim_passing_tests", False) else None

    async def build(self, task: Task) -> str:
        ws = task.workspace
        files = self._list_files(ws)
        if not files:
            return "(no files)"
        text_files = [p for p in files if p.suffix in TEXT_SUFFIXES]
        narrow = (kb_slice(task.instruction, text_files, self._kb_depth, self._kb_max)
                  if len(text_files) > self._kb_min else None)
        listed = files if narrow is None else narrow
        listing = "\n".join(f"{p.relative_to(ws).as_posix()} ({p.stat().st_size} B)" for p in listed)
        if narrow is not None:
            listing += "\n" + self._kb_note.format(n=len(files) - len(narrow))
        parts = ["FILES:\n" + listing]
        outputs, failed = await self._test_output(ws, files)
        parts += outputs
        budget = self._max_total
        for path in narrow if narrow is not None else self._relevant_files(task, text_files):
            text = self._render_file(path)
            if path in failed:
                text = drop_passing(text, failed[path], self._trim_note)
            block = f"\n--- {path.relative_to(ws).as_posix()} ---\n{text}"
            if len(block) > budget:
                parts.append(f"\n(further files omitted, context limit)")
                break
            parts.append(block)
            budget -= len(block)
        return "\n".join(parts)

    @staticmethod
    def _list_files(ws: Path) -> list[Path]:
        if not ws.is_dir():
            return []
        return sorted(p for p in ws.rglob("*")
                      if p.is_file() and not (set(p.parts) & SKIP_NAMES) and p.name != "task.json")

    async def _test_output(self, ws: Path, files: list[Path]) -> tuple[list[str], dict[Path, set[str]]]:
        """Вывод тестов для среза и упавшие тесты по файлу (для среза тест-файла, если включён)."""
        tests = [p for p in files if p.name.startswith("test_") and p.suffix == ".py" and p.parent == ws]
        out, failed = [], {}
        for test in tests[:2]:
            res = await run_python([test.name], ws, 60)
            raw = res.stdout + res.stderr
            tail = (compact_unittest(raw) if self._compact else raw)[-self._test_chars:]
            out.append(f"\n--- output of `python3 {test.name}` (exit {res.returncode}) ---\n{tail}")
            names = failed_tests(raw) if self._trim_note else None
            if names:
                failed[test] = names
        return out, failed

    def _relevant_files(self, task: Task, text_files: list[Path]) -> list[Path]:
        terms = self._terms(task.instruction)
        if len(text_files) <= self._kb_min or not terms:
            return text_files
        # много файлов (база знаний): сначала те, где встречаются имена из вопроса
        scored = []
        for p in text_files:
            body = p.read_text(encoding="utf-8", errors="replace").lower()
            score = sum(1 for t in terms if t in body or t in p.name.lower())
            scored.append((score, p))
        scored.sort(key=lambda sp: -sp[0])
        return [p for _, p in scored]

    @staticmethod
    def _terms(instruction: str) -> list[str]:
        words = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*", instruction)
        stop = {"The", "In", "How", "Use", "Work", "When", "Run", "Fix", "What", "Which", "Who", "Compute", "Then", "It", "A"}
        terms = set()
        for w in words:
            if w in stop:
                continue
            terms.add(w.lower())
            terms.update(x.lower() for x in w.split() if x not in stop and len(x) > 2)
        return sorted(terms)

    def _render_file(self, path: Path) -> str:
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.suffix in {".csv", ".tsv"}:
            lines = text.splitlines()
            head = "\n".join(lines[: self._csv_rows + 1])
            return f"{head}\n... ({max(len(lines) - 1, 0)} data rows total)"
        if len(text) > self._max_file:
            return text[: self._max_file] + f"\n... (truncated, {len(text)} chars total)"
        return text
