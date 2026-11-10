"""Прогон харнесса по нескольким наборам задач и сводка метрик — для стенда (DGX Spark) и локальных замеров.

Набор — папка `<sets>/<name>/` с подпапкой `tasks/` (папки задач с task.json). Эталон, по убыванию приоритета:
`tasks-expected.json` рядом с `tasks/` (+ `tasks-hidden/` со скрытыми тестами coding) — наборы вариантов;
`tasks/_labels.json` — сгенерированные наборы; поле `expected` в task.json — примеры организаторов.

    python -m tools.suite run --sets testsets --out results/r1 [--only a b] [--repeat 2]
                              [--mode native|docker|apptainer] [--image IMG] [--profile spark] [--budget N]
    python -m tools.suite score results/r1        # пересчитать сводку по готовым прогонам

Каждый прогон работает с копией набора (харнесс пишет answer.txt в папки задач), исходные наборы не меняются.
Итог — `summary.md` и `summary.json` в папке результатов.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.eval_answers import judge  # noqa: E402
from tools.gen.score_variants import verdict as variant_verdict  # noqa: E402
from tools.suite_report import ANSWERED_INF, FALSE_SKIP, OK, SKIP_OK, WRONG, render  # noqa: E402


def discover(sets: Path, only: list[str] | None) -> list[Path]:
    found = sorted(p for p in sets.iterdir() if (p / "tasks").is_dir())
    if only:
        missing = sorted(set(only) - {p.name for p in found})
        if missing:
            raise SystemExit(f"no such sets in {sets}: {missing}; known {[p.name for p in found]}")
        found = [p for p in found if p.name in only]
    return found


def harness_command(args: argparse.Namespace, run_dir: Path) -> tuple[list[str], dict[str, str]]:
    tasks = run_dir / "tasks"
    if args.mode == "native":
        cmd = [sys.executable, "-X", "utf8", str(ROOT / "run_agent.py"), "--tasks-dir", str(tasks)]
        if args.profile:
            cmd += ["--profile", args.profile]
        env = dict(os.environ, HARNESS_LOG_DIR=str(run_dir / "out" / "logs"), OUT=str(run_dir / "out" / "answers.json"))
        if args.budget:
            env["BUDGET"] = str(args.budget)
        return cmd, env
    cmd = [sys.executable, str(ROOT / "tools" / "docker_run.py"), str(tasks), str(run_dir / "out"),
           "--runtime", args.mode, "--image", args.image]
    if args.profile:
        cmd += ["--profile", args.profile]
    if args.budget:
        cmd += ["--budget", str(args.budget)]
    return cmd, dict(os.environ)


def run_all(args: argparse.Namespace) -> None:
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    for src in discover(args.sets, args.only):
        for rep in range(1, args.repeat + 1):
            run_dir = out / (src.name if args.repeat == 1 else f"{src.name}-r{rep}")
            if (run_dir / "run.json").exists():
                print(f"skip {run_dir.name}: already done", file=sys.stderr)
                continue
            if run_dir.exists():
                # прогон прервали до run.json: копия набора уже с answer.txt — начинаем заново
                shutil.rmtree(run_dir)
            shutil.copytree(src, run_dir)
            (run_dir / "out").mkdir()
            cmd, env = harness_command(args, run_dir)
            print(f"== {run_dir.name}: {len(list((run_dir / 'tasks').glob('*/task.json')))} tasks", file=sys.stderr)
            started = time.monotonic()
            with open(run_dir / "stderr.txt", "wb") as err:
                code = subprocess.call(cmd, env=env, stdout=err, stderr=subprocess.STDOUT)
            meta = {"set": src.name, "repeat": rep, "mode": args.mode, "profile": args.profile,
                    "exit_code": code, "wall_sec": round(time.monotonic() - started, 1)}
            if code != 0 and not read_log(run_dir / "out")[0]:
                # сервер недоступен, нет модели, нет образа — остальные наборы упадут так же
                tail = (run_dir / "stderr.txt").read_text(encoding="utf-8", errors="replace")[-1500:]
                raise SystemExit(f"{run_dir.name}: harness exited {code} before any task:\n{tail}")
            (run_dir / "run.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
            row = score_run(run_dir)
            print(f"   {row['solved']}/{row['feasible']} solved, tokens {row['cost']}, "
                  f"{meta['wall_sec']} s, exit {code}", file=sys.stderr)
    write_summary(out)


def read_log(out: Path) -> tuple[dict[str, dict], dict[str, dict]]:
    tasks: dict[str, dict] = {}
    calls: dict[str, dict] = defaultdict(lambda: {"cost": 0.0, "in": 0, "out": 0, "n": 0})
    for log in sorted((out / "logs").glob("*.jsonl")):
        for line in log.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            if rec.get("kind") == "task":
                tasks[rec["task_id"]] = rec
            elif rec.get("kind") == "llm_call":
                c = calls[rec.get("task_id", "?")]
                c["cost"] += rec.get("cost", 0.0)
                c["in"] += rec.get("input_tokens", 0)
                c["out"] += rec.get("output_tokens", 0)
                c["n"] += 1
    return tasks, calls


def truth(run_dir: Path) -> tuple[str, dict]:
    exp = run_dir / "tasks-expected.json"
    if exp.exists():
        return "variants", json.loads(exp.read_text(encoding="utf-8"))
    labels = run_dir / "tasks" / "_labels.json"
    if labels.exists():
        return "labels", json.loads(labels.read_text(encoding="utf-8"))
    return "examples", {}


def task_verdict(kind: str, ref: dict, task_dir: Path, data: dict, status: str) -> tuple[str, str | None, bool]:
    """(вердикт, ответ, выполнима)."""
    tid = data["task_id"]
    if kind == "variants":
        v, answer = variant_verdict(task_dir.parent, tid, ref[tid])
        feasible = bool(ref[tid]["feasible"])
    else:
        feasible = bool(ref.get(tid, {}).get("feasible", True))
        ans_file = task_dir / "answer.txt"
        answer = ans_file.read_text(encoding="utf-8").strip() if ans_file.exists() else None
        if not feasible:
            v = SKIP_OK if answer is None else ANSWERED_INF
        elif data.get("check") == "unittest":
            ok, answer = judge(task_dir, data)  # тесты в workspace копии набора
            v = OK if ok else WRONG
        else:
            # strip + lower — так сравнивают организаторы (встреча 10.11)
            want = str(ref.get(tid, {}).get("expected", data.get("expected", ""))).strip().lower()
            v = OK if answer is not None and answer.lower() == want else WRONG
    if feasible and v != OK and status == "skipped_infeasible":
        v = FALSE_SKIP
    return v, answer, feasible


def score_run(run_dir: Path) -> dict:
    kind, ref = truth(run_dir)
    tasks, calls = read_log(run_dir / "out")
    rows = []
    for tj in sorted((run_dir / "tasks").glob("*/task.json")):
        data = json.loads(tj.read_text(encoding="utf-8-sig"))
        rec = tasks.get(data["task_id"], {})
        status = rec.get("status", "missing")
        v, answer, feasible = task_verdict(kind, ref, tj.parent, data, status)
        c = calls.get(data["task_id"], {"cost": 0.0, "in": 0, "out": 0, "n": 0})
        expected = ref.get(data["task_id"], {}).get("expected", data.get("expected"))
        rows.append({"task_id": data["task_id"], "type": data.get("task_type", "?"),
                     "reward": float(data.get("reward", 1)), "feasible": feasible, "verdict": v,
                     "answer": answer, "expected": expected, "status": status,
                     "failure": rec.get("failure", ""), "solved_by": rec.get("solved_by", ""),
                     "cost": round(c["cost"], 1), "in": c["in"], "out": c["out"], "calls": c["n"]})
    meta_file = run_dir / "run.json"
    meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
    by_type = {}
    for t in sorted({r["type"] for r in rows}):
        rs = [r for r in rows if r["type"] == t]
        by_type[t] = {"solved": sum(r["verdict"] == OK for r in rs), "feasible": sum(r["feasible"] for r in rs),
                      "cost": round(sum(r["cost"] for r in rs), 1)}
    feasible = [r for r in rows if r["feasible"]]
    return {"run": run_dir.name, "set": meta.get("set", run_dir.name), "truth": kind, "tasks": len(rows),
            "feasible": len(feasible), "solved": sum(r["verdict"] == OK for r in rows),
            "reward": sum(r["reward"] for r in rows if r["verdict"] == OK),
            "reward_max": sum(r["reward"] for r in feasible),
            "cost": round(sum(r["cost"] for r in rows), 1), "input_tokens": sum(r["in"] for r in rows),
            "output_tokens": sum(r["out"] for r in rows), "llm_calls": sum(r["calls"] for r in rows),
            "cost_on_infeasible": round(sum(r["cost"] for r in rows if not r["feasible"]), 1),
            "verdicts": dict(Counter(r["verdict"] for r in rows)),
            "statuses": dict(Counter(r["status"] for r in rows)),
            "wall_sec": meta.get("wall_sec"), "exit_code": meta.get("exit_code"),
            "by_type": by_type, "rows": rows}


def write_summary(out: Path) -> dict:
    runs = [score_run(d) for d in sorted(out.iterdir()) if (d / "tasks").is_dir() and (d / "out").is_dir()]
    total = {k: round(sum(r[k] for r in runs), 1) for k in ("tasks", "feasible", "solved", "reward", "reward_max",
                                                              "cost", "input_tokens", "output_tokens", "llm_calls")}
    summary = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "total": total, "runs": runs}
    (out / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
    md = render(summary)
    (out / "summary.md").write_text(md, encoding="utf-8")
    print(md)
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="прогнать наборы и собрать метрики")
    r.add_argument("--sets", type=Path, required=True, help="папка с наборами (<name>/tasks/...)")
    r.add_argument("--out", type=Path, required=True, help="папка результатов (новая или с частью прогонов)")
    r.add_argument("--only", nargs="*", default=None, help="только эти наборы")
    r.add_argument("--repeat", type=int, default=1, help="сколько раз прогнать каждый набор")
    r.add_argument("--mode", choices=["native", "docker", "apptainer"], default="native")
    r.add_argument("--image", default="jiuwen-harness:dev", help="тег Docker или путь к .sif")
    r.add_argument("--profile", default=None, help="профиль из config/profiles, например spark")
    r.add_argument("--budget", default=None)
    s = sub.add_parser("score", help="пересчитать summary по готовой папке результатов")
    s.add_argument("out", type=Path)
    return p.parse_args(argv)


def main() -> None:
    args = parse_args()
    if args.cmd == "run":
        run_all(args)
    else:
        write_summary(args.out)


if __name__ == "__main__":
    main()
