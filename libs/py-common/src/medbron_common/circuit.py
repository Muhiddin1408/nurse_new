"""Circuit breaker (BLUEPRINT 7.2): N ta ketma-ket xato → `open` (so'rov yuborilmaydi) →
`recovery_timeout` dan keyin `half_open` (bitta sinov so'rovi) → muvaffaqiyat bo'lsa `closed`.

Nega kerak: yiqilgan servisni har so'rovda timeout kutib "bo'g'ish" chaqiruvchining
o'zini ham yiqitadi (worker'lar band, navbat o'sadi). Ochiq zanjir darhol rad etadi.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from enum import StrEnum

from medbron_common.metrics import CIRCUIT_STATE


class State(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold >= 1 bo'lishi kerak")
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._clock = clock
        self._state = State.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._probe_in_flight = False

    @property
    def state(self) -> State:
        if self._state is State.OPEN and self._clock() - self._opened_at >= self.recovery_timeout:
            self._state = State.HALF_OPEN
            self._probe_in_flight = False
        return self._state

    def allow(self) -> bool:
        state = self.state
        if state is State.CLOSED:
            return True
        if state is State.HALF_OPEN and not self._probe_in_flight:
            self._probe_in_flight = True
            return True
        return False

    def record_success(self) -> None:
        self._failures = 0
        self._probe_in_flight = False
        if self._state is not State.CLOSED:
            self._state = State.CLOSED
            CIRCUIT_STATE.labels(self.name).set(0)

    def record_failure(self) -> None:
        self._probe_in_flight = False
        if self._state is State.HALF_OPEN:
            self._open()
            return
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._open()

    def _open(self) -> None:
        self._state = State.OPEN
        self._opened_at = self._clock()
        self._failures = 0
        CIRCUIT_STATE.labels(self.name).set(1)
