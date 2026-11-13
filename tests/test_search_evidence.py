"""Контракт retrieval из specs/evidence-safe-search/spec.md, раздел 1."""
import json
import tempfile
import unittest
from pathlib import Path

from harness.search_corpus import SearchCorpus
from harness.search_config import LIMITS
from harness.snapshot import WorkspaceSnapshot
from harness.tasks import Task


class SearchRetrievalContract(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ws = Path(self.tmp.name)

    def write(self, name, body):
        path = self.ws / name
        path.write_text(body, encoding="utf-8")
        return path

    def test_inverse_reference_is_reachable(self):
        organization = self.write("organization.md", "# North Institute\nCity: Bergen\n")
        director = self.write("person.md", "# Ada Moss\nEmployer: North Institute\nRole: Director\n")
        self.write('aaa.md', '# Unrelated\nNo links.\n')
        selected = SearchCorpus.scan(self.ws, LIMITS).ranked('Who directs North Institute?')
        self.assertEqual({d.path for d in selected[:2]}, {organization.name, director.name})

    def test_duplicate_headings_preserve_both_documents(self):
        first = self.write("old.md", "# Ada Moss\nEmployer: North Institute\n")
        second = self.write("current.md", "# Ada Moss\nEmployer: South Institute\n")
        selected = SearchCorpus.scan(self.ws, LIMITS).ranked('Where does Ada Moss work?')
        self.assertEqual({d.path for d in selected}, {first.name, second.name})

    async def test_small_corpus_shows_every_document_despite_seed(self):
        self.write("person.md", "# Ada Moss\nCity: Bergen\n")
        self.write("register.md", "# Employer register\nAda Moss: North Institute; South Institute\n")
        for i in range(5):
            self.write(f"note{i}.md", f"# Record {i}\nArchived.\n")
        cfg = json.loads((Path(__file__).parents[1] / "config/harness.json").read_text(encoding="utf-8"))
        snapshot_cfg = dict(cfg["snapshot"])
        snapshot_cfg["search_retrieval"] = {"enabled": True}
        snapshot = WorkspaceSnapshot(snapshot_cfg)
        task = Task("synthetic", "search", "How many employers does Ada Moss have?",
                    1, "exact_int", self.ws)
        rendered = await snapshot.build(task)
        self.assertIn("Ada Moss: North Institute; South Institute", rendered)
        for path in self.ws.glob("*.md"):
            self.assertIn(f"--- {path.name} ---", rendered)
