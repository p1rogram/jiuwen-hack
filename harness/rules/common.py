"""Общий разбор условия для решателей по правилам."""
from __future__ import annotations

import re

from harness.feasibility.model import SENTENCE_RE


def split_question(instruction: str, tail: list[re.Pattern]) -> tuple[list[str], str] | None:
    """Предложения до вопроса и сам вопрос. Ровно один вопрос, после него — только служебный хвост по шаблонам
    `tail` (инструмент, answer.txt): любое другое предложение может менять ответ («Then it receives…», «modulo 7»)."""
    sentences = [s.strip() for s in SENTENCE_RE.split(instruction.strip()) if s.strip()]
    asked = [i for i, s in enumerate(sentences) if s.endswith("?")]
    if len(asked) != 1:
        return None
    i = asked[0]
    if not all(any(rx.match(s) for rx in tail) for s in sentences[i + 1:]):
        return None
    return sentences[:i], sentences[i]
