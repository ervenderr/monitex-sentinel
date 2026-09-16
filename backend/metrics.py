"""Runtime counters for the operator's health strip.

Counters are the one genuinely mutable thing in the system, so all mutation is
confined to this class and readers only ever see a frozen `MetricsSnapshot`.
Everything runs on a single event loop, so plain integers are safe here.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from time import monotonic

from pydantic import BaseModel, ConfigDict

LATENCY_SAMPLE_SIZE = 200
THROUGHPUT_WINDOW_S = 10.0


class MetricsSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    events_ingested: int
    events_shed: int
    events_malformed: int
    events_triaged: int
    events_per_second: float
    queue_depth: int
    queue_capacity: int
    backpressure_waits: int
    llm_calls: int
    llm_skipped_fast_path: int
    llm_failures: int
    llm_degraded: int
    llm_overrides: int
    llm_tokens_in: int
    llm_tokens_out: int
    llm_cached_tokens: int
    llm_cache_hit_rate: float
    llm_circuit_state: str
    triage_latency_p50_ms: float
    triage_latency_p95_ms: float
    cost_usd: float
    stream_connected: bool
    stream_reconnects: int
    video_connected: bool
    video_frames_sampled: int
    video_events_emitted: int


def _percentile(sorted_samples: list[float], fraction: float) -> float:
    if not sorted_samples:
        return 0.0
    index = min(len(sorted_samples) - 1, int(round(fraction * (len(sorted_samples) - 1))))
    return sorted_samples[index]


class Metrics:
    def __init__(self) -> None:
        self.events_ingested = 0
        self.events_shed = 0
        self.events_malformed = 0
        self.events_triaged = 0
        self.backpressure_waits = 0
        self.llm_calls = 0
        self.llm_skipped_fast_path = 0
        self.llm_failures = 0
        self.llm_degraded = 0
        self.llm_overrides = 0
        self.llm_tokens_in = 0
        self.llm_tokens_out = 0
        self.llm_cached_tokens = 0
        self.cost_usd = 0.0
        self._circuit_state: Callable[[], str] = lambda: "closed"
        self.stream_connected = False
        self.stream_reconnects = 0
        self.video_connected = False
        self.video_frames_sampled = 0
        self.video_events_emitted = 0

        self._latencies: deque[float] = deque(maxlen=LATENCY_SAMPLE_SIZE)
        self._arrivals: deque[float] = deque()
        self._queue_depth: Callable[[], int] = lambda: 0
        self._queue_capacity = 0

    def bind_queue(self, depth: Callable[[], int], capacity: int) -> None:
        self._queue_depth = depth
        self._queue_capacity = capacity

    def record_ingested(self) -> None:
        self.events_ingested += 1
        self._arrivals.append(monotonic())
        self._prune_arrivals()

    def bind_circuit(self, state: Callable[[], str]) -> None:
        self._circuit_state = state

    def record_triaged(
        self,
        latency_ms: float,
        cost_usd: float,
        *,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cached_tokens: int = 0,
        overrode_baseline: bool = False,
    ) -> None:
        self.events_triaged += 1
        self._latencies.append(latency_ms)
        self.cost_usd += cost_usd
        self.llm_tokens_in += tokens_in
        self.llm_tokens_out += tokens_out
        self.llm_cached_tokens += cached_tokens
        if overrode_baseline:
            self.llm_overrides += 1

    def _prune_arrivals(self) -> None:
        cutoff = monotonic() - THROUGHPUT_WINDOW_S
        while self._arrivals and self._arrivals[0] < cutoff:
            self._arrivals.popleft()

    @property
    def events_per_second(self) -> float:
        self._prune_arrivals()
        return round(len(self._arrivals) / THROUGHPUT_WINDOW_S, 2)

    def snapshot(self) -> MetricsSnapshot:
        samples = sorted(self._latencies)
        return MetricsSnapshot(
            events_ingested=self.events_ingested,
            events_shed=self.events_shed,
            events_malformed=self.events_malformed,
            events_triaged=self.events_triaged,
            events_per_second=self.events_per_second,
            queue_depth=self._queue_depth(),
            queue_capacity=self._queue_capacity,
            backpressure_waits=self.backpressure_waits,
            llm_calls=self.llm_calls,
            llm_skipped_fast_path=self.llm_skipped_fast_path,
            llm_failures=self.llm_failures,
            llm_degraded=self.llm_degraded,
            llm_overrides=self.llm_overrides,
            llm_tokens_in=self.llm_tokens_in,
            llm_tokens_out=self.llm_tokens_out,
            llm_cached_tokens=self.llm_cached_tokens,
            llm_cache_hit_rate=(
                round(self.llm_cached_tokens / self.llm_tokens_in, 3)
                if self.llm_tokens_in
                else 0.0
            ),
            llm_circuit_state=self._circuit_state(),
            triage_latency_p50_ms=round(_percentile(samples, 0.50), 1),
            triage_latency_p95_ms=round(_percentile(samples, 0.95), 1),
            cost_usd=round(self.cost_usd, 6),
            stream_connected=self.stream_connected,
            stream_reconnects=self.stream_reconnects,
            video_connected=self.video_connected,
            video_frames_sampled=self.video_frames_sampled,
            video_events_emitted=self.video_events_emitted,
        )
