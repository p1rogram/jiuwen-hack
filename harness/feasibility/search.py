"""search: сущность из вопроса есть в kb, и цепочка полей (работодатель → штаб-квартира…) не рвётся.

Пометка hard — только при положительном доказательстве: имени нет нигде в kb; поле есть у других карточек, но не
у нужной; значение-ссылка не ведёт ни к одной карточке. Если вопрос не разобран — сигналов нет (выполнима).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness.feasibility.model import HARD, Signal, questions
from harness.tasks import Task

FIELD_RE = re.compile(r"^\s*[-*]?\s*([A-Za-z][A-Za-z ]{0,30}?)\s*:\s*(\S.*?)\s*$")
CAP_SEQ_RE = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+")


@dataclass
class KnowledgeBase:
    cards: dict[str, dict[str, str]] = field(default_factory=dict)  # имя (lower) → поле (lower) → значение
    text: str = ""          # всё содержимое + имена файлов, lower
    file_keys: set[str] = field(default_factory=set)

    def peer_has_label(self, card: dict[str, str], labels: list[str]) -> bool:
        """Поле есть у карточки того же рода (общее другое поле): у компании нет «born» не потому, что его стёрли,
        а потому, что вопрос разобран не так («Irina Smith's company established» — company не отношение)."""
        own = set(card) - set(labels)
        return any(own & set(other) for other in self.cards.values() if any(lab in other for lab in labels))

    def overlapping(self, value: str) -> list[str]:
        return [n for n in self.cards if re.search(rf"(?<!\w){re.escape(n)}(?!\w)", value)
                or re.search(rf"(?<!\w){re.escape(value)}(?!\w)", n)]

    def mentions(self, name: str) -> bool:
        n = name.lower()
        return n in self.text or n.replace(" ", "_") in self.file_keys or n.replace(" ", "-") in self.file_keys


@dataclass
class Resolution:
    answer: str | None
    signals: list[Signal]


MD_LINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]|\[([^\]]+)\]\([^)]*\)")


def clean_value(value: str) -> str:
    """Значение поля без разметки: [Name](file.md), [[Name]], **Name**, точка в конце."""
    value = MD_LINK_RE.sub(lambda m: m.group(1) or m.group(2), value)
    return value.strip().strip("*_`").strip().rstrip(".;,").strip()


def load_kb(files: list[Path]) -> KnowledgeBase:
    kb = KnowledgeBase()
    parts = []
    for path in files:
        body = path.read_text(encoding="utf-8", errors="replace")
        parts.append(body.lower())
        kb.file_keys.add(path.stem.lower())
        name, fields = None, {}
        for line in body.splitlines():
            if name is None and line.startswith("# "):
                name = line[2:].strip()
                continue
            m = FIELD_RE.match(line)
            if m:
                fields.setdefault(m.group(1).strip().lower(), m.group(2))
        if name:
            kb.cards[name.lower()] = fields
    kb.text = "\n".join(parts) + "\n" + "\n".join(kb.file_keys)
    return kb


class SearchCheck:
    def __init__(self, cfg: dict[str, Any]):
        self._max_files = int(cfg["max_files"])
        self._attrs = cfg["attributes"]
        self._rels = cfg["relations"]
        self._stop = set(cfg["name_stopwords"])

    def check(self, task: Task) -> list[Signal]:
        return self.resolve(task).signals

    def resolve(self, task: Task) -> Resolution:
        files = sorted(task.workspace.rglob("*.md"))
        if not files or len(files) > self._max_files:
            return Resolution(None, [])  # нет kb — сигнал общей проверки
        kb = load_kb(files)
        question = " ".join(questions(task.instruction))
        if not question:
            return Resolution(None, [])
        q = question.lower()
        entity = self._find_entity(kb, q)
        if entity is None:
            # имя из того же словаря, что и карточки (общее имя или фамилия); «Knowledge Base» в вопросе — не сущность
            words = {w for name in kb.cards for w in name.split()}
            unknown = [n for n in self._capitalized(question)
                       if not kb.mentions(n) and words & set(n.lower().split())]
            if unknown:
                return Resolution(None, [Signal("unknown_entity", HARD, f"{unknown[0]!r} is not in kb")])
            return Resolution(None, [])
        start = q.find(entity)
        steps = self._steps(q, start, start + len(entity))
        if not steps:
            return Resolution(None, [])
        if not self._anchored(q, entity, steps[0][0]):
            # «Which person has Chen Wang as a mentor?» — отношение не от этого имени: путь по карточкам ничего не доказывает
            return Resolution(None, [])
        return self._walk(kb, entity, steps)

    @staticmethod
    def _anchored(q: str, entity: str, kw: str) -> bool:
        """Первый шаг цепочки прямо привязан к имени: «X's kw», «kw of X», «X (was|is) kw» (was Irina Smith born)."""
        e, k = re.escape(entity), re.escape(kw)
        return bool(re.search(rf"{e}'s {k}\b|\b{k} of (?:the )?{e}(?!\w)|{e} (?:was |is |were )?{k}\b", q))

    @staticmethod
    def _find_entity(kb: KnowledgeBase, q: str) -> str | None:
        found = [n for n in kb.cards if re.search(rf"(?<![\w]){re.escape(n)}(?![\w])", q)]
        return max(found, key=len) if found else None

    def _capitalized(self, question: str) -> list[str]:
        names = []
        for seq in CAP_SEQ_RE.findall(question):
            words = seq.split()
            while words and words[0] in self._stop:
                words = words[1:]
            if len(words) >= 2:
                names.append(" ".join(words))
        return names

    def _steps(self, q: str, start: int, end: int) -> list[tuple[str, list[str]]]:
        """Отношения после имени по порядку, затем до имени в обратном порядке, затем атрибут."""
        after, before = [], []
        for spec in self._rels.values():
            for kw in spec["keywords"]:
                for m in re.finditer(rf"\b{re.escape(kw)}\b", q):
                    (after if m.start() >= end else before).append((m.start(), kw, spec["labels"]))
        steps = [(kw, labels) for _, kw, labels in sorted(after)]
        steps += [(kw, labels) for _, kw, labels in sorted(before, reverse=True)]
        attr = [(q.find(kw), kw, spec["labels"]) for spec in self._attrs.values() for kw in spec["keywords"]
                if re.search(rf"\b{re.escape(kw)}\b", q)]
        if attr:
            _, kw, labels = min(attr)
            steps.append((kw, labels))
        return steps

    @staticmethod
    def _walk(kb: KnowledgeBase, entity: str, steps: list[tuple[str, list[str]]]) -> Resolution:
        cur = entity
        for i, (kw, labels) in enumerate(steps):
            card = kb.cards[cur]
            value = next((card[lab] for lab in labels if lab in card), None)
            if value is None:
                if kb.peer_has_label(card, labels):
                    return Resolution(None, [Signal("missing_field", HARD, f"{cur!r} has no {kw!r}")])
                return Resolution(None, [])
            value = clean_value(value)
            if i == len(steps) - 1:
                return Resolution(value, [])
            nxt = value.lower()
            if nxt not in kb.cards:
                near = kb.overlapping(nxt)
                if len(near) == 1:
                    cur = near[0]  # «Arktika Labs Inc.» → карточка Arktika Labs
                    continue
                if near:
                    return Resolution(None, [])
                if kb.mentions(value) and value.lower() not in {v.lower() for c in kb.cards.values() for v in c.values()}:
                    return Resolution(None, [])  # есть где-то, но не карточкой — не знаем
                if nxt.replace(" ", "_") in {k.split("_", 1)[-1] for k in kb.file_keys}:
                    return Resolution(None, [])
                return Resolution(None, [Signal("broken_chain", HARD, f"{kw} of {cur!r} = {value!r} has no card")])
            cur = nxt
        return Resolution(None, [])
