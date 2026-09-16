"""Deterministic rule classifier.

This does three jobs, which is why it earns its place next to the LLM:

1. **Preliminary verdict** - runs in microseconds at ingest, so an alarm appears
   on the operator board immediately instead of waiting on an API round-trip.
2. **Fast path** - a handful of event types are unambiguous (`fire_alarm`,
   `panic_button`). Sending those to an LLM buys nothing and costs latency and
   money, so they skip it entirely.
3. **Fallback** - when the LLM times out, rate-limits, or returns junk, this is
   what the operator sees instead of an error. Degraded, never blank.

Severity is deliberately *not* a pure function of event type. A 0.38-confidence
`motion_detected` at 13:00 is noise; the same event at 03:00 is worth a look.
That judgement is what the LLM layer refines - these rules just have to be
defensible on their own.
"""

from __future__ import annotations

from datetime import datetime

from backend.models import RawEvent, Severity, TriageResult

# Unambiguous by definition: a human pressed the button, or the fire panel fired.
# No language model is going to improve on this, so we skip the call.
FAST_PATH_TYPES: frozenset[str] = frozenset({"fire_alarm", "panic_button"})

# Baseline severity before confidence and time-of-day adjustments.
BASE_SEVERITY: dict[str, Severity] = {
    "fire_alarm": "critical",
    "panic_button": "critical",
    "smoke_detected": "critical",
    "glass_break": "warning",
    "door_forced": "warning",
    "perimeter_breach": "warning",
    "loitering": "warning",
    "camera_offline": "warning",
    "motion_detected": "info",
    "object_detected": "info",
    "sensor_fault": "info",
}

# Intrusion-shaped events that mean more outside business hours.
INTRUSION_TYPES: frozenset[str] = frozenset(
    {"perimeter_breach", "door_forced", "glass_break", "loitering", "motion_detected"}
)

# Only these escalate to critical on detector confidence alone. A confidently
# detected loiterer is still just a loiterer - confidence tells us the detector
# is sure *what* it saw, not that what it saw is an emergency. Forced entry is
# different: if the detector is sure, someone is coming through the door.
FORCED_ENTRY_TYPES: frozenset[str] = frozenset(
    {"perimeter_breach", "door_forced", "glass_break"}
)

# Below this, a detector is guessing.
LOW_CONFIDENCE = 0.5
HIGH_CONFIDENCE = 0.85

AFTER_HOURS_START = 22  # inclusive
AFTER_HOURS_END = 5  # exclusive

RECOMMENDED_ACTIONS: dict[str, str] = {
    "fire_alarm": "Dispatch fire service and initiate evacuation protocol now.",
    "panic_button": "Call the site contact and dispatch a guard immediately.",
    "smoke_detected": "Verify on camera and prepare to dispatch fire service.",
    "glass_break": "Pull up the zone camera and verify forced entry.",
    "door_forced": "Verify on camera and dispatch a guard if entry is confirmed.",
    "perimeter_breach": "Check the perimeter camera for an intruder before dispatching.",
    "loitering": "Watch the zone feed for two minutes before escalating.",
    "camera_offline": "Raise a maintenance ticket and check the adjacent camera for coverage.",
    "motion_detected": "Glance at the zone feed; no action if the area is occupied.",
    "object_detected": "Confirm the detected object on camera.",
    "sensor_fault": "Log for maintenance; no operator action required.",
}

DEFAULT_ACTION = "Review the zone feed and confirm before escalating."

_SEVERITY_ORDER: tuple[Severity, ...] = ("info", "warning", "critical")


def _shift(severity: Severity, steps: int) -> Severity:
    index = _SEVERITY_ORDER.index(severity) + steps
    return _SEVERITY_ORDER[max(0, min(len(_SEVERITY_ORDER) - 1, index))]


def is_after_hours(moment: datetime) -> bool:
    hour = moment.hour
    return hour >= AFTER_HOURS_START or hour < AFTER_HOURS_END


def is_fast_path(event: RawEvent) -> bool:
    """True when the rules are authoritative and the LLM call can be skipped."""
    return event.type in FAST_PATH_TYPES


def classify(event: RawEvent) -> tuple[Severity, bool, str]:
    """Return (severity, is_real_threat, reasoning) for one event."""
    severity = BASE_SEVERITY.get(event.type, "warning")
    reasons: list[str] = [f"base severity for {event.type} is {severity}"]

    confidence = event.confidence
    after_hours = is_after_hours(event.timestamp)

    # Unknown types keep their conservative 'warning' default - we would rather
    # an operator glance at something harmless than miss a new event type.
    if not event.is_known_type:
        reasons.append("unrecognised event type, defaulting to warning")

    if event.type not in FAST_PATH_TYPES:
        if confidence is None:
            reasons.append("no confidence reported, treated as uncertain")
            severity = _shift(severity, -1) if severity == "critical" else severity
        elif confidence < LOW_CONFIDENCE:
            severity = _shift(severity, -1)
            reasons.append(f"low detector confidence ({confidence:.2f}) downgrades severity")
        elif (
            confidence >= HIGH_CONFIDENCE
            and severity == "warning"
            and event.type in FORCED_ENTRY_TYPES
        ):
            severity = _shift(severity, 1)
            reasons.append(f"high detector confidence ({confidence:.2f}) upgrades severity")

        if after_hours and event.type in INTRUSION_TYPES and severity != "info":
            severity = _shift(severity, 1)
            reasons.append("occurred outside business hours")

    is_real_threat = severity == "critical" or (
        severity == "warning" and (confidence or 0.0) >= LOW_CONFIDENCE
    )

    return severity, is_real_threat, "; ".join(reasons)


def summarise(event: RawEvent, severity: Severity) -> str:
    """One line an operator can act on without reading the raw event."""
    label = event.type.replace("_", " ")
    confidence = (
        f"{event.confidence:.0%} confidence"
        if event.confidence is not None
        else "no confidence reported"
    )
    return f"{label.capitalize()} in {event.zone} at {event.site_id} ({confidence})."


def triage(event: RawEvent, *, degraded: bool = False) -> TriageResult:
    """Full rule-based verdict. Used for preliminary, fast-path, and fallback."""
    severity, is_real_threat, reasoning = classify(event)
    return TriageResult(
        severity=severity,
        is_real_threat=is_real_threat,
        summary=summarise(event, severity),
        recommended_action=RECOMMENDED_ACTIONS.get(event.type, DEFAULT_ACTION),
        reasoning=reasoning,
        origin="fallback" if degraded else "rules",
        degraded=degraded,
        model=None,
    )
