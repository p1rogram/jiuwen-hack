"""Замер: по одному вызову каждой модели на задаче-примере; сырые usage, reasoning, время.

Запуск: python tools/bench_models.py [--task DIR] [--models m1 m2 ...]
Ключ берётся из env/.env кодом и не печатается.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.executor import run_script  # noqa: E402
from harness.settings import load_prompt, load_settings  # noqa: E402
from harness.snapshot import WorkspaceSnapshot  # noqa: E402
from harness.solver import extract_code  # noqa: E402
from harness.tasks import load_tasks  # noqa: E402
from harness.verifier import last_line  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def get_json(url: str, key: str) -> dict:
    r = requests.get(url, headers={"Authorization": f"Bearer {key}"}, timeout=30)
    return {"status": r.status_code, "body": r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text[:300]}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--task", type=Path, default=ROOT / "data/task-examples/task-dir/math_0000")
    p.add_argument("--models", nargs="*", default=None)
    p.add_argument("--max-tokens", type=int, default=6000)
    args = p.parse_args()

    s = load_settings()
    models = args.models or s.section("models")["all"]
    out_path = s.log_dir / f"bench-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    info = get_json(f"{s.base_url}/models", s.api_key)
    if info["status"] == 200:
        rows = [{"id": m.get("id"), "limit": m.get("limit")} for m in info["body"].get("data", [])]
        print("models visible:", json.dumps([r for r in rows if r["id"] in models], ensure_ascii=False))
    else:
        print("GET /models:", info)
    print("GET /limits:", json.dumps(get_json(f"{s.base_url}/limits", s.api_key), ensure_ascii=False)[:800])

    task = next(t for t in load_tasks(args.task.parent) if t.task_dir == args.task)
    snap = asyncio.run(WorkspaceSnapshot(s.section("snapshot")).build(task))
    messages = [
        {"role": "system", "content": load_prompt("system.txt")},
        {"role": "user", "content": load_prompt("stage1_user.txt").format(instruction=task.instruction, snapshot=snap)},
    ]
    expected = json.loads((task.task_dir / "task.json").read_text(encoding="utf-8")).get("expected")

    for model in models:
        body = {"model": model, "messages": messages, "temperature": 0.0, "max_tokens": args.max_tokens}
        t0 = time.monotonic()
        r = requests.post(f"{s.base_url}/chat/completions", json=body,
                          headers={"Authorization": f"Bearer {s.api_key}"}, timeout=300)
        dt = time.monotonic() - t0
        rec = {"model": model, "task_id": task.task_id, "http": r.status_code, "sec": round(dt, 2)}
        if r.status_code != 200:
            rec["error"] = r.text[:500]
            rec["headers"] = {k: v for k, v in r.headers.items() if k.lower().startswith(("x-", "retry"))}
        else:
            data = r.json()
            msg = data["choices"][0]["message"]
            content = msg.get("content") or ""
            reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
            code = extract_code(content)
            answer = ""
            if code:
                # скрипт модели пишет answer.txt и правит файлы — запускаем во временной копии, не в исходнике
                with tempfile.TemporaryDirectory() as tmp:
                    copy = Path(tmp) / task.task_dir.name
                    shutil.copytree(task.task_dir, copy)
                    ws = copy / task.workspace.relative_to(task.task_dir)
                    run = asyncio.run(run_script(code, ws, 30))
                answer = last_line(run.stdout) if run.ok else f"ERR {run.stderr[-200:]}"
            rec.update({
                "usage": data.get("usage"), "finish_reason": data["choices"][0].get("finish_reason"),
                "content_chars": len(content), "reasoning_chars": len(reasoning),
                "message_keys": sorted(msg.keys()), "answer": answer, "expected": expected,
                "content_head": content[:400],
            })
        with out_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        u = rec.get("usage") or {}
        print(f"{model:<28} http={rec['http']} {rec['sec']:>6}s in={u.get('prompt_tokens')} out={u.get('completion_tokens')} "
              f"details={u.get('completion_tokens_details')} reasoning_chars={rec.get('reasoning_chars')} "
              f"answer={rec.get('answer')!r} expected={expected!r}")
        time.sleep(1.5)
    print("saved", out_path)


if __name__ == "__main__":
    main()
