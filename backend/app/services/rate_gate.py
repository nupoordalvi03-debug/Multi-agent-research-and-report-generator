"""Process-wide request pacing + concurrency limit + shared cooldown.

One gate per (provider, model) — or per API such as TinyFish Search — so ALL agents and ALL
sessions share the same budget. When one call gets HTTP 429, `cooldown()` pauses every caller,
instead of letting concurrent requests keep hitting the limit."""
from __future__ import annotations

import asyncio
import time
import weakref
from typing import Awaitable, Callable

# loop -> {key: gate}. Keyed by the loop OBJECT (weakly): id(loop) can be reused after a loop is
# garbage-collected, which would hand a new loop a stale gate with a stale cooldown.
_gates: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict]" = weakref.WeakKeyDictionary()


class RateGate:
    def __init__(self, rpm: float, concurrency: int):
        self.min_interval = 60.0 / rpm if rpm and rpm > 0 else 0.0
        self.sem = asyncio.Semaphore(max(1, concurrency))
        self.lock = asyncio.Lock()
        self.next_slot = 0.0
        self.cooldown_until = 0.0

    async def acquire(self, sleep: Callable[[float], Awaitable] = asyncio.sleep,
                      on_wait: Callable[[float, str], None] | None = None) -> None:
        await self.sem.acquire()
        try:
            async with self.lock:
                now = time.monotonic()
                start = max(now, self.next_slot, self.cooldown_until)
                self.next_slot = start + self.min_interval
            delay = start - now
            if delay > 0:
                if on_wait and delay >= 3:
                    reason = "cooldown" if self.cooldown_until > now else "pacing"
                    on_wait(delay, reason)
                await sleep(delay)
        except BaseException:
            self.sem.release()
            raise

    def release(self) -> None:
        self.sem.release()

    def cooldown(self, seconds: float) -> None:
        self.cooldown_until = max(self.cooldown_until, time.monotonic() + seconds)


def get_gate(key: tuple, rpm: float, concurrency: int) -> RateGate:
    """Gates are per event loop (asyncio primitives cannot cross loops)."""
    per_loop = _gates.setdefault(asyncio.get_running_loop(), {})
    g = per_loop.get(key)
    if g is None:
        g = per_loop[key] = RateGate(rpm, concurrency)
    return g
