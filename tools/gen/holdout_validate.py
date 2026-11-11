"""Офлайн-проверка целостности, эталонов и скрытых краёв holdout."""
import argparse
import json
import shutil
import tempfile
from collections import Counter
from pathlib import Path

from tools.gen.holdout import VERSION, build, inventory
from tools.gen.holdout_oracles import answer
from tools.gen.score_variants import tests_pass


def validate(root: Path):
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest["version"] != VERSION or manifest["sha256"] != inventory(root):
        raise ValueError("manifest version or file hashes differ")
    expected = json.loads((root / "tasks-expected.json").read_text(encoding="utf-8"))
    cases = build()
    ids = [f"{c.category}_h{i:02d}" for i, c in enumerate(cases, 1)]
    if set(expected) != set(ids) or {p.name for p in (root / "tasks").iterdir()} != set(ids):
        raise ValueError("incomplete task inventory")
    if [r["task_id"] for r in manifest["cases"]] != ids:
        raise ValueError("incomplete manifest cases")
    counts = Counter((c.category, c.feasible) for c in cases)
    if len(cases) != 40 or any(counts[t, f] != (8 if f else 2)
                               for t in ("math", "data_analysis", "search", "coding") for f in (False, True)):
        raise ValueError("incorrect holdout composition")
    for task_id, case in zip(ids, cases):
        exp = expected[task_id]
        if (exp["expected"], exp["feasible"], exp["family_id"]) != (case.expected, case.feasible, case.family):
            raise ValueError(f"{task_id}: expected metadata differs")
        if not case.evidence:
            raise ValueError(f"{task_id}: missing evidence")
        if not case.feasible:
            continue
        if case.category != "coding":
            if answer(case) != case.expected:
                raise ValueError(f"{task_id}: independent oracle disagrees")
            continue
        if exp.get("require_hidden") is not True:
            raise ValueError(f"{task_id}: hidden tests must be mandatory")
        hidden = root / "tasks-hidden" / task_id / "test_hidden.py"
        ws = root / "tasks" / task_id / "workspace"
        if tests_pass(ws, hidden, require_hidden=True)[0]:
            raise ValueError(f"{task_id}: original bug not detected by visible tests")
        for folder, wanted in [("references", (True, True)), ("partial", (True, False))]:
            with tempfile.TemporaryDirectory() as tmp:
                work = Path(tmp) / "ws"
                shutil.copytree(ws, work)
                shutil.copyfile(root / folder / task_id / "solution.py", work / "solution.py")
                actual = tests_pass(work, hidden, require_hidden=True)
                if actual != wanted:
                    raise ValueError(f"{task_id}: {folder} {actual}, expected {wanted}")
    return dict(version=VERSION, tasks=40, feasible=32, infeasible=8, coding_references=8)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    print(json.dumps(validate(args.root), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
