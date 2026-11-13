"""Безопасные события отказов поискового протокола."""
from harness.search_answer import PROTOCOL_ERROR


def record_rejection(log, prepared, outcome, reply, stage, verdict):
    if log is None or verdict.kind != PROTOCOL_ERROR:
        return
    issue = verdict.protocol_issue or {}
    reason = getattr(reply, 'finish_reason', None)
    if reason not in ('stop', 'length', 'tool_calls', None):
        reason = 'other'
    record = dict(kind='search_protocol_rejected', task_id=prepared.task.task_id,
                  stage=stage, code=issue.get('code'), field=issue.get('field'),
                  output_chars=len(reply.text), finish_reason=reason,
                  repair_used=bool(outcome.protocol_repairs),
                  corpus_complete=prepared.search.corpus.complete,
                  context_complete=prepared.search.context_complete)
    try:
        log.write(record)
    except Exception:
        pass  # Диагностический журнал не меняет решение и списание.
