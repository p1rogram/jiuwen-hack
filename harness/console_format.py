"""Строки консоли из событий журнала: числа с разделителями из конфига, форматы — config/console.json. Без ввода-вывода."""
from __future__ import annotations

from typing import Any

from harness.decision import STOP


class LineFormat:
    def __init__(self, cfg: dict[str, Any]):
        self._f = cfg["formats"]
        self._n = cfg["numbers"]
        self._labels = cfg["status_labels"]
        self._answer_max = int(cfg["answer_max_chars"])
        self._empty = cfg["empty_answer"]
        self._ellipsis = cfg["answer_ellipsis"]

    def num(self, x: float, digits: int) -> str:
        s = f"{float(x):,.{digits}f}"
        # «,» и «.» меняются местами через промежуточный знак: 21,480.5 → 21 480,5
        return s.replace(",", "\0").replace(".", self._n["decimal_sep"]).replace("\0", self._n["thousands_sep"])

    def lam(self, x: float) -> str:
        return f"{float(x):.{int(self._n['lambda_significant'])}g}".replace(".", self._n["decimal_sep"])

    def tokens(self, x: float) -> str:
        return self.num(x, int(self._n["cost_digits"]))

    @staticmethod
    def task_id(rec: dict[str, Any]) -> str:
        # task_id приходит из task.json: управляющие символы (ESC, CR) подделали бы строки консоли
        return "".join(c if c.isprintable() else "?" for c in str(rec.get("task_id", "")))

    def label(self, status: str) -> str:
        return self._labels.get(status, status)

    def progress(self, done: int, total: int, running: int, spent: float, held: float, budget: float,
                 lam: float, rate_limits: int) -> str:
        return self._f["progress"].format(done=done, total=total, running=running,
                                          left=max(total - done - running, 0), spent=self.tokens(spent),
                                          held=self.tokens(held), budget=self.tokens(budget), lam=self.lam(lam),
                                          rate_limits=rate_limits)

    def decision(self, rec: dict[str, Any]) -> str:
        """Выбранная ветка с EMV и лучшая из отвергнутых; не прошедшая резерв помечена."""
        branches = rec.get("branches") or []
        choice = rec.get("choice", STOP)
        base = {"task_id": self.task_id(rec), "failure": rec.get("failure", ""), "choice": choice}
        chosen = next((b for b in branches if b["name"] == choice), None)
        others = sorted((b for b in branches if b is not chosen), key=lambda b: -b["emv"])
        other = others[0] if others else None
        mark = {"other": other["name"], "other_emv": self._emv(other),
                "other_mark": self._f["no_fit_mark"] if other.get("fits") is False else ""} if other else {}
        if chosen is None:
            return self._f["decision_stop"].format(**base, **mark) if other else \
                self._f["decision_stop_empty"].format(**base)
        if other is None:
            return self._f["decision_single"].format(**base, emv=self._emv(chosen))
        # отвергнутая с большим EMV не прошла резерв (или без цены) — знак «>» был бы неверен
        key = "decision_alt" if other["emv"] > chosen["emv"] else "decision"
        return self._f[key].format(**base, emv=self._emv(chosen), **mark)

    def _emv(self, branch: dict[str, Any]) -> str:
        return self.num(branch["emv"], int(self._n["emv_digits"]))

    def task(self, rec: dict[str, Any]) -> str:
        answer = rec.get("answer") or ""
        shown = repr(answer[:self._answer_max]) + (self._ellipsis if len(answer) > self._answer_max else "") \
            if answer else self._empty
        stage2 = self._f["task_stage2"].format(steps=rec.get("stage2_steps", 0), stop=rec["stage2_stop"]) \
            if rec.get("stage2_stop") else ""
        return self._f["task"].format(task_id=self.task_id(rec), status=self.label(rec.get("status", "")),
                                      cost=self.tokens(rec.get("cost", 0)), answer=shown, stage2=stage2)

    def rate_limit(self, rec: dict[str, Any]) -> str:
        return self._f["rate_limit"].format(wait=self.num(rec.get("wait_sec", 0), int(self._n["wait_digits"])),
                                            task_id=self.task_id(rec))

    def summary(self, done: int, total: int, statuses: dict[str, int], spent: float, budget: float,
                rescued: int, stage2: int) -> str:
        parts = self._f["summary_sep"].join(self._f["summary_status"].format(label=self.label(s), n=n)
                                            for s, n in sorted(statuses.items(), key=lambda x: -x[1]))
        return self._f["summary"].format(done=done, total=total, statuses=parts, spent=self.tokens(spent),
                                         budget=self.tokens(budget), rescued=rescued, stage2=stage2)
