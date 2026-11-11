"""Сборка замороженного holdout: python -m tools.gen.holdout <новая-папка-набора>."""
import argparse
import hashlib
import json
from pathlib import Path

from tools.gen.content import holdout_coding, holdout_data, holdout_math, holdout_search

VERSION = "holdout-v1"
DIFFERENCES = {
    "math": "Прежние templates/variants: последовательная арифметика; здесь остатки, решётки, множества, рекуррентность.",
    "data_analysis": "Прежние: агрегат одного CSV; здесь версии двух таблиц, интервалы, веса и latest-by-key.",
    "search": "Прежние: карточки людей/компаний; здесь разделы, таблицы, датированные версии, пересечение/исключение.",
    "coding": "Прежние: простые функции/классы из coding_pool/variants_coding; здесь интервалы, DAG, веса, кавычки.",
}


def build():
    return [case for module in (holdout_math, holdout_data, holdout_search, holdout_coding) for case in module.cases()]


def inventory(root: Path):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and p != root / "manifest.json"}


def write(root: Path):
    # mkdir без exist_ok — два генератора не должны смешивать файлы одного набора.
    root.mkdir(parents=True, exist_ok=False)
    expected, records = {}, []
    for index, case in enumerate(build(), 1):
        task_id = f"{case.category}_h{index:02d}"
        task = root / "tasks" / task_id
        task.mkdir(parents=True)
        suffix = " Write ONLY the final answer to answer.txt in the current directory."
        if case.category == "coding":
            suffix = " Write ONLY DONE to answer.txt when all requirements are met."
        meta = dict(task_id=task_id, task_type=case.category, instruction=case.instruction + suffix,
                    reward=1, check="unittest" if case.category == "coding" else
                    "exact_str" if case.category == "search" else "exact_int")
        dump(task / "task.json", meta)
        for name, body in case.files.items():
            put(task / "workspace" / name, body)
        expected[task_id] = dict(type=case.category, feasible=case.feasible, expected=case.expected,
                                 family_id=case.family, require_hidden=case.category == "coding" and case.feasible)
        for folder, body, name in [("tasks-hidden", case.hidden, "test_hidden.py"),
                                    ("references", case.reference, "solution.py"),
                                    ("partial", case.partial, "solution.py")]:
            if body:
                put(root / folder / task_id / name, body)
        module_name = {"data_analysis": "data"}.get(case.category, case.category)
        records.append(dict(task_id=task_id, family_id=case.family, feasible=case.feasible,
                            source=f"tools/gen/content/holdout_{module_name}.py",
                            evidence=case.evidence, distinction=DIFFERENCES[case.category]))
    dump(root / "tasks-expected.json", expected)
    dump(root / "manifest.json", dict(version=VERSION, cases=records, sha256=inventory(root)))
    return root


def put(path: Path, body: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body.encode("utf-8"))


def dump(path: Path, value):
    put(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    write(args.out)
    print(f"{VERSION}: 40 tasks -> {args.out}")


if __name__ == "__main__":
    main()
