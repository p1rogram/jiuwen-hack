"""Неизменяемый допустимый корпус одной задачи; пути являются идентификаторами."""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

SUFFIXES = {".md", ".txt", ".csv", ".json", ".tsv", ".yaml", ".yml"}
EXCLUDED = {"task.json", "answer.txt", "tasks-expected.json", "oracle-rationale.json",
            "manifest.json", "logs", "out", "output", "secrets", "node_modules", "__pycache__"}


@dataclass(frozen=True)
class Document:
    path: str
    text: str
    digest: str
    byte_size: int

    @property
    def lines(self) -> tuple[str, ...]:
        return tuple(self.text.splitlines())

    @property
    def headings(self) -> tuple[str, ...]:
        return tuple(re.findall(r"^#+\s+(.+?)\s*$", self.text, re.MULTILINE))


@dataclass(frozen=True)
class SearchCorpus:
    root: Path
    documents: tuple[Document, ...]
    incomplete: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return not self.incomplete

    @property
    def fingerprint(self) -> str:
        raw = repr([(d.path, d.digest) for d in self.documents]) + repr(self.incomplete)
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, path: str) -> Document | None:
        return next((d for d in self.documents if d.path == path), None)

    def unchanged(self, doc: Document) -> bool:
        try:
            path = (self.root / doc.path).resolve(strict=True)
            if not path.is_relative_to(self.root) or path.stat().st_size != doc.byte_size:
                return False
            with path.open('rb') as stream:
                data = stream.read(doc.byte_size + 1)
            return len(data) == doc.byte_size and hashlib.sha256(data).hexdigest() == doc.digest
        except OSError:
            return False

    @classmethod
    def scan(cls, root: Path, cfg: dict) -> SearchCorpus:
        root = root.resolve()
        docs, errors = [], []
        used, count = 0, 0
        for base, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d.casefold() not in EXCLUDED
                             and not (Path(base) / d).is_symlink()
                             and not (getattr(Path(base) / d, 'is_junction', lambda: False)())
                             and (Path(base) / d).resolve().is_relative_to(root))
            for name in sorted(files):
                p = Path(base) / name
                if (name.startswith('.') or name.casefold() in EXCLUDED or p.suffix.lower() not in SUFFIXES
                        or any(word in name.casefold() for word in ('secret', 'credential', 'private_key'))):
                    continue
                count += 1
                if count > cfg['scan_files']:
                    errors.append('scan_files'); break
                rel = p.relative_to(root).as_posix()
                try:
                    resolved = p.resolve(strict=True)
                    if not resolved.is_relative_to(root) or p.is_symlink():
                        errors.append(f'path:{rel}'); continue
                    remaining = cfg['scan_bytes'] - used
                    with resolved.open('rb') as stream:
                        data = stream.read(remaining + 1)
                    if len(data) > remaining:
                        errors.append('scan_bytes'); break
                    used += len(data)
                    docs.append(Document(rel, data.decode('utf-8-sig'), hashlib.sha256(data).hexdigest(), len(data)))
                except (OSError, UnicodeError):
                    errors.append(f'read:{rel}')
            if 'scan_bytes' in errors or 'scan_files' in errors:
                break
        return cls(root, tuple(docs), tuple(errors))

    def ranked(self, instruction: str) -> list[Document]:
        text = instruction.casefold()
        seeds = {d.path for d in self.documents if any(h.casefold() in text for h in d.headings)}
        linked = set(seeds)
        for _ in range(3):
            for a in self.documents:
                for b in self.documents:
                    if a.path == b.path:
                        continue
                    edge = any(h.casefold() in b.text.casefold() for h in a.headings)
                    if edge and (a.path in linked or b.path in linked):
                        linked.update((a.path, b.path))
        terms = set(re.findall(r'\w{3,}', text))
        return sorted(self.documents, key=lambda d: (
            d.path not in seeds, d.path not in linked,
            -sum(t in (d.path + ' ' + d.text).casefold() for t in terms), d.path))
