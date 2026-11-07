"""Проигрыш журнала прогона при урезанном бюджете: старый планировщик (константы по типу) против модели расхода.

Запуск: python -m tools.simulate_budget <tasks-dir>=<log> [...] [--budgets 0.25 0.5 0.75] [--parallel 5]
Цена, длительность и успех каждой задачи — фактические из журнала. Задачи делятся пополам по task_id, модель
расхода калибруется на другой половине (как в calibrate_cost --eval), чтобы не проверять на обучающих данных.
Перерасход: прокси перестаёт отвечать и запуск обрывается — баллы только за задачи, завершённые до этого.
Третий вариант (emv) — DecisionPolicy: порядок reward · p / E[цена], пропуск soft-задач с низкой выгодой, а этап 2
из журнала оплачивается, только если при λ на момент старта задачи политика его выбрала бы (иначе его цена
вычитается, а спасённая им задача считается нерешённой).
"""
from __future__ import annotations

import argparse
import heapq
from collections import defaultdict
from dataclasses import dataclass

from pathlib import Path

from harness.budget import BudgetLedger
from harness.cost_model import CostModel
from harness.decision import DecisionPolicy
from harness.feasibility import SOFT, Signal
from harness.prompting import Prepared
from harness.settings import load_config, load_settings_raw
from harness.success_model import STAGE2, SuccessModel
from harness.tasks import Task
from tools.calibrate_cost import _half, fit
from tools.cost_data import OLD_COSTS, Obs, load_pairs


@dataclass
class SimResult:
    reward: float = 0.0
    spent: float = 0.0
    solved: int = 0
    skipped: int = 0
    overrun: bool = False


def _prepared(o: Obs) -> Prepared:
    task = Task(o.task_id, o.task_type, "", o.reward, "", Path("."))
    return Prepared(task, [{"role": "user", "content": "x" * o.prompt_chars}], [Signal(c, SOFT, "") for c in o.soft])


def make_policy(model: CostModel, success: SuccessModel) -> DecisionPolicy:
    # резерв ветки симуляция ведёт сама (held), у политики — бесконечный бюджет: нужен только выбор по EMV
    return DecisionPolicy(success, model, BudgetLedger(float("inf")))


def simulate(obs: list[Obs], budget: float, parallel: int, model: CostModel | None,
             policy: DecisionPolicy | None = None) -> SimResult:
    """model=None — старый планировщик: порядок reward / константа, допуск remaining ≥ константа, без резерва.

    policy — вариант emv (model должна быть та же, что у policy).
    """
    old = lambda o: OLD_COSTS.get(o.task_type, OLD_COSTS["default"])  # noqa: E731
    res = SimResult()
    preps = {o.task_id: _prepared(o) for o in obs}
    if model is None:
        queue = sorted(obs, key=lambda o: -(o.reward / old(o)))
    elif policy is None:
        queue = sorted(obs, key=lambda o: -(o.reward / max(model.estimate(o.task_type, o.prompt_chars).mean, 1.0)))
    else:
        queue = sorted(obs, key=lambda o: -policy.priority(preps[o.task_id]))
        lam0 = policy.lambda_([preps[o.task_id] for o in queue if o.llm], budget)
        low = {o.task_id for o in queue if o.llm and policy.skip_soft(preps[o.task_id], lam0)}
        res.skipped += len(low)
        queue = [o for o in queue if o.task_id not in low]
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
            cost, solved = o.cost, o.solved
            if policy is not None and o.s2_cost > 0:
                lam = policy.lambda_([preps[q.task_id] for q in queue if q.llm],
                                     budget - res.spent - sum(held.values()))
                if policy.branch(preps[o.task_id], o.s2_failure, lam, [STAGE2]).choice != STAGE2:
                    cost, solved = o.cost - o.s2_cost, o.solved and o.solved_by != STAGE2
            seq += 1
            heapq.heappush(running, (now + max(o.sec, 0.1), seq, o, cost, solved))
        if not running:
            break
        now, _, o, cost, solved = heapq.heappop(running)
        held.pop(o.task_id, None)
        res.spent += cost
        if res.spent > budget:
            res.overrun = True
            break
        if solved:
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
    success_cfg = load_config(load_settings_raw()["success_model_file"])
    print(f"{'run':<22} {'half':>4} {'budget':>7} {'max':>5} | {'old reward':>10} {'over':>4} | "
          f"{'new reward':>10} {'over':>4} {'skip':>4} | {'emv reward':>10} {'over':>4} {'skip':>4}")
    totals = defaultdict(lambda: [0.0, 0.0, 0, 0, 0.0, 0])
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
                m = CostModel(cfg, w)
                r_emv = simulate(part, b, a.parallel, m, make_policy(m, SuccessModel(success_cfg)))
                t = totals[frac]
                t[0] += r_old.reward
                t[1] += r_new.reward
                t[2] += r_old.overrun
                t[3] += r_new.overrun
                t[4] += r_emv.reward
                t[5] += r_emv.overrun
                print(f"{run:<22} {fold:>4} {frac:>7.2f} {best:>5.0f} | {r_old.reward:>10.0f} "
                      f"{'YES' if r_old.overrun else '-':>4} | {r_new.reward:>10.0f} "
                      f"{'YES' if r_new.overrun else '-':>4} {r_new.skipped:>4} | {r_emv.reward:>10.0f} "
                      f"{'YES' if r_emv.overrun else '-':>4} {r_emv.skipped:>4}")
    print("totals by budget:")
    for frac, (ro, rn, oo, on, re_, oe) in sorted(totals.items()):
        print(f"  {frac:.2f}: old {ro:.0f} (overruns {oo}), new {rn:.0f} (overruns {on}), "
              f"emv {re_:.0f} (overruns {oe})")


if __name__ == "__main__":
    main()
