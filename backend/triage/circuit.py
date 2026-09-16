"""Circuit breaker for the LLM provider.

When a provider is down, rate-limited, or timing out, continuing to call it is
actively harmful: every alarm pays the full timeout before falling back, so the
queue backs up and the operator's board goes stale. The breaker notices the
pattern and starts failing *fast* instead - the fallback verdict is served
immediately and the pipeline keeps pace with the stream.

    closed  --failures >= threshold-->  open
    open    --after cooldown-->         half_open
    half_open --success-->              closed
    half_open --failure-->              open

Half-open admits a single trial call, so recovery costs one request rather than
a fresh stampede of them.
"""

from __future__ import annotations

from time import monotonic
from typing import Literal

CircuitState = Literal["closed", "open", "half_open"]


class CircuitOpenError(RuntimeError):
    """Raised instead of calling a provider that is known to be failing."""


class CircuitBreaker:
    def __init__(self, *, failure_threshold: int = 5, cooldown_s: float = 30.0) -> None:
        if failure_threshold <= 0:
            raise ValueError("failure_threshold must be positive")
        if cooldown_s <= 0:
            raise ValueError("cooldown_s must be positive")
        self._failure_threshold = failure_threshold
        self._cooldown_s = cooldown_s
        self._consecutive_failures = 0
        self._opened_at: float | None = None
        self._trial_in_flight = False

    @property
    def state(self) -> CircuitState:
        if self._opened_at is None:
            return "closed"
        if monotonic() - self._opened_at >= self._cooldown_s:
            return "half_open"
        return "open"

    def before_call(self) -> None:
        """Raise CircuitOpenError if the call should not be attempted."""
        state = self.state
        if state == "open":
            raise CircuitOpenError("llm provider circuit is open")
        if state == "half_open":
            if self._trial_in_flight:
                # One trial at a time; everyone else fails fast.
                raise CircuitOpenError("llm provider circuit is half-open")
            self._trial_in_flight = True

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._opened_at = None
        self._trial_in_flight = False

    def record_failure(self) -> None:
        self._trial_in_flight = False
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._failure_threshold:
            self._opened_at = monotonic()

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures
