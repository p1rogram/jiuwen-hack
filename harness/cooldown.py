"""Общая пауза шлюза без удержания слотов и изменения политики повторов."""
import asyncio
import time
from contextlib import asynccontextmanager


class SharedCooldown:
    def __init__(self, enabled: bool, clock=time.monotonic, sleep=asyncio.sleep):
        self.enabled = enabled
        self.clock = clock
        self.sleep = sleep
        self.deadline = 0.0

    def remaining(self) -> float:
        return max(0.0, self.deadline - self.clock()) if self.enabled else 0.0

    def extend(self, wait: float) -> None:
        if self.enabled:
            self.deadline = max(self.deadline, self.clock() + wait)

    async def wait(self) -> None:
        while self.remaining() > 0:
            await self.sleep(self.remaining())

    @asynccontextmanager
    async def slot(self, semaphore):
        started = self.clock()
        gate_wait = 0.0
        while True:
            gate_started = self.clock()
            await self.wait()
            gate_wait += self.clock() - gate_started
            await semaphore.acquire()
            # Deadline мог продлиться, пока запрос ждал слот; ждать внутри слота нельзя.
            if self.remaining() > 0:
                semaphore.release()
                continue
            break
        try:
            yield {"gate_wait_sec": gate_wait, "gate_and_slot_wait_sec": self.clock() - started}
        finally:
            semaphore.release()
