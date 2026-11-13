"""Контролируемая диагностика протокола без фрагментов ответа модели."""
import re


ISSUES = {
    'json_syntax': ('single_json_object', 'Return one complete JSON object; remove text outside it'),
    'duplicate_field': ('unique_fields', 'Include each field once'),
    'schema_fields': ('required_fields', 'Use exactly the required protocol fields'),
    'invalid_type': ('field_type', 'Use the required JSON type for this field'),
    'invalid_status': ('known_status', 'Use answer, ambiguous, or insufficient'),
    'invalid_range': ('positive_ordered_range', 'Use positive integer line numbers with start<=end'),
    'limit_exceeded': ('configured_limit', 'Keep the field within its configured limit'),
    'unknown_source': ('corpus_source', 'Cite a path shown by list/read/snapshot'),
    'source_changed': ('unchanged_source', 'Evidence source changed; do not reuse this citation'),
    'unseen_lines': ('visible_lines', 'Read the cited range before submitting'),
    'missing_evidence': ('supported_answer', 'Cite at least one visible source range'),
    'invalid_alternatives': ('distinct_alternatives', 'Only ambiguous requires at least two distinct alternatives'),
}


ALTERNATIVE_HINTS = {
    'expected_string_array': 'Use an array of nonempty strings within the answer length limit',
    'too_many_items': 'Keep alternatives within the configured item limit',
    'expected_empty': 'Set alternatives to [] for answer or insufficient status',
    'expected_distinct_candidates': 'For ambiguous status provide at least two distinct candidate strings',
}


class SearchProtocolError(ValueError):
    def __init__(self, code: str, field: str = '$', *, alternative_constraint: str | None = None):
        constraint, hint = ISSUES[code]
        if alternative_constraint is not None:
            if code != 'invalid_alternatives' or alternative_constraint not in ALTERNATIVE_HINTS:
                raise ValueError('invalid alternative constraint')
            constraint, hint = alternative_constraint, ALTERNATIVE_HINTS[alternative_constraint]
        if not re.fullmatch(r'\$|action|cursor|query|path|start_line|max_lines|status|answer|reason|alternatives|evidence(?:\[\d{1,10}\](?:\.path)?)?', field):
            raise ValueError('invalid diagnostic field')
        self.code, self.field = code, field
        self.constraint, self.repair_hint = constraint, hint
        super().__init__(hint)

    def as_dict(self):
        return dict(code=self.code, field=self.field, constraint=self.constraint, repair_hint=self.repair_hint)
