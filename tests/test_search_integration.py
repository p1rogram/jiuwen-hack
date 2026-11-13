"""Совместимость ранних правил, честных статусов и общего восстановления."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from harness.decision import Decision
from harness.executor import RunResult
from harness.feasibility.detector import FeasibilityDetector
from harness.outcome import Outcome
from harness.pipeline import TaskPipeline
from harness.prompting import Prepared
from harness.rules import RulesSolver
from harness.search_config import LIMITS
from harness.search_corpus import SearchCorpus
from harness.settings import load_config
from harness.tasks import Task
from harness.verifier import Verdict, Verifier
from tools.calibrate_success import branch_outcomes


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    async def test_honest_format_preserves_legacy_technical_observation(self):
        task = Task('t', 'math', '', 1, 'exact_int', self.root)
        verdict = await Verifier(1000, honest_format=True).check(task, RunResult(0, '7', '', False))
        self.assertTrue(verdict.accepted)
        self.assertFalse(verdict.ok)
        self.assertTrue(verdict.legacy_ok)
        first, success = Mock(first=AsyncMock(return_value=verdict)), Mock()
        outcome = await TaskPipeline(first, Mock(), Mock(), success, lambda: 0).solve(Prepared(task, []), Outcome('t'))
        self.assertEqual(outcome.answer, '7')
        self.assertFalse(outcome.verified)
        success.observe.assert_called_once_with('stage1', 'math', '', True)

    async def test_nonsearch_recovery_only_once_without_stage2(self):
        task = Task('t', 'coding', '', 1, 'unittest', self.root)
        failed = Verdict(False, '', 'length', 'truncated')
        first = Mock(first=AsyncMock(return_value=failed), retry=AsyncMock(return_value=failed))
        first.can_retry.return_value = True
        policy = Mock()
        policy.branch.return_value = Decision('truncated', 0, 'retry', 100)
        second = Mock()
        prepared = Prepared(task, [], bounded_recovery=True)
        outcome = await TaskPipeline(first, second, policy, Mock(), lambda: 0).solve(prepared, Outcome('t'))
        self.assertEqual(outcome.stage2_stop, 'output_limit')
        self.assertEqual(outcome.recovery_count, 1)
        self.assertEqual(first.retry.await_count, 1)
        second.solve.assert_not_called()

    def test_strict_rules_and_detector_do_not_infer_missing_semantics(self):
        kb = self.root / 'kb'
        kb.mkdir()
        (kb / 'a.md').write_text('# Ada Moss\nBorn: 1980\n')
        intro = 'The directory kb/ contains a small knowledge base of markdown files. '
        task = Task('t', 'search', intro + 'In which year was Ada Moss born?', 1, 'exact_int', self.root)
        rules = RulesSolver(load_config('rules.json'))
        self.assertEqual(rules.solve(task, SearchCorpus.scan(self.root, LIMITS)), '1980')
        (kb / 'a.md').write_text('# Ada Moss\nBorn: 1980\nEffective Date: 2020\n')
        self.assertIsNone(rules.solve(task, SearchCorpus.scan(self.root, LIMITS)))
        other = Task('t', 'search', intro + "In which year was Ada Moss's employer founded?", 1, 'exact_int', self.root)
        detector = FeasibilityDetector(load_config('feasibility.json'), search_evidence=True)
        self.assertEqual(detector.check(other), [])

    def test_old_and_new_search_statuses_are_not_offline_truth(self):
        for status in ('verified', 'unverified', 'submitted_unchecked'):
            self.assertEqual(branch_outcomes(dict(type='search', status=status, attempts=1)), [])

    async def test_repeated_nonsearch_script_keeps_accepted_format_only(self):
        from harness.stage2 import Stage2Solver
        task = Task('t', 'math', '', 1, 'exact_int', self.root)
        solver = Mock(_verifier=Verifier(1000, honest_format=True))
        solver._stop = Stage2Solver._stop
        verdict = await Stage2Solver._repeated(solver, task, Outcome('t'), RunResult(0, '7\n', '', False),
                                               Verdict(False, '', 'previous', 'bad_format'))
        self.assertTrue(verdict.accepted)
        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.answer, '7')
