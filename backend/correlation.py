"""Pattern-based escalation across alarms at one site.

The brief's own example: repeated perimeter breaches at one site in a short
window are a materially different risk than any single one of them, even
though each individually might only be a warning. A lone 0.6-confidence
motion hit at a loading dock is noise; three of them in two minutes, or a
warning-level hit right after a breach, is a pattern an operator should not
have to notice by eye while scanning a scrolling board.

Deliberately monotonic: this only ever raises severity, never lowers it, and
never re-escalates an alarm that is already critical - there is nothing above
critical to escalate to, and a rule-driven fire_alarm has already claimed the
operator's attention on its own.

Evaluated exactly once per alarm, at the moment its triage becomes final (see
intake.py's fast path and triage/worker.py's LLM path) - never on a
preliminary verdict, which would double-count the same alarm's sighting once
before the LLM replies and once after.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from time import monotonic

from backend.models import AlarmRecord, Severity

DEFAULT_THRESHOLD = 3
DEFAULT_WINDOW_S = 120.0

# Info-severity events are exactly the noise triage exists to filter out;
# counting them toward a pattern would defeat the point of classifying them
# as noise in the first place.
COUNTED_SEVERITIES: frozenset[Severity] = frozenset({"warning", "critical"})


@dataclass(frozen=True)
class _Sighting:
    at: float
    event_type: str


class CorrelationEngine:
    def __init__(
        self, *, threshold: int = DEFAULT_THRESHOLD, window_s: float = DEFAULT_WINDOW_S
    ) -> None:
        if threshold < 2:
            raise ValueError("threshold must be at least 2 - one event is not a pattern")
        if window_s <= 0:
            raise ValueError("window_s must be positive")
        self._threshold = threshold
        self._window_s = window_s
        self._by_site: dict[str, deque[_Sighting]] = {}

    def _window_for(self, site_id: str, now: float) -> deque[_Sighting]:
        window = self._by_site.setdefault(site_id, deque())
        cutoff = now - self._window_s
        while window and window[0].at < cutoff:
            window.popleft()
        return window

    def evaluate(self, record: AlarmRecord) -> AlarmRecord:
        """Record this alarm's sighting and escalate it if the site's pattern
        just crossed the threshold. Returns the record unchanged unless it
        escalates - every path here is immutable, matching the rest of the
        alarm lifecycle.
        """
        severity = record.triage.severity
        site_id = record.event.site_id
        now = monotonic()

        window = self._window_for(site_id, now)
        if severity in COUNTED_SEVERITIES:
            window.append(_Sighting(at=now, event_type=record.event.type))

        if severity == "critical" or len(window) < self._threshold:
            return record

        reason = self._describe(window, site_id)
        escalated_triage = record.triage.model_copy(
            update={
                "severity": "critical",
                "is_real_threat": True,
                "reasoning": f"Escalated - {reason}. {record.triage.reasoning}".strip(),
            }
        )
        return record.with_triage(
            escalated_triage, state=record.triage_state
        ).with_escalation(reason)

    def _describe(self, window: deque[_Sighting], site_id: str) -> str:
        types = {s.event_type for s in window}
        window_s = int(self._window_s)
        if len(types) == 1:
            label = next(iter(types)).replace("_", " ")
            return f"{len(window)}x {label} at {site_id} within {window_s}s"
        labels = ", ".join(sorted(t.replace("_", " ") for t in types))
        return f"{len(window)} alarms at {site_id} within {window_s}s ({labels})"
