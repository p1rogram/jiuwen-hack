"""Общий разбор условия для решателей по правилам."""
from __future__ import annotations

from harness.feasibility.model import SENTENCE_RE


def split_question(instruction: str) -> tuple[list[str], str] | None:
    """Предложения до вопроса и сам вопрос. Ровно один вопрос: после него — только служебный хвост
    (инструменты, answer.txt); второй вопрос — условие не по шаблону."""
    sentences = [s.strip() for s in SENTENCE_RE.split(instruction.strip()) if s.strip()]
    asked = [i for i, s in enumerate(sentences) if s.endswith("?")]
    if len(asked) != 1:
        return None
    i = asked[0]
    return sentences[:i], sentences[i]
