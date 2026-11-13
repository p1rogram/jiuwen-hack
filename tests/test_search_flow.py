"""Число recovery, единый допуск стадий и отсутствие search self-learning."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from harness.decision import Decision
from harness.outcome import Outcome
from harness.pipeline import TaskPipeline
from harness.prompting import Prepared
from harness.search_access import SearchAccess
from harness.search_config import LIMITS
from harness.search_corpus import SearchCorpus
from harness.search_flow import solve_search, truncated
from harness.tasks import Task
from harness.verifier import Verdict


class SearchFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / 'a.md').write_text('# Item\nValue: 2\n')
        access = SearchAccess(SearchCorpus.scan(root, LIMITS), dict(LIMITS), evidence_enabled=True, recovery_enabled=True)
        access.snapshot('Item', dict(max_file_chars=6000, max_total_chars=24000))
        self.p = Prepared(Task('t', 'search', 'value?', 1, 'exact_int', root), [], search=access)
        self.good = json.dumps(dict(status='answer', answer='2', reason='explicit', alternatives=[],
                                    evidence=[dict(path='a.md', start_line=2, end_line=2)]))

    def reply(self, text, reason='stop'):
        return SimpleNamespace(text=text, finish_reason=reason, usage=SimpleNamespace(input_tokens=10,
                               output_tokens=20, reasoning_tokens=0), cost=21, latency_sec=0)

    def solver(self, replies, max_steps=6):
        from harness.stage2 import Stage2Solver
        solver = Mock(_max_steps=max_steps, _max_tokens=100, _model='m')
        solver._gw.ask = AsyncMock(side_effect=replies)
        solver._cost.stage2_step.return_value = 21
        solver._stop = Stage2Solver._stop
        return solver

    async def test_stage2_length_once_then_accepts_within_same_budget(self):
        solver = self.solver([self.reply(self.good, 'length'), self.reply(self.good)])
        outcome = Outcome('t', messages=[dict(role='assistant', content='invalid')])
        verdict = await solve_search(solver, self.p, outcome, Verdict(False, '', 'invalid', 'protocol_error'), 100)
        self.assertTrue(verdict.accepted)
        self.assertEqual(outcome.recovery_count, 1)
        self.assertEqual(outcome.stage2_steps, 2)
        self.assertEqual(outcome.cost, 42)

    async def test_repeated_length_stops_without_third_call(self):
        solver = self.solver([self.reply(self.good, 'length')] * 2)
        outcome = Outcome('t')
        verdict = await solve_search(solver, self.p, outcome, Verdict(False, '', '', 'protocol_error'), 100)
        self.assertFalse(verdict.accepted)
        self.assertEqual(outcome.stage2_stop, 'output_limit')
        self.assertEqual(solver._gw.ask.await_count, 2)

    async def test_budget_blocks_recovery_and_final_cannot_bypass(self):
        solver = self.solver([self.reply('FINAL: 2')])
        outcome = Outcome('t')
        verdict = await solve_search(solver, self.p, outcome, Verdict(False, '', '', 'protocol_error'), 21)
        self.assertFalse(verdict.accepted)
        self.assertEqual(solver._gw.ask.await_count, 1)
        self.assertEqual(outcome.stage2_stop, 'protocol_error')

    async def test_stage1_recovery_has_no_stage2_fallback_or_learning(self):
        stage1 = Mock(first=AsyncMock(return_value=Verdict(False, '', '', 'truncated')),
                      retry=AsyncMock(return_value=Verdict(False, '', '', 'needs_context')))
        stage1.can_retry.return_value = True
        stage2, success, policy = Mock(), Mock(), Mock()
        policy.branch.return_value = Decision('truncated', 0, 'retry', 100)
        pipeline = TaskPipeline(stage1, stage2, policy, success, lambda: 0)
        outcome = await pipeline.solve(self.p, Outcome('t'))
        self.assertEqual(outcome.stage2_stop, 'recovery_exhausted')
        self.assertEqual(outcome.recovery_count, 1)
        stage2.solve.assert_not_called()
        success.observe.assert_not_called()

    def test_finish_reason_takes_precedence_over_usage(self):
        self.assertTrue(truncated(self.reply(self.good, 'length'), 100))
        self.assertFalse(truncated(self.reply(self.good), 10))
        self.assertTrue(truncated(self.reply(self.good, None), 10))

    async def test_addressed_repair_and_one_event_for_each_rejection(self):
        from harness.search_answer import evaluate
        first = evaluate('bad', self.p.search)[0]
        solver = self.solver([self.reply('bad'), self.reply(self.good)])
        solver._log = Mock()
        outcome = Outcome('t', messages=[dict(role='assistant', content='bad')])
        verdict = await solve_search(solver, self.p, outcome, first, 100)
        self.assertFalse(verdict.accepted)
        self.assertEqual(solver._gw.ask.await_count, 1)
        self.assertEqual(outcome.protocol_repairs, 1)
        prompt = solver._gw.ask.call_args.args[1][-2]['content']
        self.assertIn('json_syntax at $', prompt)
        event = solver._log.write.call_args.args[0]
        self.assertEqual(event['code'], 'json_syntax')
        self.assertTrue(event['repair_used'])
        solver._log.write.assert_called_once()

    async def test_unseen_repair_allows_read_within_remaining_steps(self):
        from harness.search_answer import evaluate
        self.p.search.visible.clear()
        first = evaluate(self.good, self.p.search)[0]
        read = json.dumps(dict(action='read', path='a.md', start_line=2, max_lines=1))
        solver = self.solver([self.reply(read), self.reply(self.good)])
        verdict = await solve_search(solver, self.p, Outcome('t'), first, 100)
        self.assertTrue(verdict.accepted)
        self.assertEqual(solver._gw.ask.await_count, 2)

    async def test_changed_source_stops_before_repair(self):
        from harness.search_answer import evaluate
        (self.p.task.workspace / 'a.md').write_text('changed')
        first = evaluate(self.good, self.p.search)[0]
        solver = self.solver([self.reply(self.good)])
        outcome = Outcome('t')
        verdict = await solve_search(solver, self.p, outcome, first, 100)
        self.assertFalse(verdict.accepted)
        self.assertEqual(outcome.stage2_stop, 'source_changed')
        solver._gw.ask.assert_not_called()

    async def test_stage1_rejection_event_contains_no_raw_reply(self):
        from harness.solver import Stage1Solver
        gateway, log = Mock(), Mock()
        gateway.ask = AsyncMock(return_value=self.reply('private malformed reply'))
        solver = Stage1Solver(gateway, Mock(), Mock(), 'm',
                              dict(attempts=2, script_timeout_sec=1), log)
        outcome = Outcome('t')
        verdict = await solver.first(self.p, outcome)
        self.assertEqual(verdict.protocol_issue['code'], 'json_syntax')
        log.write.assert_called_once()
        event = log.write.call_args.args[0]
        self.assertEqual(event['stage'], 'stage1.1')
        self.assertEqual(event['output_chars'], 23)
        self.assertFalse(event['repair_used'])
        self.assertNotIn('private', json.dumps(event))

    async def test_event_disk_failure_does_not_change_verdict(self):
        solver = self.solver([self.reply('bad')])
        solver._log = Mock()
        solver._log.write.side_effect = OSError('disk')
        verdict = await solve_search(solver, self.p, Outcome('t'),
                                     Verdict(False, '', '', 'protocol_error'), 100)
        self.assertEqual(verdict.kind, 'protocol_error')
        self.assertEqual(solver._gw.ask.await_count, 1)
