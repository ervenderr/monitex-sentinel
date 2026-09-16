"""Circuit breaker state machine, driven by a fake clock."""

from __future__ import annotations

import pytest

from backend.triage import circuit as circuit_module
from backend.triage.circuit import CircuitBreaker, CircuitOpenError


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch):
    class Clock:
        now = 1000.0

        def advance(self, seconds: float) -> None:
            self.now += seconds

    fake = Clock()
    monkeypatch.setattr(circuit_module, "monotonic", lambda: fake.now)
    return fake


def _trip(breaker: CircuitBreaker, times: int) -> None:
    for _ in range(times):
        breaker.before_call()
        breaker.record_failure()


def test_starts_closed_and_allows_calls(clock) -> None:
    breaker = CircuitBreaker(failure_threshold=3, cooldown_s=30)
    assert breaker.state == "closed"
    breaker.before_call()  # must not raise


def test_opens_after_the_threshold(clock) -> None:
    breaker = CircuitBreaker(failure_threshold=3, cooldown_s=30)
    _trip(breaker, 2)
    assert breaker.state == "closed", "must not open early"
    _trip(breaker, 1)
    assert breaker.state == "open"


def test_open_circuit_fails_fast(clock) -> None:
    breaker = CircuitBreaker(failure_threshold=1, cooldown_s=30)
    _trip(breaker, 1)
    with pytest.raises(CircuitOpenError):
        breaker.before_call()


def test_success_resets_the_failure_run(clock) -> None:
    breaker = CircuitBreaker(failure_threshold=3, cooldown_s=30)
    _trip(breaker, 2)
    breaker.record_success()
    assert breaker.consecutive_failures == 0
    _trip(breaker, 2)
    assert breaker.state == "closed", "failures must be consecutive to trip it"


def test_half_opens_after_cooldown(clock) -> None:
    breaker = CircuitBreaker(failure_threshold=1, cooldown_s=30)
    _trip(breaker, 1)
    clock.advance(29)
    assert breaker.state == "open"
    clock.advance(2)
    assert breaker.state == "half_open"


def test_half_open_admits_exactly_one_trial(clock) -> None:
    breaker = CircuitBreaker(failure_threshold=1, cooldown_s=30)
    _trip(breaker, 1)
    clock.advance(31)

    breaker.before_call()  # the trial
    with pytest.raises(CircuitOpenError):
        breaker.before_call()  # everyone else still fails fast


def test_successful_trial_closes_the_circuit(clock) -> None:
    breaker = CircuitBreaker(failure_threshold=1, cooldown_s=30)
    _trip(breaker, 1)
    clock.advance(31)
    breaker.before_call()
    breaker.record_success()
    assert breaker.state == "closed"


def test_failed_trial_reopens_for_another_cooldown(clock) -> None:
    breaker = CircuitBreaker(failure_threshold=1, cooldown_s=30)
    _trip(breaker, 1)
    clock.advance(31)
    breaker.before_call()
    breaker.record_failure()
    assert breaker.state == "open"
    clock.advance(31)
    assert breaker.state == "half_open"


@pytest.mark.parametrize(
    "kwargs", [{"failure_threshold": 0}, {"cooldown_s": 0}, {"failure_threshold": -1}]
)
def test_rejects_nonsense_configuration(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        CircuitBreaker(**kwargs)
