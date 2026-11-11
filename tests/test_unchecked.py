"""Кандидат без видимых тестов не является verified или наблюдением точности."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from harness.executor import RunResult
from harness.outcome import Outcome
from harness.pipeline import TaskPipeline
from harness.prompting import Prepared
from harness.tasks import Task
from harness.verifier import Verifier, Verdict


class UncheckedTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.ws = Path(tmp.name)
        self.task = Task('t', 'coding', '', 1, 'unittest', self.ws)
        self.verifier = Verifier(2000)

    async def check(self, prepared, run=None):
        return await self.verifier.check(self.task, run or RunResult(0, '', '', False),
                                         visible_tests=prepared.visible_tests, allow_unchecked=True)

    async def test_candidate_not_verified_and_hidden_passes(self):
        import json
        from tests.test_stage2 import FakeGateway, code
        from harness.solver import Stage1Solver
        from harness.prompting import Stage1Prompt
        from harness.snapshot import WorkspaceSnapshot
        from harness.settings import load_settings_raw
        from harness.results import ResultsWriter
        (self.ws / 'solution.py').write_text('def solve(x): return 0\n')
        cfg = load_settings_raw()
        prompt = Stage1Prompt(WorkspaceSnapshot(cfg['snapshot']), patch_types=['coding'],
                              patch_prompts=cfg['stage1']['patch_prompts'])
        prepared = await prompt.build(self.task)
        gateway = FakeGateway([code('def solve(x): return x * 2')])
        stage1 = Stage1Solver(gateway, prompt, self.verifier, 'm', cfg['stage1'])
        outcome = await TaskPipeline(stage1, Mock(), Mock(), Mock(), lambda: 0).solve(prepared, Outcome('t'))
        self.assertTrue(outcome.unchecked)
        self.assertFalse(outcome.verified)
        self.assertEqual(outcome.answer, 'DONE')
        out = self.ws / 'out.json'
        writer = ResultsWriter(out)
        self.assertEqual(writer.record(self.task, outcome.answer), '')
        self.assertEqual((self.ws / 'answer.txt').read_text().strip(), 'DONE')
        self.assertEqual(json.loads(out.read_text()), {'t': 'DONE'})
        hidden = self.ws / 'test_hidden.py'
        hidden.write_text('import unittest\nfrom solution import solve\nclass T(unittest.TestCase):\n'
                          ' def test_hidden(self): self.assertEqual(solve(3), 6)\n')
        from harness.test_runner import run_tests
        self.assertTrue((await run_tests(self.ws, hidden.name, 10)).ok)

    async def test_created_tests_cannot_verify(self):
        prepared = Prepared(self.task, [])
        (self.ws / 'test_fake.py').write_text('import unittest\nclass T(unittest.TestCase):\n'
                                            ' def test_fake(self): self.assertTrue(True)\n')
        self.assertFalse((await self.check(prepared)).ok)
        self.assertEqual((await self.check(prepared)).verification, 'unavailable')

    async def test_deleted_empty_skipped_and_changed_tests_fail(self):
        path = self.ws / 'test_original.py'
        original = 'import unittest\nclass T(unittest.TestCase):\n def test_red(self): self.fail()\n'
        for replacement in [None, '', original.replace('self.fail()', "self.skipTest('skip')")]:
            path.write_text(original)
            prepared = Prepared(self.task, [])
            path.unlink() if replacement is None else path.write_text(replacement)
            verdict = await self.check(prepared)
            self.assertFalse(verdict.accepted)
            self.assertEqual(verdict.kind, 'tests_fail')

    async def test_failure_or_final_only_never_candidate(self):
        prepared = Prepared(self.task, [])
        for run in [RunResult(1, '', 'error', False), RunResult(0, '', '', True)]:
            self.assertFalse((await self.check(prepared, run)).accepted)
        self.assertFalse((await self.verifier.check(self.task, RunResult(0, 'DONE', '', False),
                                                   visible_tests=prepared.visible_tests)).accepted)

    async def test_pipeline_stops_without_observation(self):
        verdict = Verdict(False, 'DONE', verification='unavailable')
        stage1 = Mock(first=AsyncMock(return_value=verdict))
        success = Mock()
        policy = Mock()
        pipeline = TaskPipeline(stage1, Mock(), policy, success, lambda: 0)
        outcome = await pipeline.solve(Prepared(self.task, []), Outcome('t'))
        self.assertEqual(outcome.answer, 'DONE')
        self.assertTrue(outcome.unchecked)
        self.assertFalse(outcome.verified)
        success.observe.assert_not_called()
        policy.branch.assert_not_called()

    async def test_stage2_success_stops_but_final_only_does_not_submit(self):
        from tests.test_stage2 import FakeGateway, CFG, COST, code
        from harness.stage2 import Stage2Solver
        from harness.cost_model import CostModel
        for text, accepted in [(code("from pathlib import Path\nPath('solution.py').write_text('def solve(x): return x*2')"), True),
                               ('FINAL: DONE', False)]:
            gateway = FakeGateway([text])
            solver = Stage2Solver(gateway, self.verifier, CostModel(COST, .1), 'm', dict(CFG, max_steps=1))
            outcome = Outcome('t')
            verdict = await solver.solve(Prepared(self.task, []), outcome,
                                          Verdict(False, '', 'stage1 failed', 'script_error'), 10000)
            self.assertEqual(verdict.accepted, accepted)
            self.assertFalse(verdict.ok)
            self.assertEqual(len(gateway.stages), 1)
