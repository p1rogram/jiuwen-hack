"""Телеметрия попыток без содержимого запросов; сбой наблюдения не повторяет API."""
from uuid import uuid4


class AttemptTrace:
    def __init__(self, log, call, clock):
        self.log, self.clock = log, clock
        self.fields = {"call_id": uuid4().hex, "task_id": call.task_id,
                       "stage": call.stage, "model": call.model}
        self.attempt = 0

    def write(self, kind, **fields):
        try:
            self.log.write({"kind": kind, **self.fields, "attempt": self.attempt,
                            "monotonic_sec": self.clock(), **fields})
        except Exception:
            # Сбой нового журнала не должен отменять оплаченный успех или вызывать повтор.
            pass

    def start(self, attempt, waited, deadline):
        self.attempt = attempt
        self.write("llm_attempt_start", **waited,
                   cooldown_deadline=deadline)

    def finish(self, error_class=None, rate_wait=None, deadline=0.0):
        self.write("llm_attempt_end", error_class=error_class,
                   rate_limit_wait_sec=rate_wait, cooldown_deadline=deadline)
