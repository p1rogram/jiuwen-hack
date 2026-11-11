"""Контракт независимого набора; эталоны и доказательства отдельно от задач."""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Case:
    category: str
    family: str
    instruction: str
    expected: str | None
    evidence: str
    files: dict[str, str] = field(default_factory=dict)
    oracle: dict[str, Any] = field(default_factory=dict)
    reference: str = ""
    hidden: str = ""
    partial: str = ""

    @property
    def feasible(self):
        return self.expected is not None
