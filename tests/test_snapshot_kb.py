"""Узкий срез базы знаний: семена по заголовку из вопроса + обход связей по упоминанию заголовков карточек.
Ожидания — из specs/token-cuts/spec.md, шаг 1; разметка списком и жирным — specs/optimization-research/findings.md
(идея 1: var2 search_v01, срез терял карточку советника при строках «- Ключ: Значение»)."""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.settings import load_settings_raw  # noqa: E402
from harness.snapshot import WorkspaceSnapshot  # noqa: E402
from harness.tasks import Task  # noqa: E402

CARDS = {
    "person_anna_lee": ("Anna Lee", {"Born": "1980", "Employer": "Cobalt Works", "Mentor": "Boris Kim"}),
    "person_boris_kim": ("Boris Kim", {"Born": "1975", "Employer": "Fjord Data", "Mentor": "Olga Sato"}),
    "person_olga_sato": ("Olga Sato", {"Born": "1960", "Employer": "Ember Bio", "Mentor": "Anna Lee"}),
    "company_cobalt_works": ("Cobalt Works", {"Headquarters": "Quito", "Founded": "1952"}),
    "company_fjord_data": ("Fjord Data", {"Headquarters": "Osaka", "Founded": "1964"}),
    "company_ember_bio": ("Ember Bio", {"Headquarters": "Graz", "Founded": "2007"}),
    "company_iris_optics": ("Iris Optics", {"Headquarters": "Leeds", "Founded": "1990"}),
    "person_kira_lee": ("Kira Lee", {"Born": "1991", "Employer": "Iris Optics", "Mentor": "Olga Sato"}),
}
INTRO = "The directory kb/ contains a small knowledge base of markdown files. "


PLAIN = "{k}: {v}"


def build(root: Path, question: str, line: str = PLAIN) -> str:
    ws = root / "t" / "workspace"
    (ws / "kb").mkdir(parents=True)
    for stem, (name, fields) in CARDS.items():
        body = "\n".join([f"# {name}", ""] + [line.format(k=k, v=v) for k, v in fields.items()]) + "\n"
        (ws / "kb" / f"{stem}.md").write_text(body, encoding="utf-8")
    task = Task("search_x", "search", INTRO + question, 1.0, "exact_str", root / "t")
    return asyncio.run(WorkspaceSnapshot(load_settings_raw()["snapshot"]).build(task))


class KbSlice(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_two_hop_chain_only(self):
        snap = build(self.root, "In which city is the headquarters of the employer of Anna Lee's mentor?")
        for stem in ("person_anna_lee", "person_boris_kim", "company_fjord_data"):
            self.assertIn(f"--- kb/{stem}.md ---", snap)
        self.assertNotIn("company_iris_optics", snap)
        self.assertNotIn("person_kira_lee", snap)

    def test_unknown_name_keeps_old_ranking(self):
        snap = build(self.root, "In which year was Pavel Novak born?")
        self.assertIn("--- kb/company_iris_optics.md ---", snap)
        self.assertIn("--- kb/person_kira_lee.md ---", snap)

    def test_chain_found_in_any_markup(self):
        # должно пройти: звено цепочки — упоминание имени другой карточки, разметка строки не важна
        q = "In which city is the headquarters of the employer of Anna Lee's mentor?"
        for line in ("- {k}: {v}", "* **{k}:** {v}", "| {k} | {v} |", "Their {k} is {v}."):
            with self.subTest(line=line):
                root = self.root / str(abs(hash(line)))
                snap = build(root, q, line)
                for stem in ("person_anna_lee", "person_boris_kim", "company_fjord_data"):
                    self.assertIn(f"--- kb/{stem}.md ---", snap)
                self.assertNotIn("company_iris_optics", snap)


if __name__ == "__main__":
    unittest.main()
