"""Domain models for the alarm pipeline.

Everything here is frozen. State transitions (acknowledge, resolve, triage
upgrade) return a new record via `model_copy` rather than mutating in place,
so a record handed to an SSE subscriber can never change underneath it.

The event feed is untrusted input: fields go missing, confidence arrives as a
string, timestamps are malformed. `normalise_payload` absorbs all of that at
the boundary so nothing downstream has to defend itself.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Severity = Literal["info", "warning", "critical"]
SEVERITY_RANK: dict[Severity, int] = {"info": 1, "warning": 2, "critical": 3}

AlarmStatus = Literal["active", "acknowledged", "resolved"]
TriageOrigin = Literal["rules", "llm", "fallback"]
TriageState = Literal["preliminary", "final"]
EventSource = Literal["sensor", "camera"]

KNOWN_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "motion_detected",
        "perimeter_breach",
        "door_forced",
        "glass_break",
        "smoke_detected",
        "fire_alarm",
        "object_detected",
        "loitering",
        "camera_offline",
        "sensor_fault",
        "panic_button",
    }
)

UNKNOWN_TYPE = "unknown"
UNKNOWN_FIELD = "unknown"


def utcnow() -> datetime:
    return datetime.now(UTC)


def _coerce_str(value: Any, fallback: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def _coerce_confidence(value: Any) -> float | None:
    """Confidence is advisory and frequently junk. Never let it raise."""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed:  # NaN
        return None
    return max(0.0, min(1.0, parsed))


def _coerce_timestamp(value: Any) -> datetime:
    """Fall back to arrival time rather than rejecting the event."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return utcnow()
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return utcnow()


def normalise_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Coerce a raw feed payload into something RawEvent can always accept."""
    event_type = _coerce_str(payload.get("type"), UNKNOWN_TYPE)
    source = payload.get("source")
    if source not in ("sensor", "camera"):
        source = "camera" if event_type in ("object_detected", "loitering") else "sensor"

    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    snapshot_url = payload.get("snapshot_url")
    if not isinstance(snapshot_url, str) or not snapshot_url.strip():
        snapshot_url = None

    return {
        "event_id": _coerce_str(payload.get("event_id"), f"evt_{uuid.uuid4().hex[:10]}"),
        "site_id": _coerce_str(payload.get("site_id"), UNKNOWN_FIELD),
        "zone": _coerce_str(payload.get("zone"), UNKNOWN_FIELD),
        "type": event_type,
        "source": source,
        "confidence": _coerce_confidence(payload.get("confidence")),
        "timestamp": _coerce_timestamp(payload.get("timestamp")),
        "snapshot_url": snapshot_url,
        "metadata": metadata,
    }


class RawEvent(BaseModel):
    """A single alarm as it entered the system."""

    model_config = ConfigDict(frozen=True)

    event_id: str
    site_id: str
    zone: str
    type: str
    source: EventSource
    confidence: float | None = None
    timestamp: datetime
    snapshot_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    received_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="before")
    @classmethod
    def _normalise(cls, value: Any) -> Any:
        if isinstance(value, dict):
            received_at = value.get("received_at")
            normalised = normalise_payload(value)
            if received_at is not None:
                normalised["received_at"] = received_at
            return normalised
        return value

    @property
    def is_known_type(self) -> bool:
        return self.type in KNOWN_EVENT_TYPES


class TriageResult(BaseModel):
    """The verdict on one alarm, and an audit trail of how we reached it."""

    model_config = ConfigDict(frozen=True)

    severity: Severity
    is_real_threat: bool
    summary: str
    recommended_action: str
    reasoning: str = ""
    origin: TriageOrigin
    degraded: bool = False
    model: str | None = None
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    # True when the model disagreed with the rule baseline. Tracked because a
    # model that never overrides is not earning its cost, and one that always
    # overrides is miscalibrated.
    overrode_baseline: bool = False

    @property
    def rank(self) -> int:
        return SEVERITY_RANK[self.severity]


class AlarmRecord(BaseModel):
    """An alarm plus its triage verdict and operator state."""

    model_config = ConfigDict(frozen=True)

    event: RawEvent
    triage: TriageResult
    triage_state: TriageState = "preliminary"
    status: AlarmStatus = "active"
    escalation: str | None = None
    acknowledged_at: datetime | None = None
    acknowledged_by: str | None = None
    resolved_at: datetime | None = None
    resolved_by: str | None = None
    updated_at: datetime = Field(default_factory=utcnow)

    @property
    def event_id(self) -> str:
        return self.event.event_id

    @property
    def sort_key(self) -> tuple[int, int, float]:
        """Rank for the operator board: unresolved first, then severity, then recency."""
        open_first = 0 if self.status == "resolved" else 1
        return (open_first, self.triage.rank, self.event.received_at.timestamp())

    def with_triage(self, triage: TriageResult, state: TriageState = "final") -> AlarmRecord:
        return self.model_copy(
            update={"triage": triage, "triage_state": state, "updated_at": utcnow()}
        )

    def with_escalation(self, escalation: str | None) -> AlarmRecord:
        return self.model_copy(update={"escalation": escalation, "updated_at": utcnow()})

    def acknowledged(self, by: str) -> AlarmRecord:
        now = utcnow()
        return self.model_copy(
            update={
                "status": "acknowledged",
                "acknowledged_at": now,
                "acknowledged_by": by,
                "updated_at": now,
            }
        )

    def resolved(self, by: str) -> AlarmRecord:
        now = utcnow()
        return self.model_copy(
            update={
                "status": "resolved",
                "resolved_at": now,
                "resolved_by": by,
                # Resolving without acknowledging still records who saw it.
                "acknowledged_at": self.acknowledged_at or now,
                "acknowledged_by": self.acknowledged_by or by,
                "updated_at": now,
            }
        )
