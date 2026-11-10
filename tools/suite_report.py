"""Сводка метрик tools.suite в Markdown: таблица прогонов, разбивка по типам, ошибки."""
from __future__ import annotations

OK, WRONG, SKIP_OK, ANSWERED_INF, FALSE_SKIP = "OK", "WRONG", "SKIP-OK", "ANSWERED-INFEASIBLE", "FALSE-SKIP"
TYPES = ("coding", "data_analysis", "math", "search")


def pct(a: float, b: float) -> str:
    return f"{100 * a / b:.1f} %" if b else "—"


def render(summary: dict) -> str:
    t = summary["total"]
    lines = [f"# Метрики харнесса ({summary['generated']})", "",
             f"Итого: решено **{t['solved']:g}/{t['feasible']:g}** выполнимых ({pct(t['solved'], t['feasible'])}), "
             f"баллы {t['reward']:g}/{t['reward_max']:g}, токены (выход + 0,1·вход) {t['cost']:g}, "
             f"вызовов модели {t['llm_calls']:g}.", "",
             "| Прогон | Решено | Баллы | Токены | Вход / выход | Вызовы | На невыполнимые | Время, с | Код |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in summary["runs"]:
        lines.append(f"| {r['run']} | {r['solved']}/{r['feasible']} | {r['reward']:g}/{r['reward_max']:g} | {r['cost']:g} "
                     f"| {r['input_tokens']} / {r['output_tokens']} | {r['llm_calls']} | {r['cost_on_infeasible']:g} "
                     f"| {r['wall_sec'] if r['wall_sec'] is not None else '—'} | {r['exit_code'] if r['exit_code'] is not None else '—'} |")
    lines += ["", "## По типам", "", "| Прогон | " + " | ".join(TYPES) + " |",
              "|---|---|---|---|---|"]
    for r in summary["runs"]:
        cells = [f"{b['solved']}/{b['feasible']} ({b['cost']:g})" if (b := r["by_type"].get(t)) else "—"
                 for t in TYPES]
        lines.append(f"| {r['run']} | " + " | ".join(cells) + " |")
    lines += ["", "В скобках — токены типа.", "", "## Ошибки и невыполнимые", ""]
    for r in summary["runs"]:
        bad = [x for x in r["rows"] if x["verdict"] not in (OK, SKIP_OK)]
        if not bad:
            continue
        lines.append(f"**{r['run']}**")
        for x in bad:
            lines.append(f"- `{x['task_id']}` {x['verdict']}: ответ {x['answer']!r}, эталон {x['expected']!r}, "
                         f"статус {x['status']}{', провал ' + x['failure'] if x['failure'] else ''}, токены {x['cost']:g}")
        lines.append("")
    lines += ["Вердикты: OK — верно; WRONG/FAIL — неверно (FAIL, VISIBLE-ONLY — тесты coding); SKIP-OK — невыполнимая "
              "пропущена; ANSWERED-INFEASIBLE — на невыполнимую дан ответ (баллы не отнимает, тратит токены); "
              "FALSE-SKIP — выполнимая пропущена детектором.", ""]
    return "\n".join(lines)
