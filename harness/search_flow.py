"""Поисковый цикл внутри существующего бюджета и числа шагов этапа 2."""
from harness.prompting import prompt_chars
from harness.search_answer import NEEDS_CONTEXT, PROTOCOL_ERROR, SEARCH_REFUSALS, evaluate
from harness.settings import load_prompt
from harness.search_diagnostics import record_rejection
from harness.verifier import TRUNCATED, Verdict


def truncated(reply, cap: int | None) -> bool:
    reason = getattr(reply, 'finish_reason', None)
    return reason == 'length' or (reason not in ('stop', 'tool_calls') and cap is not None and
                                  reply.usage.output_tokens >= cap)


def repair(problem: str) -> dict:
    return dict(role='user', content=load_prompt('search_repair.txt').format(problem=problem))


def repair_verdict(verdict):
    issue = verdict.protocol_issue
    problem = (f"{issue['code']} at {issue['field']}: {issue['repair_hint']}"
               if issue else verdict.problem)
    return repair(problem)


def source_changed(verdict):
    return verdict.protocol_issue and verdict.protocol_issue['code'] == 'source_changed'


async def solve_search(solver, prepared, outcome, failed, budget):
    access = prepared.search
    messages = list(outcome.messages)
    verdict = failed
    spent_before = outcome.cost
    if source_changed(failed):
        return solver._stop(outcome, 'source_changed', failed)
    if failed.kind == NEEDS_CONTEXT:
        verdict, request = evaluate(messages[-1]['content'], access)
        if request:
            messages.append(dict(role='user', content=access.request(request)))
    else:
        if outcome.protocol_repairs:
            return solver._stop(outcome, PROTOCOL_ERROR, failed)
        outcome.protocol_repairs += 1
        messages.append(repair_verdict(failed))
    for step in range(1, solver._max_steps + 1):
        if outcome.cost - spent_before + solver._cost.stage2_step('search', prompt_chars(messages)) > budget:
            return solver._stop(outcome, 'budget', Verdict(False, '', 'search branch budget', 'budget'))
        stage = f'stage2.{step}'
        reply = await solver._gw.ask(solver._model, messages, task_id=prepared.task.task_id,
                                     stage=stage, max_tokens=solver._max_tokens)
        outcome.add_call(solver._model, stage, reply)
        outcome.stage2_steps = step
        messages.append(dict(role='assistant', content=reply.text))
        if truncated(reply, solver._max_tokens):
            outcome.limit_reason = 'confirmed_length' if getattr(reply, 'finish_reason', None) == 'length' else 'suspected_limit'
            verdict = Verdict(False, '', 'output limit', TRUNCATED)
            if not access.recovery_enabled or outcome.recovery_count:
                return solver._stop(outcome, 'output_limit', verdict)
            outcome.recovery_count += 1
            messages.append(repair('output limit; use one compact JSON object'))
            continue
        verdict, request = evaluate(reply.text, access)
        record_rejection(getattr(solver, '_log', None), prepared, outcome, reply, stage, verdict)
        if source_changed(verdict):
            return solver._stop(outcome, 'source_changed', verdict)
        if verdict.accepted or verdict.kind in SEARCH_REFUSALS:
            return solver._stop(outcome, verdict.verification or verdict.kind, verdict)
        if request:
            messages.append(dict(role='user', content=access.request(request)))
        elif verdict.kind == PROTOCOL_ERROR:
            if outcome.protocol_repairs:
                return solver._stop(outcome, PROTOCOL_ERROR, verdict)
            outcome.protocol_repairs += 1
            messages.append(repair_verdict(verdict))
    return solver._stop(outcome, 'max_steps', Verdict(False, '', 'search step limit', 'max_steps'))
