import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

class State(str, Enum):
    CLOSED = "closed"  # normal ish
    OPEN = "open"  # servis o'lgan, umuman urinmaymiz
    HALF_OPEN = "half_open"  # tiklandimi, bitta so'rov bilan tekshiramiz


@dataclass
class CircuitBreaker:
    """Yiqilgan servisga urinishni butunlay to'xtatadi.

    NEGA KERAK: servis o'lganda har bir so'rov timeout'ni to'liq kutadi.
    Sekundiga 100 so'rov kelsa, 2 soniyalik timeout bilan 200 ta so'rov
    bir vaqtda osilib turadi — thread'lar tugaydi va SIZ ham o'lasiz.

    Circuit breaker buni oldini oladi: bir necha xatodan keyin u "ochiladi"
    va keyingi so'rovlar tarmoqqa umuman chiqmay, DARHOL xato qaytaradi.
    Bir muddatdan keyin bitta sinov so'rovini o'tkazadi (half-open).
    """

    failure_threshold: int = 5
    recovery_timeout: float = 30.0

    _state: State = State.CLOSED
    _failures: int = 0
    _opened_at: float = 0.0
    _lock: threading.Lock = None  # type: ignore[assignment]

    def __post_init__(self):
        self._lock = threading.Lock()

    @property
    def state(self) -> State:
        with self._lock:
            if self._state == State.OPEN:
                if time.monotonic() - self._opened_at >= self.recovery_timeout:
                    self._state = State.HALF_OPEN
                    logger.info("Circuit breaker: OPEN -> HALF_OPEN")
            return self._state

    def allow(self) -> bool:
        return self.state != State.OPEN

    def record_success(self) -> None:
        with self._lock:
            if self._state != State.CLOSED:
                logger.info("Circuit breaker: %s -> CLOSED", self._state)
            self._state = State.CLOSED
            self._failures = 0

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            # HALF_OPEN da bitta xato yetarli — darhol qayta yopamiz
            if self._state == State.HALF_OPEN or self._failures >= self.failure_threshold:
                self._state = State.OPEN
                self._opened_at = time.monotonic()
                logger.warning("Circuit breaker OCHILDI (%s xato)", self._failures)


_breaker = CircuitBreaker()