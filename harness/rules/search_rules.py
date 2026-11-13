"""search без LLM: цепочка полей по карточкам kb, когда вопрос и карточки — строго по шаблону."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from harness.rules.common import split_question
from harness.tasks import Task


class SearchRules:
    def __init__(self, cfg: dict[str, Any], tail: list[re.Pattern]):
        self._tail = tail
        self._intro = re.compile(cfg["intro"])
        self._questions = [(re.compile(q["re"]), q["chain"], q["attr"], re.compile(q["answer"]))
                           for q in cfg["questions"]]
        self._field = re.compile(cfg["field"])
        self._max_files = int(cfg["max_files"])
        self._known_fields = {q['attr'] for q in cfg['questions']} | {field for q in cfg['questions'] for field in q['chain']}

    def solve(self, task: Task, corpus=None) -> str | None:
        parts = split_question(task.instruction, self._tail)
        if parts is None or len(parts[0]) != 1:
            return None
        intro = self._intro.match(parts[0][0])
        if not intro:
            return None
        if corpus is None:
            cards = self._cards(task.workspace / intro.group("dir"))
        else:
            prefix = intro.group('dir') + '/'
            if not corpus.complete or not corpus.documents or len(corpus.documents) > self._max_files:
                return None
            if any(not d.path.startswith(prefix) or not d.path.endswith('.md') for d in corpus.documents):
                return None
            cards = self._parse_cards([d.text for d in corpus.documents], strict=True)
        if cards is None:
            return None
        for rx, chain, attr, answer in self._questions:
            m = rx.match(parts[1])
            if m and m.group("p") in cards:
                return self._walk(cards, m.group("p"), chain, attr, answer)
        return None

    @staticmethod
    def _walk(cards: dict[str, dict[str, str]], name: str, chain: list[str], attr: str,
              answer: re.Pattern) -> str | None:
        cur = name
        for rel in chain:
            nxt = cards[cur].get(rel)
            if nxt not in cards:
                return None  # звено ведёт не к карточке — пусть разбирается обычный путь
            cur = nxt
        value = cards[cur].get(attr)
        return value if value is not None and answer.match(value) else None

    def _cards(self, kb: Path) -> dict[str, dict[str, str]] | None:
        """Имя → поля. Любое отступление от формата «# Имя / пусто / Ключ: Значение» — None."""
        if not kb.is_dir():
            return None
        files = sorted(kb.rglob("*"))
        if not files or len(files) > self._max_files or any(not f.is_file() or f.suffix != ".md" for f in files):
            return None
        cards: dict[str, dict[str, str]] = {}
        return self._parse_cards([p.read_text(encoding='utf-8') for p in files])

    def _parse_cards(self, texts: list[str], strict: bool = False) -> dict[str, dict[str, str]] | None:
        cards = {}
        for text in texts:
            lines = [ln.strip() for ln in text.splitlines()]
            lines = [ln for ln in lines if ln]
            if not lines or not lines[0].startswith("# "):
                return None
            name, fields = lines[0][2:].strip(), {}
            for ln in lines[1:]:
                m = self._field.match(ln)
                if not m or m.group("key") in fields:
                    return None
                if strict and m.group('key') not in self._known_fields:
                    return None
                fields[m.group("key")] = m.group("value").strip()
            if name in cards:
                return None
            cards[name] = fields
        return cards
