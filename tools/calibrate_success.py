"""Калибровка SuccessModel (config/success_model.json): априоры из раздела priors + факт из журналов и разметки.

Запуск: python -m tools.calibrate_success [<tasks-dir>=<log> ...] [--labeled <set-dir> ...] [--out FILE]
- p веток (stage1 по типу, retry и stage2 по виду провала) — по итогам задач в журналах; невыполнимые по разметке
  (_labels.json) не считаются: их провал ничего не говорит о ветке;
- p_feasible по кодам soft — детектор офлайн на размеченных наборах (без LLM): доля выполнимых среди задач,
  у которых есть этот soft-сигнал и нет hard.
Повторный запуск не накапливает счёт: каждый раз — priors + все переданные данные.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from harness.settings import CONFIG_PATH
from harness.success_model import DEFAULT, RETRY, STAGE1, STAGE2
from tools.cost_data import parse_pair
from tools.gen.eval_detector import evaluate

Counts = dict[str, dict[str, list[int]]]  # ветка → ключ → [успехов, всего]


def _add(counts: Counts, branch: str, key: str, ok: bool) -> None:
    for k in dict.fromkeys([key, DEFAULT]):
        cell = counts[branch].setdefault(k, [0, 0])
        cell[0] += int(ok)
        cell[1] += 1


def branch_outcomes(rec: dict) -> list[tuple[str, str, bool]]:
    """(ветка, ключ, успех) по итогу задачи. Журналы до этапа 2 (без solved_by): успех этапа 1 — одна попытка."""
    if rec.get("status") not in ("verified", "unverified"):
        return []
    t, verified = rec.get("type", ""), rec["status"] == "verified"
    if "solved_by" not in rec:
        first_ok = verified and rec.get("attempts", 1) == 1
        out = [(STAGE1, t, first_ok)]
        if not first_ok and rec.get("attempts", 1) > 1:
            out.append((RETRY, "", verified))
        return out
    solved_by = rec["solved_by"]
    out = [] if any(d["failure"] == "forced" for d in rec.get("decisions", [])) else [(STAGE1, t, solved_by == STAGE1)]
    chosen = [d for d in rec.get("decisions", []) if d["choice"] in (RETRY, STAGE2)]
    for i, d in enumerate(chosen):
        last = i == len(chosen) - 1
        key = "" if d["failure"] == "forced" else d["failure"]
        out.append((d["choice"], key, last and verified and solved_by == d["choice"]))
    return out


def log_counts(pairs: list[str]) -> Counts:
    counts: Counts = defaultdict(dict)
    for text in pairs:
        tasks_dir, log = parse_pair(text)
        labels_path = tasks_dir / "_labels.json"
        labels = json.loads(labels_path.read_text(encoding="utf-8")) if labels_path.exists() else {}
        for line in log.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            if rec.get("kind") != "task" or not labels.get(rec["task_id"], {"feasible": True})["feasible"]:
                continue
            for branch, key, ok in branch_outcomes(rec):
                _add(counts, branch, key or DEFAULT, ok)
    return counts


def feasible_counts(set_dirs: list[Path]) -> dict[str, list[int]]:
    counts: dict[str, list[int]] = {}
    for d in set_dirs:
        for r in evaluate(d)["rows"]:
            sev = {s["severity"] for s in r["signals"]}
            if "hard" in sev or "soft" not in sev:
                continue  # hard пропускается и так; без soft p_feasible = 1
            # default не копим: он для кодов, которых в наборах нет, — о них разметка ничего не говорит
            for code in dict.fromkeys(s["code"] for s in r["signals"] if s["severity"] == "soft"):
                cell = counts.setdefault(code, [0, 0])
                cell[0] += int(r["feasible"])
                cell[1] += 1
    return counts


def _posterior(prior: dict[str, dict], key: str, ok: int, n: int) -> dict:
    p = prior.get(key) or prior[DEFAULT]
    return {"alpha": round(p["alpha"] + ok, 3), "beta": round(p["beta"] + n - ok, 3), "n": n}


def fit(priors: dict, counts: Counts, feasible: dict[str, list[int]]) -> dict:
    branches = {b: dict(keys) for b, keys in priors["branches"].items()}
    for b, keys in counts.items():
        prior = priors["branches"].get(b) or priors["branches"][DEFAULT]
        for key, (ok, n) in keys.items():
            branches.setdefault(b, {})[key] = _posterior(prior, key, ok, n)
    p_feasible = dict(priors["p_feasible"])
    for code, (ok, n) in feasible.items():
        p_feasible[code] = _posterior(priors["p_feasible"], code, ok, n)
    return {"priors": priors, "branches": branches, "p_feasible": p_feasible}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("pairs", nargs="*", help="<tasks-dir>=<logs/run-*.jsonl>")
    p.add_argument("--labeled", nargs="*", type=Path, default=[], help="размеченные наборы для p_feasible")
    p.add_argument("--out", type=Path, default=None)
    a = p.parse_args()
    path = CONFIG_PATH.parent / "success_model.json"
    priors = json.loads(path.read_text(encoding="utf-8"))["priors"]
    cfg = fit(priors, log_counts(a.pairs), feasible_counts(a.labeled))
    cfg["source"] = [Path(x.rpartition("=")[2]).name for x in a.pairs] + [d.name for d in a.labeled]
    for b, keys in cfg["branches"].items():
        for k, v in keys.items():
            print(f"{b:<8} {k:<24} p={v['alpha'] / (v['alpha'] + v['beta']):.3f}  n={v.get('n', 'prior')}")
    for k, v in cfg["p_feasible"].items():
        print(f"p_feasible {k:<20} p={v['alpha'] / (v['alpha'] + v['beta']):.3f}  n={v.get('n', 'prior')}")
    if a.out:
        a.out.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
