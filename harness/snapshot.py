"""Этап 0: компактный срез папки задачи для промпта, без LLM."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from harness.executor import run_python
from harness.tasks import Task

SKIP_NAMES = {"answer.txt", "__pycache__", ".DS_Store"}


class WorkspaceSnapshot:
    def __init__(self, cfg: dict[str, Any]):
        self._max_file = cfg["max_file_chars"]
        self._max_total = cfg["max_total_chars"]
        self._csv_rows = cfg["csv_sample_rows"]
        self._test_chars = cfg["test_output_chars"]

    async def build(self, task: Task) -> str:
        ws = task.workspace
        files = self._list_files(ws)
        if not files:
            return "(no files)"
        parts = ["FILES:\n" + "\n".join(f"{p.relative_to(ws).as_posix()} ({p.stat().st_size} B)" for p in files)]
        parts += await self._test_output(ws, files)
        budget = self._max_total
        for path in self._relevant_files(task, ws, files):
            text = self._render_file(path)
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

    async def _test_output(self, ws: Path, files: list[Path]) -> list[str]:
        tests = [p for p in files if p.name.startswith("test_") and p.suffix == ".py" and p.parent == ws]
        out = []
        for test in tests[:2]:
            res = await run_python([test.name], ws, 60)
            tail = (res.stdout + res.stderr)[-self._test_chars:]
            out.append(f"\n--- output of `python3 {test.name}` (exit {res.returncode}) ---\n{tail}")
        return out

    def _relevant_files(self, task: Task, ws: Path, files: list[Path]) -> list[Path]:
        text_files = [p for p in files if p.suffix in {".py", ".md", ".txt", ".csv", ".json", ".tsv", ".yaml", ".yml"}]
        terms = self._terms(task.instruction)
        if len(text_files) <= 6 or not terms:
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
