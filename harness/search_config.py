"""Единые технические лимиты поиска и проверка сочетаний флагов."""
LIMITS = dict(scan_files=1000, scan_bytes=8 * 1024 * 1024, manifest_page_files=100,
              read_max_lines=80, tool_response_chars=6000, query_chars=256,
              answer_chars=2000, reason_chars=1000, evidence_items=16, alternatives_items=16)


def validate(raw: dict) -> None:
    retrieval = raw.get('search_retrieval', {}).get('enabled', False)
    evidence = raw.get('search_evidence', {}).get('enabled', False)
    recovery = raw.get('bounded_recovery', {}).get('enabled', False)
    if (evidence and not retrieval) or (recovery and not evidence):
        raise ValueError('search evidence requires retrieval; bounded recovery requires evidence')
    for key, default in LIMITS.items():
        value = raw.get('search_retrieval', {}).get(key, default)
        if type(value) is not int or value < 1:
            raise ValueError(f'invalid search limit: {key}')
