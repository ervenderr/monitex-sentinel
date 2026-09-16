"""Calibration cases, including the one the brief calls out by name."""

from __future__ import annotations

import pytest

from backend.triage import rules
from tests.conftest import make_event

NOON = "2026-09-16T13:00:00Z"
NIGHT = "2026-09-16T03:00:00Z"


def test_the_brief_example_low_confidence_motion_at_noon_is_noise() -> None:
    event = make_event(type="motion_detected", confidence=0.38, timestamp=NOON)
    result = rules.triage(event)
    assert result.severity == "info"
    assert result.is_real_threat is False


@pytest.mark.parametrize("event_type", ["panic_button", "fire_alarm"])
def test_the_brief_example_panic_and_fire_are_always_critical(event_type: str) -> None:
    # Critical even at the lowest confidence the feed produces.
    event = make_event(type=event_type, confidence=0.35, timestamp=NOON)
    assert rules.triage(event).severity == "critical"
    assert rules.is_fast_path(event) is True


def test_fast_path_is_narrow() -> None:
    # Anything ambiguous must still reach the LLM.
    assert rules.is_fast_path(make_event(type="perimeter_breach")) is False
    assert rules.is_fast_path(make_event(type="smoke_detected")) is False


def test_after_hours_escalates_intrusion() -> None:
    day = rules.triage(make_event(type="loitering", confidence=0.88, timestamp=NOON))
    night = rules.triage(make_event(type="loitering", confidence=0.88, timestamp=NIGHT))
    assert day.severity == "warning"
    assert night.severity == "critical"


def test_confidence_only_escalates_forced_entry() -> None:
    forced = rules.triage(make_event(type="door_forced", confidence=0.95, timestamp=NOON))
    loiter = rules.triage(make_event(type="loitering", confidence=0.95, timestamp=NOON))
    assert forced.severity == "critical"
    assert loiter.severity == "warning"


def test_low_confidence_downgrades() -> None:
    result = rules.triage(make_event(type="perimeter_breach", confidence=0.2, timestamp=NOON))
    assert result.severity == "info"


def test_unknown_type_defaults_to_warning_not_dropped() -> None:
    result = rules.triage(make_event(type="alien_landing", confidence=0.7, timestamp=NOON))
    assert result.severity == "warning"
    assert "unrecognised" in result.reasoning


def test_missing_confidence_is_treated_as_uncertain() -> None:
    result = rules.triage(make_event(type="smoke_detected", confidence=None, timestamp=NOON))
    assert result.severity == "warning"


def test_fallback_is_flagged_degraded() -> None:
    result = rules.triage(make_event(), degraded=True)
    assert result.degraded is True
    assert result.origin == "fallback"


def test_every_known_type_has_an_action() -> None:
    for event_type in rules.BASE_SEVERITY:
        result = rules.triage(make_event(type=event_type))
        assert result.recommended_action
        assert result.summary
