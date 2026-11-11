"""Независимые вычисления для фиксированных не-coding эталонов."""
import csv
import io
import itertools
import re


def rows(case, name):
    return list(csv.DictReader(io.StringIO(case.files[name])))


def math_answer(case):
    p = case.oracle
    if case.family == "congruence":
        return next(x for x in range(p["m"] * p["n"]) if x % p["m"] == p["a"] and x % p["n"] == p["b"])
    if case.family == "blocked_paths":
        # Полный перебор путей вместо рекуррентного подсчёта.
        total = 0
        for steps in itertools.product((0, 1), repeat=p["w"] + p["h"]):
            x = y = 0
            blocked = False
            for step in steps:
                x, y = x + (step == 0), y + (step == 1)
                blocked |= (x, y) == (1, 1)
            total += (x, y) == (p["w"], p["h"]) and not blocked
        return total
    if case.family == "three_sets":
        a, b, c, ab, ac, bc, abc = p["counts"]
        # Сумма семи непересекающихся областей диаграммы Венна.
        return sum([a-ab-ac+abc, b-ab-bc+abc, c-ac-bc+abc, ab-abc, ac-abc, bc-abc, abc])
    if case.family == "recurrence":
        def value(n):
            return (p["first"], p["second"])[n] if n < 2 else 2 * value(n-1) + value(n-2)
        return value(p["index"])
    raise ValueError(case.family)


def data_answer(case):
    if case.family == "version_join":
        prices = rows(case, "prices.csv")
        return sum(int(order["quantity"]) * int(max((p for p in prices if p["sku"] == order["sku"]),
                                                   key=lambda p: int(p["version"]))["price"])
                   for order in rows(case, "orders.csv"))
    if case.family == "interval_overlap":
        # Считаем целочисленные минутные ячейки, а не формулу пересечения.
        return sum(int(row["start"]) <= t < int(row["end"])
                   for row in rows(case, "sessions.csv") for t in range(case.oracle["lo"], case.oracle["hi"]))
    if case.family == "weighted_floor":
        data = rows(case, "measurements.csv")
        expanded = [int(r["value"]) for r in data for _ in range(int(r["weight"]))]
        return sum(expanded) // len(expanded)
    if case.family == "latest_event":
        data = rows(case, "events.csv")
        latest = {r["key"]: r for r in sorted(data, key=lambda r: int(r["time"]))}
        return sum(int(r["units"]) for r in latest.values())
    raise ValueError(case.family)


def search_answer(case):
    if case.family == "section_reference":
        name = re.search(r"owner for (\w+)", case.instruction)[1]
        section = case.files["kb/guide.md"].split("## " + name + "\n")[1].split("## ")[0]
        anchor = re.search(r"Runbook: ops.md#(\w+)", section)[1]
        text = case.files["kb/ops.md"].split("## " + anchor + "\n")[1].split("\n## ")[0]
        return re.search(r"Owner: (\w+)", text)[1]
    if case.family == "table_lookup":
        route = re.search(r"route (\w+)", case.instruction)[1]
        return re.search(r"\| " + route + r" \| express \| (\w+) \|", case.files["kb/routes.md"])[1]
    if case.family == "effective_revision":
        date = re.search(r"on (\d{4}-\d\d-\d\d)", case.instruction)[1]
        revisions = re.findall(r"Effective: ([\d-]+)\nProvider: (\w+)", case.files["kb/policy.md"])
        return max((d, name) for d, name in revisions if d <= date)[1]
    if case.family == "property_intersection":
        zone = re.search(r"zone (\w+)", case.instruction)[1]
        matches = [body.splitlines()[0][2:] for body in case.files.values()
                   if "Transport: rail\n" in body and "Storage: cold\n" in body and f"Zone: {zone}\n" not in body]
        if len(matches) != 1:
            raise ValueError("ambiguous intersection")
        return matches[0]
    raise ValueError(case.family)


def answer(case):
    return str({"math": math_answer, "data_analysis": data_answer, "search": search_answer}[case.category](case))
