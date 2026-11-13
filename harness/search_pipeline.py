"""Переходы поисковых состояний; резервирование остаётся в DecisionPolicy."""
from harness.decision import STOP
from harness.search_answer import NEEDS_CONTEXT, PROTOCOL_ERROR, SEARCH_REFUSALS
from harness.success_model import RETRY, STAGE1, STAGE2
from harness.verifier import TRUNCATED, Verdict


async def recover_stage1(pipeline, prepared, outcome, verdict):
    allowed = [RETRY] if pipeline._s1.can_retry(outcome) and not outcome.recovery_count else []
    decision = pipeline._policy.branch(prepared, TRUNCATED, pipeline._lam(), allowed)
    pipeline._decided(prepared, outcome, decision, verdict.problem)
    if decision.choice == RETRY:
        outcome.recovery_count += 1
        result = await pipeline._s1.retry(prepared, outcome, verdict)
        pipeline._success.observe(RETRY, prepared.task.task_type, TRUNCATED,
                                  result.ok if result.legacy_ok is None else result.legacy_ok)
        pipeline._apply(outcome, result, RETRY)
        if not result.accepted:
            outcome.stage2_stop = 'output_limit' if result.kind == TRUNCATED else 'recovery_exhausted'
    else:
        outcome.stage2_stop = 'budget' if any(b.fits is False for b in decision.branches) else 'policy'
    return outcome


async def solve_search_pipeline(pipeline, prepared, outcome):
    verdict = await pipeline._s1.first(prepared, outcome)
    pipeline._apply(outcome, verdict, STAGE1)
    if verdict.protocol_issue and verdict.protocol_issue['code'] == 'source_changed':
        outcome.stage2_stop = 'source_changed'
        return outcome
    if verdict.accepted or verdict.kind in SEARCH_REFUSALS:
        return outcome
    if verdict.kind == TRUNCATED:
        if not prepared.search.recovery_enabled:
            outcome.stage2_stop = 'output_limit'
            return outcome
        allowed = [RETRY] if pipeline._s1.can_retry(outcome) else []
    elif verdict.kind in (NEEDS_CONTEXT, PROTOCOL_ERROR):
        allowed = [STAGE2] if pipeline._s2 else []
    else:
        return outcome
    decision = pipeline._policy.branch(prepared, verdict.kind, pipeline._lam(), allowed)
    pipeline._decided(prepared, outcome, decision, verdict.problem)
    if decision.choice == STOP:
        outcome.stage2_stop = 'budget' if any(b.fits is False for b in decision.branches) else 'policy'
        return outcome
    if decision.choice == RETRY:
        outcome.recovery_count += 1
        verdict = await pipeline._s1.retry(prepared, outcome, verdict)
        if not verdict.accepted and verdict.kind not in SEARCH_REFUSALS:
            outcome.stage2_stop = 'output_limit' if verdict.kind == TRUNCATED else 'recovery_exhausted'
        pipeline._apply(outcome, verdict, RETRY)
    else:
        verdict = await pipeline._s2.solve(prepared, outcome, verdict, decision.budget)
        pipeline._apply(outcome, verdict, STAGE2)
    return outcome
