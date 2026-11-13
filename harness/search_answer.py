"""Единый строгий протокол и механический допуск поискового кандидата."""
from __future__ import annotations

import json
import re

from harness.search_access import SearchAccess
from harness.search_errors import SearchProtocolError
from harness.verifier import Verdict

NEEDS_CONTEXT = 'needs_context'
PROTOCOL_ERROR = 'protocol_error'
SEARCH_REFUSALS = ('ambiguous', 'insufficient')


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise SearchProtocolError('duplicate_field')
        obj[key] = value
    return obj


def _parse_int(text):
    # Строковые диапазоны ограничены 2**31; огромные числа нельзя отдавать int().
    if len(text.lstrip('-')) > 4300:
        raise SearchProtocolError('invalid_range')
    return int(text)


def parse(text: str, cfg: dict) -> dict:
    if len(text) > cfg['tool_response_chars'] * 4:
        raise SearchProtocolError('limit_exceeded')
    text = text.strip()
    fence = re.fullmatch(r'```json\s*\n(.*?)\n```', text, re.DOTALL)
    if fence:
        text = fence[1]
    try:
        obj = json.loads(text, object_pairs_hook=_object, parse_int=_parse_int)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise SearchProtocolError('json_syntax') from exc
    if not isinstance(obj, dict):
        raise SearchProtocolError('invalid_type')
    if 'action' in obj:
        action = obj['action']
        keys = {'list': {'action', 'cursor'}, 'search': {'action', 'query', 'cursor'},
                'read': {'action', 'path', 'start_line', 'max_lines', 'cursor'}}
        if not isinstance(action, str) or action not in keys or set(obj) - keys[action]:
            raise SearchProtocolError('schema_fields', 'action')
        if obj.get('cursor') is not None and (not isinstance(obj['cursor'], str) or len(obj['cursor']) > 128):
            raise SearchProtocolError('invalid_type', 'cursor')
        if action == 'search' and (not isinstance(obj.get('query'), str) or
                                  not 1 <= len(obj['query'].strip()) <= cfg['query_chars']):
            raise SearchProtocolError('invalid_type', 'query')
        if action == 'read' and (not isinstance(obj.get('path'), str) or len(obj['path']) > 1024 or
                not _integer(obj.get('start_line'), 1, 2**31) or
                not _integer(obj.get('max_lines'), 1, cfg['read_max_lines'])):
            raise SearchProtocolError('invalid_range', 'start_line')
        return obj
    if set(obj) != {'status', 'answer', 'evidence', 'reason', 'alternatives'}:
        raise SearchProtocolError('schema_fields')
    status, answer, reason = obj['status'], obj['answer'], obj['reason']
    if status not in ('answer', *SEARCH_REFUSALS):
        raise SearchProtocolError('invalid_status', 'status')
    if not isinstance(answer, str):
        raise SearchProtocolError('invalid_type', 'answer')
    if len(answer) > cfg['answer_chars']:
        raise SearchProtocolError('limit_exceeded', 'answer')
    if not isinstance(reason, str):
        raise SearchProtocolError('invalid_type', 'reason')
    if len(reason) > cfg['reason_chars']:
        raise SearchProtocolError('limit_exceeded', 'reason')
    if bool(answer.strip()) != (status == 'answer'):
        raise SearchProtocolError('invalid_type', 'answer')
    if not isinstance(obj['evidence'], list):
        raise SearchProtocolError('invalid_type', 'evidence')
    if len(obj['evidence']) > cfg['evidence_items']:
        raise SearchProtocolError('limit_exceeded', 'evidence')
    for index, item in enumerate(obj['evidence']):
        if (not isinstance(item, dict) or set(item) != {'path', 'start_line', 'end_line'} or
                not isinstance(item['path'], str) or len(item['path']) > 1024 or
                not _integer(item['start_line'], 1, 2**31) or
                not _integer(item['end_line'], item['start_line'], 2**31)):
            raise SearchProtocolError('invalid_range', f'evidence[{index}]')
    alternatives = obj['alternatives']
    if not isinstance(alternatives, list):
        raise SearchProtocolError('invalid_alternatives', 'alternatives', alternative_constraint='expected_string_array')
    if len(alternatives) > cfg['alternatives_items']:
        raise SearchProtocolError('invalid_alternatives', 'alternatives', alternative_constraint='too_many_items')
    if any(not isinstance(a, str) or not 1 <= len(a) <= cfg['answer_chars'] for a in alternatives):
        raise SearchProtocolError('invalid_alternatives', 'alternatives', alternative_constraint='expected_string_array')
    if status == 'ambiguous' and len(set(alternatives)) < 2:
        raise SearchProtocolError('invalid_alternatives', 'alternatives', alternative_constraint='expected_distinct_candidates')
    if status != 'ambiguous' and alternatives:
        raise SearchProtocolError('invalid_alternatives', 'alternatives', alternative_constraint='expected_empty')
    return obj


def evaluate(text: str, access: SearchAccess) -> tuple[Verdict, dict | None]:
    try:
        obj = parse(text, access.cfg)
        if 'action' in obj:
            return Verdict(False, '', 'additional context requested', NEEDS_CONTEXT), obj
        if obj['status'] in SEARCH_REFUSALS:
            return Verdict(False, '', obj['reason'], obj['status']), None
        if not obj['evidence']:
            raise SearchProtocolError('missing_evidence', 'evidence')
        for index, item in enumerate(obj['evidence']):
            doc = access.corpus.get(item['path'])
            if doc is None:
                raise SearchProtocolError('unknown_source', f'evidence[{index}].path')
            if not access.corpus.unchanged(doc):
                raise SearchProtocolError('source_changed', f'evidence[{index}].path')
            start, end = item['start_line'], item['end_line']
            if end > len(doc.lines) or not all(i in access.visible.get(doc.path, set()) for i in range(start, end + 1)):
                raise SearchProtocolError('unseen_lines', f'evidence[{index}]')
        return Verdict(False, obj['answer'].strip(), verification='model_supported', submission_allowed=True), None
    except SearchProtocolError as exc:
        return Verdict(False, '', exc.repair_hint, PROTOCOL_ERROR, protocol_issue=exc.as_dict()), None
