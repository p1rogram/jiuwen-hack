"""Калибровка модели расхода (config/cost_model.json) по журналам прогонов и офлайн-оценка её точности.

Запуск: python -m tools.calibrate_cost <tasks-dir>=<log> [...] [--out config/cost_model.json] [--eval] [--keep-stage2]
--eval: задачи делятся пополам по task_id (оба прогона одной задачи — в одной половине), калибровка на одной
половине, проверка на другой и наоборот; сравнение со старыми константами по типу.
"""
from __future__ import annotations

import argparse
import json
import math
import zlib
from collections import defaultdict
from pathlib import Path

from harness.cost_model import CostModel
from harness.settings import load_config, load_prompt, load_settings_raw
from tools.cost_data import OLD_COSTS, Obs, load_pairs

STAGE2_MIN_TYPE = 5  # задач с этапом 2 на тип для своего раздела: меньше — p90 шагов не по чему считать


# высокая оценка выхода этапа 1 — p95 (решение 2026-11-09: при p90 факт выше high у 15.5 % задач, баллы те же)
STAGE1_HIGH_Q = 0.95


def _quantile(values: list[float], q: float) -> float:
    s = sorted(values)
    return s[max(0, math.ceil(q * len(s)) - 1)]


def _p90(values: list[float]) -> float:
    return _quantile(values, 0.9)


def _type_stats(obs: list[Obs]) -> dict:
    outs = [o.out1 for o in obs]
    retries = sum(o.attempts > 1 for o in obs)
    # отношение сумм, а не среднее отношений: длинные промпты весят больше, они и дороже
    cpt = sum(o.prompt_chars for o in obs) / sum(o.in1 for o in obs)
    mean = round(sum(outs) / len(outs), 1)
    # при коротких ответах с редкими длинными квантиль бывает ниже среднего — high не должен быть меньше mean
    return {"chars_per_token": round(cpt, 3), "out_mean": mean,
            "out_high": max(float(_quantile(outs, STAGE1_HIGH_Q)), mean),
            # +0.5 / +1: на сотне задач повторов 0–1, нулевая вероятность врала бы на новом наборе
            "p_retry": round((retries + 0.5) / (len(obs) + 1), 4), "n": len(obs)}


def fit(obs: list[Obs], prior_weight: float, keep_stage2: bool = False) -> dict:
    llm = [o for o in obs if o.llm and o.prompt_chars > 0]
    if len(llm) < 2:
        raise SystemExit("not enough LLM calls with prompt_chars to calibrate")
    raw = load_settings_raw()
    retry_chars = len(load_prompt("stage1_retry.txt").replace("{problem}", "")) + raw["stage1"]["error_tail_chars"]
    by_type: dict[str, list[Obs]] = defaultdict(list)
    for o in llm:
        by_type[o.task_type].append(o)
    types = {t: _type_stats(os_) for t, os_ in sorted(by_type.items())}
    types["default"] = _type_stats(llm)
    stage2 = _shipped_stage2() if keep_stage2 else _stage2(obs)
    return {"stage1": {"retry_prompt_chars": retry_chars, "prior_weight": prior_weight,
                       "types": types}, "stage2": stage2}


def _shipped_stage2() -> dict:
    return load_config(load_settings_raw()["cost_model_file"]).get("stage2", {})


def _stage2(obs: list[Obs]) -> dict:
    """Шаги и выход шага этапа 2 по журналам; нет данных — прежний раздел конфига (с пометкой prior)."""
    runs = [o for o in obs if o.s2_outs]
    if len(runs) < 3:
        return _shipped_stage2()
    types = {"default": _stage2_stats(runs)}
    for t in sorted({o.task_type for o in runs}):
        mine = [o for o in runs if o.task_type == t]
        if len(mine) >= STAGE2_MIN_TYPE:
            types[t] = _stage2_stats(mine)
    return {"prior": False, "types": types}


def _stage2_stats(runs: list[Obs]) -> dict:
    steps = [len(o.s2_outs) for o in runs]
    outs = [x for o in runs for x in o.s2_outs]
    grow = [x for o in runs for x in o.s2_obs_chars]
    return {"steps_mean": round(sum(steps) / len(steps), 2), "steps_high": float(_p90(steps)),
            "step_out_mean": round(sum(outs) / len(outs), 1), "step_out_p90": float(_p90(outs)),
            "obs_chars": round(sum(grow) / len(grow), 1) if grow else 1200.0, "n": len(runs)}


def _half(task_id: str) -> int:
    return zlib.crc32(task_id.encode()) % 2


def evaluate(obs: list[Obs], prior_weight: float, keep_stage2: bool = False) -> dict:
    w = load_settings_raw()["accounting"]["input_weight"]
    rows = []
    for fold in (0, 1):
        train = [o for o in obs if _half(o.task_id) != fold]
        test = [o for o in obs if _half(o.task_id) == fold and o.llm]
        model = CostModel(fit(train, prior_weight, keep_stage2), w)
        for o in test:
            e = model.estimate(o.task_type, o.prompt_chars)
            rows.append((o, e.mean, e.high, OLD_COSTS.get(o.task_type, OLD_COSTS["default"])))
    act = sum(o.cost for o, *_ in rows)
    res = {"tasks": len(rows), "wape_new": sum(abs(m - o.cost) for o, m, _, _ in rows) / act,
           "wape_old": sum(abs(c - o.cost) for o, _, _, c in rows) / act,
           "over_high": sum(o.cost > h for o, _, h, _ in rows) / len(rows), "by_type": {}}
    by_type: dict[str, list] = defaultdict(list)
    for r in rows:
        by_type[r[0].task_type].append(r)
    for t, rs in sorted(by_type.items()):
        a = sum(o.cost for o, *_ in rs)
        res["by_type"][t] = {"n": len(rs), "actual_mean": round(a / len(rs), 1),
                             "wape_new": round(sum(abs(m - o.cost) for o, m, _, _ in rs) / a, 3),
                             "wape_old": round(sum(abs(c - o.cost) for o, _, _, c in rs) / a, 3),
                             "over_high": round(sum(o.cost > h for o, _, h, _ in rs) / len(rs), 3)}
    return res


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("pairs", nargs="+", help="<tasks-dir>=<logs/run-*.jsonl>")
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--prior-weight", type=float, default=5.0)
    p.add_argument("--eval", action="store_true")
    p.add_argument("--keep-stage2", action="store_true",
                   help="раздел stage2 — из текущего config/cost_model.json (сменились только промпты этапа 1)")
    a = p.parse_args()
    obs = load_pairs(a.pairs)
    cfg = fit(obs, a.prior_weight, a.keep_stage2)
    cfg["source"] = [Path(x.rpartition("=")[2]).name for x in a.pairs]
    print(json.dumps(cfg, indent=1, ensure_ascii=False))
    if a.out:
        a.out.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if a.eval:
        r = evaluate(obs, a.prior_weight, a.keep_stage2)
        print(f"eval on held-out halves: tasks {r['tasks']}, WAPE new {r['wape_new']:.3f} vs old {r['wape_old']:.3f}, "
              f"actual > high: {r['over_high']:.1%}")
        for t, s in r["by_type"].items():
            print(f"  {t:<14} n={s['n']:<3} actual {s['actual_mean']:>6}  WAPE new {s['wape_new']:.3f} "
                  f"old {s['wape_old']:.3f}  over_high {s['over_high']:.1%}")


if __name__ == "__main__":
    main()
