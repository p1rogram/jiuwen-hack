"""Локальная оценка: сравнивает answer.txt с expected из task.json (только для примеров, где expected виден).

Запуск: python tools/eval_answers.py <tasks-dir>
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def judge(task_dir: Path, data: dict) -> tuple[bool, str]:
    ans_file = task_dir / "answer.txt"
    answer = ans_file.read_text(encoding="utf-8").strip() if ans_file.exists() else ""
    if data.get("check") == "unittest":
        ws = task_dir / "workspace"
        ok = all(subprocess.run([sys.executable, t.name], cwd=ws, capture_output=True).returncode == 0
                 for t in ws.glob("test_*.py"))
        return ok and answer == "DONE", answer
    return answer == str(data.get("expected", "")).strip(), answer


def main() -> None:
    root = Path(sys.argv[1])
    total = got = 0.0
    for tj in sorted(root.glob("*/task.json")):
        data = json.loads(tj.read_text(encoding="utf-8"))
        ok, answer = judge(tj.parent, data)
        total += data.get("reward", 0)
        got += data.get("reward", 0) if ok else 0
        print(f"{'OK ' if ok else 'BAD'} {data['task_id']:<22} answer={answer!r} expected={data.get('expected')!r}")
    print(f"reward {got:g} / {total:g}")


if __name__ == "__main__":
    main()
