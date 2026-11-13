"""Границы протокола, видимости и полноты по приложениям A/B спецификации."""
import json
import tempfile
import unittest
from pathlib import Path

from harness.search_access import SearchAccess
from harness.search_answer import evaluate, parse
from harness.search_config import LIMITS, validate
from harness.search_corpus import SearchCorpus


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / 'doc.md').write_text('# Entity\nValue: N/A\nOther: fact\n', encoding='utf-8')
        self.access = SearchAccess(SearchCorpus.scan(self.root, LIMITS), dict(LIMITS))

    def answer(self, **fields):
        data = dict(status='answer', answer='N/A', evidence=[dict(path='doc.md', start_line=1, end_line=2)],
                    reason='Explicit value', alternatives=[])
        data.update(fields)
        return json.dumps(data)

    def test_citation_requires_shown_complete_lines(self):
        self.assertFalse(evaluate(self.answer(), self.access)[0].accepted)
        self.access.request(dict(action='read', path='doc.md', start_line=1, max_lines=1))
        self.assertFalse(evaluate(self.answer(), self.access)[0].accepted)
        self.access.request(dict(action='read', path='doc.md', start_line=2, max_lines=1))
        verdict = evaluate(self.answer(), self.access)[0]
        self.assertTrue(verdict.accepted)
        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.verification, 'model_supported')
        self.assertEqual(verdict.answer, 'N/A')

    def test_changed_or_unknown_source_rejected(self):
        self.access.snapshot('Entity', dict(max_file_chars=6000, max_total_chars=24000))
        (self.root / 'doc.md').write_text('# Entity\nValue: changed\n')
        self.assertFalse(evaluate(self.answer(), self.access)[0].accepted)
        self.assertFalse(evaluate(self.answer(evidence=[dict(path='../doc.md', start_line=1, end_line=2)]), self.access)[0].accepted)

    def test_final_run_fences_and_unknown_fields(self):
        for text in ('FINAL: N/A', '```python\nprint("N/A")\n```', 'prefix ' + self.answer(),
                     self.answer(extra=True), self.answer(evidence=[]),
                     self.answer(evidence=[dict(path='doc.md', start_line=True, end_line=2)])):
            with self.subTest(text=text):
                self.assertEqual(evaluate(text, self.access)[0].kind, 'protocol_error')
        self.assertEqual(parse('```json\n' + self.answer() + '\n```', LIMITS)['answer'], 'N/A')
        self.assertEqual(evaluate('{"status":"answer","status":"insufficient"}', self.access)[0].kind, 'protocol_error')

    def test_refusals_never_submit(self):
        for status, alternatives in [('ambiguous', ['A', 'B']), ('insufficient', [])]:
            verdict = evaluate(self.answer(status=status, answer='', alternatives=alternatives), self.access)[0]
            self.assertFalse(verdict.accepted)
            self.assertEqual(verdict.kind, status)

    def test_protocol_issue_preserves_specific_failure(self):
        cases = [('broken JSON', 'json_syntax', '$'),
                 (self.answer(extra=True), 'schema_fields', '$'),
                 (self.answer(evidence=[]), 'missing_evidence', 'evidence'),
                 (self.answer(), 'unseen_lines', 'evidence[0]'),
                 (self.answer(evidence=[dict(path='../doc.md', start_line=1, end_line=1)]),
                  'unknown_source', 'evidence[0].path')]
        for text, code, field in cases:
            with self.subTest(code=code):
                verdict = evaluate(text, self.access)[0]
                self.assertEqual(verdict.kind, 'protocol_error')
                self.assertFalse(verdict.accepted)
                self.assertEqual(verdict.protocol_issue['code'], code)
                self.assertEqual(verdict.protocol_issue['field'], field)
                self.assertLessEqual(len(json.dumps(verdict.protocol_issue)), 512)

    def test_issue_table_and_unexpected_errors(self):
        from unittest.mock import patch
        cases = [(self.answer(reason=3), 'invalid_type'),
                 (self.answer(reason='x' * (LIMITS['reason_chars'] + 1)), 'limit_exceeded'),
                 (self.answer(status='unknown'), 'invalid_status'),
                 (self.answer(evidence=[dict(path='doc.md', start_line=True, end_line=2)]), 'invalid_range'),
                 (self.answer(status='ambiguous', answer='', alternatives=['A', 'A']), 'invalid_alternatives'),
                 ('{"status":"answer","status":"answer"}', 'duplicate_field')]
        for text, code in cases:
            with self.subTest(code=code):
                verdict = evaluate(text, self.access)[0]
                self.assertEqual(verdict.protocol_issue['code'], code)
                self.assertFalse(verdict.accepted)
        with patch.object(SearchCorpus, 'get', side_effect=RuntimeError('internal')):
            with self.assertRaises(RuntimeError):
                evaluate(self.answer(), self.access)

    def test_oversized_json_integer_is_controlled_rejection(self):
        text = '{"action":"read","path":"doc.md","start_line":' + '9' * 5000 + ',"max_lines":1}'
        verdict = evaluate(text, self.access)[0]
        self.assertEqual(verdict.kind, 'protocol_error')
        self.assertEqual(verdict.protocol_issue['code'], 'invalid_range')

    def test_alternatives_addressed_constraints(self):
        cases = [('answer', None, 'expected_string_array'),
                 ('answer', [3], 'expected_string_array'),
                 ('answer', [''], 'expected_string_array'),
                 ('answer', ['x'] * (LIMITS['alternatives_items'] + 1), 'too_many_items'),
                 ('answer', ['A'], 'expected_empty'),
                 ('insufficient', ['A'], 'expected_empty'),
                 ('ambiguous', ['A', 'A'], 'expected_distinct_candidates')]
        for status, alternatives, constraint in cases:
            with self.subTest(status=status, constraint=constraint):
                verdict = evaluate(self.answer(status=status, answer='N/A' if status == 'answer' else '',
                                               alternatives=alternatives), self.access)[0]
                self.assertFalse(verdict.accepted)
                issue = verdict.protocol_issue
                self.assertEqual(issue['code'], 'invalid_alternatives')
                self.assertEqual(issue['field'], 'alternatives')
                self.assertEqual(issue['constraint'], constraint)
                self.assertLessEqual(len(json.dumps(issue)), 512)

    def test_alternatives_boundaries_preserve_contract(self):
        from harness.search_errors import SearchProtocolError
        for status, values in [('answer', []), ('insufficient', []),
                               ('ambiguous', ['A', 'B']),
                               ('ambiguous', [str(i) for i in range(LIMITS['alternatives_items'])])]:
            with self.subTest(status=status, count=len(values)):
                parsed = parse(self.answer(status=status, answer='N/A' if status == 'answer' else '',
                                           alternatives=values), LIMITS)
                self.assertEqual(parsed['alternatives'], values)
        for code, constraint in [('invalid_type', 'expected_empty'),
                                 ('invalid_alternatives', 'untrusted text')]:
            with self.assertRaises(ValueError):
                SearchProtocolError(code, 'alternatives', alternative_constraint=constraint)

    def test_search_unicode_ranges_and_cursor_isolation(self):
        (self.root / 'doc.md').write_text('# Entity\nStraße\nSTRASSE\n', encoding='utf-8')
        access = SearchAccess(SearchCorpus.scan(self.root, LIMITS), dict(LIMITS, read_max_lines=1))
        req = dict(action='search', query='strasse', cursor=None)
        first = json.loads(access.request(req))
        self.assertEqual(first['total'], 2)
        self.assertEqual(first['items'][0]['start_line'], 2)
        self.assertTrue(first['truncated'])
        req['cursor'] = first['next_cursor']
        second = json.loads(access.request(req))
        self.assertEqual(second['items'][0]['start_line'], 3)
        self.assertIsNone(second['next_cursor'])
        self.assertIsNotNone(json.loads(self.access.request(req))['error'])

    def test_long_line_not_citable_and_json_is_bounded(self):
        (self.root / 'doc.md').write_text('x' * 10000)
        access = SearchAccess(SearchCorpus.scan(self.root, LIMITS), dict(LIMITS, tool_response_chars=300))
        response = access.request(dict(action='read', path='doc.md', start_line=1, max_lines=1))
        self.assertLessEqual(len(response), 300)
        self.assertTrue(json.loads(response)['truncated'])
        self.assertFalse(access.visible)

    def test_limits_and_decode_failures_mean_incomplete(self):
        corpus = SearchCorpus.scan(self.root, dict(LIMITS, scan_bytes=1))
        self.assertFalse(corpus.complete)
        (self.root / 'bad.md').write_bytes(b'\xff')
        self.assertFalse(SearchCorpus.scan(self.root, LIMITS).complete)
        (self.root / 'answer.txt').write_text('old answer')
        (self.root / '.env').write_text('excluded')
        self.assertNotIn('answer.txt', [d.path for d in SearchCorpus.scan(self.root, LIMITS).documents])

    def test_flags_reject_invalid_combinations(self):
        for raw in [dict(search_evidence=dict(enabled=True)),
                    dict(bounded_recovery=dict(enabled=True))]:
            with self.assertRaises(ValueError):
                validate(raw)

    def test_exclusions_are_case_insensitive_and_source_growth_is_rejected(self):
        (self.root / 'Secrets').mkdir()
        (self.root / 'Secrets' / 'config.json').write_text('excluded')
        (self.root / 'TASK.JSON').write_text('excluded')
        (self.root / 'Answer.TXT').write_text('excluded')
        corpus = SearchCorpus.scan(self.root, LIMITS)
        self.assertEqual([d.path for d in corpus.documents], ['doc.md'])
        doc = corpus.get('doc.md')
        (self.root / 'doc.md').write_text('x' * 100000)
        self.assertFalse(corpus.unchanged(doc))

    def test_tools_page_without_materializing_all_match_objects(self):
        import tracemalloc
        (self.root / 'doc.md').write_text('a\n' * 100000)
        access = SearchAccess(SearchCorpus.scan(self.root, LIMITS), dict(LIMITS))
        tracemalloc.start()
        try:
            result = json.loads(access.request(dict(action='search', query='a')))
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertEqual(result['total'], 100000)
        self.assertLessEqual(len(result['items']), LIMITS['read_max_lines'])
        self.assertLess(peak, 10 * 1024 * 1024)
