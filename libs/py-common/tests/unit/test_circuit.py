from medbron_common.circuit import CircuitBreaker, State


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_opens_after_threshold_and_recovers() -> None:
    clock = Clock()
    cb = CircuitBreaker("t", failure_threshold=3, recovery_timeout=30, clock=clock)
    for _ in range(2):
        assert cb.allow()
        cb.record_failure()
    assert cb.state is State.CLOSED
    cb.record_failure()
    assert cb.state is State.OPEN and not cb.allow()

    clock.now = 29.9
    assert not cb.allow()
    clock.now = 30
    assert cb.state is State.HALF_OPEN
    assert cb.allow()  # bitta sinov
    assert not cb.allow()  # ikkinchisi kutadi
    cb.record_success()
    assert cb.state is State.CLOSED and cb.allow()


def test_half_open_failure_reopens() -> None:
    clock = Clock()
    cb = CircuitBreaker("t", failure_threshold=1, recovery_timeout=10, clock=clock)
    cb.record_failure()
    clock.now = 10
    assert cb.allow()
    cb.record_failure()
    assert cb.state is State.OPEN
    clock.now = 15
    assert not cb.allow()


def test_success_resets_failure_count() -> None:
    cb = CircuitBreaker("t", failure_threshold=2)
    cb.record_failure()
    cb.record_success()
    cb.record_failure()
    assert cb.state is State.CLOSED
