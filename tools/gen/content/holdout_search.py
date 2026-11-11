"""Search по разделам, таблицам, датам и пересечению свойств документов."""
from tools.gen.holdout_types import Case


def cases():
    out = []
    for name, answer in [("Aster", "Soren"), ("Birch", "Tala")]:
        out.append(Case("search", "section_reference",
                        f"In kb/guide.md find the escalation owner for {name} using its Runbook section reference. "
                        "Return the owner name only.", answer,
                        f"{name}: ссылка Runbook → раздел Escalation → {answer}; не владелец проекта.",
                        {"kb/guide.md": "# Guide\n## Aster\nProject owner: Mira\nRunbook: ops.md#east\n"
                         "## Birch\nProject owner: Nero\nRunbook: ops.md#west\n",
                         "kb/ops.md": "# Operations\n## east\n### Escalation\nOwner: Soren\n"
                         "## west\n### Escalation\nOwner: Tala\n"}))
    for route, answer in [("north", "Amber"), ("south", "Cedar")]:
        out.append(Case("search", "table_lookup",
                        f"Which carrier is listed for route {route} with service express in kb/routes.md? "
                        "Return just the carrier.", answer, f"Строка {route}/express: {answer}.",
                        {"kb/routes.md": "# Routes\n| route | service | carrier |\n|---|---|---|\n"
                         "| north | standard | Birch |\n| north | express | Amber |\n"
                         "| south | express | Cedar |\n| south | standard | Dune |\n"}))
    for date, answer in [("2025-02-01", "Larch"), ("2025-08-01", "Maple")]:
        out.append(Case("search", "effective_revision",
                        f"According to kb/policy.md, which provider is effective on {date}? "
                        "Use the revision with greatest effective date not after that day; return only its name.",
                        answer, f"На {date} действует {answer}; будущая версия не применяется.",
                        {"kb/policy.md": "# Provider policy\n## Revision C\nEffective: 2026-01-01\nProvider: Pine\n"
                         "## Revision A\nEffective: 2025-01-01\nProvider: Larch\n"
                         "## Revision B\nEffective: 2025-06-01\nProvider: Maple\n"}))
    for forbidden, answer in [("red", "Beryl"), ("blue", "Arden")]:
        out.append(Case("search", "property_intersection",
                        f"Each kb/ card describes one depot. Which depot supports both rail and cold storage, "
                        f"but does not have zone {forbidden}? Return its name. The cards are complete.", answer,
                        f"Оба свойства у Arden и Beryl; исключение зоны {forbidden} оставляет {answer}.",
                        {"kb/a.md": "# Arden\nTransport: rail\nStorage: cold\nZone: red\n",
                         "kb/b.md": "# Beryl\nTransport: rail\nStorage: cold\nZone: blue\n",
                         "kb/c.md": "# Canto\nTransport: road\nStorage: cold\nZone: green\n"}))
    out += [Case("search", "absent_section",
                 "What is the escalation owner in the west section of kb/ops.md? No other source is available.",
                 None, "Есть только east, владелец west неизвестен.",
                 {"kb/ops.md": "# Operations\n## east\nOwner: Soren\n"}),
            Case("search", "conflicting_revision",
                 "Which provider applies on 2025-08-01 according to kb/policy.md? "
                 "Use greatest effective date, but no rule resolves ties.", None,
                 "Две взаимоисключающие записи одной даты, приоритет отсутствует.",
                 {"kb/policy.md": "# Policy\n## A\nEffective: 2025-06-01\nProvider: Maple\n"
                  "## B\nEffective: 2025-06-01\nProvider: Pine\n"})]
    return out
