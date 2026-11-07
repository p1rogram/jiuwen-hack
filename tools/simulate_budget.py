"""Проигрыш журнала прогона при урезанном бюджете: старый планировщик (константы по типу) против модели расхода.

Запуск: python -m tools.simulate_budget <tasks-dir>=<log> [...] [--budgets 0.25 0.5 0.75] [--parallel 5]
Цена, длительность и успех каждой задачи — фактические из журнала. Задачи делятся пополам по task_id, модель
расхода калибруется на другой половине (как в calibrate_cost --eval), чтобы не проверять на обучающих данных.
Перерасход: прокси перестаёт отвечать и запуск обрывается — баллы только за задачи, завершённые до этого.
"""
from __future__ import annotations

import argparse
import heapq
from collections import defaultdict
from dataclasses import dataclass

from harness.cost_model import CostModel
from harness.settings import load_settings_raw
from tools.calibrate_cost import _half, fit
from tools.cost_data import OLD_COSTS, Obs, load_pairs


@dataclass
class SimResult:
    reward: float = 0.0
    spent: float = 0.0
    solved: int = 0
    skipped: int = 0
    overrun: bool = False


def simulate(obs: list[Obs], budget: float, parallel: int, model: CostModel | None) -> SimResult:
    """model=None — старый планировщик: порядок reward / константа, допуск remaining ≥ константа, без резерва."""
    old = lambda o: OLD_COSTS.get(o.task_type, OLD_COSTS["default"])  # noqa: E731
    if model is None:
        queue = sorted(obs, key=lambda o: -(o.reward / old(o)))
    else:
        queue = sorted(obs, key=lambda o: -(o.reward / max(model.estimate(o.task_type, o.prompt_chars).mean, 1.0)))
    res = SimResult()
    deferred: list[Obs] = []
    retried = model is None  # второй проход по отложенным — только у нового (как Harness.run)
    held: dict[str, float] = {}
    running: list[tuple[float, int, Obs]] = []
    now = 0.0
    seq = 0
    while queue or running or (deferred and not retried):
        if not queue and not running:
            queue, deferred, retried = deferred, [], True
        while queue and len(running) < parallel:
            o = queue.pop(0)
            if not o.llm:  # пропущена детектором — 0 токенов у обоих
                continue
            if model is None:
                if budget - res.spent < old(o):
                    res.skipped += 1
                    continue
            else:
                high = model.estimate(o.task_type, o.prompt_chars).high
                if budget - res.spent - sum(held.values()) < high:
                    if retried:
                        res.skipped += 1
                    else:
                        deferred.append(o)
                    continue
                held[o.task_id] = high
            seq += 1
            heapq.heappush(running, (now + max(o.sec, 0.1), seq, o))
        if not running:
            break
        now, _, o = heapq.heappop(running)
        held.pop(o.task_id, None)
        res.spent += o.cost
        if res.spent > budget:
            res.overrun = True
            break
        if o.solved:
            res.reward += o.reward
            res.solved += 1
        if model is not None:
            model.observe(o.task_type, o.prompt_chars, o.in1, o.out1)
    return res


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("pairs", nargs="+", help="<tasks-dir>=<logs/run-*.jsonl>")
    p.add_argument("--budgets", nargs="+", type=float, default=[0.25, 0.5, 0.75])
    p.add_argument("--parallel", type=int, default=int(load_settings_raw()["parallel_sessions"]))
    p.add_argument("--prior-weight", type=float, default=5.0)
    a = p.parse_args()
    w = load_settings_raw()["accounting"]["input_weight"]
    obs = load_pairs(a.pairs)
    by_run: dict[str, list[Obs]] = defaultdict(list)
    for o in obs:
        by_run[o.run].append(o)
    print(f"{'run':<22} {'half':>4} {'budget':>7} {'max':>5} | {'old reward':>10} {'over':>4} | "
          f"{'new reward':>10} {'over':>4} {'skip':>4}")
    totals = defaultdict(lambda: [0.0, 0.0, 0, 0])
    for run, run_obs in sorted(by_run.items()):
        for fold in (0, 1):
            part = [o for o in run_obs if _half(o.task_id) == fold]
            cfg = fit([o for o in obs if _half(o.task_id) != fold], a.prior_weight)
            full = sum(o.cost for o in part)
            best = sum(o.reward for o in part if o.solved)
            for frac in a.budgets:
                b = full * frac
                r_old = simulate(part, b, a.parallel, None)
                r_new = simulate(part, b, a.parallel, CostModel(cfg, w))
                t = totals[frac]
                t[0] += r_old.reward
                t[1] += r_new.reward
                t[2] += r_old.overrun
                t[3] += r_new.overrun
                print(f"{run:<22} {fold:>4} {frac:>7.2f} {best:>5.0f} | {r_old.reward:>10.0f} "
                      f"{'YES' if r_old.overrun else '-':>4} | {r_new.reward:>10.0f} "
                      f"{'YES' if r_new.overrun else '-':>4} {r_new.skipped:>4}")
    print("totals by budget:")
    for frac, (ro, rn, oo, on) in sorted(totals.items()):
        print(f"  {frac:.2f}: old {ro:.0f} (overruns {oo}), new {rn:.0f} (overruns {on})")


if __name__ == "__main__":
    main()
