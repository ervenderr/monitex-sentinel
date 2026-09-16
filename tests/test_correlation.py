"""CorrelationEngine, driven by a fake clock so window expiry is deterministic."""

from __future__ import annotations

import pytest

from backend import correlation as correlation_module
from backend.correlation import CorrelationEngine
from tests.conftest import make_record


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch):
    class Clock:
        now = 1000.0

        def advance(self, seconds: float) -> None:
            self.now += seconds

    fake = Clock()
    monkeypatch.setattr(correlation_module, "monotonic", lambda: fake.now)
    return fake


def _record(*, site_id="site-101", event_type="perimeter_breach", severity="warning", **kw):
    record = make_record(site_id=site_id, type=event_type, **kw)
    triage = record.triage.model_copy(update={"severity": severity})
    return record.with_triage(triage, state="final")


def test_below_threshold_does_not_escalate(clock) -> None:
    engine = CorrelationEngine(threshold=3, window_s=120)
    for _ in range(2):
        result = engine.evaluate(_record())
    assert result.triage.severity == "warning"
    assert result.escalation is None


def test_reaching_the_threshold_escalates_the_triggering_alarm(clock) -> None:
    engine = CorrelationEngine(threshold=3, window_s=120)
    engine.evaluate(_record())
    engine.evaluate(_record())
    result = engine.evaluate(_record())

    assert result.triage.severity == "critical"
    assert result.triage.is_real_threat is True
    assert result.escalation is not None
    assert "site-101" in result.escalation
    assert "perimeter breach" in result.escalation


def test_earlier_alarms_in_the_pattern_are_not_retroactively_rewritten(clock) -> None:
    """Real-time systems announce the new aggregated risk when the pattern
    crosses the threshold; they don't reach back and rewrite history."""
    engine = CorrelationEngine(threshold=3, window_s=120)
    first = engine.evaluate(_record())
    second = engine.evaluate(_record())
    engine.evaluate(_record())  # third crosses the threshold

    assert first.triage.severity == "warning"
    assert second.triage.severity == "warning"


def test_different_sites_do_not_cross_contaminate(clock) -> None:
    engine = CorrelationEngine(threshold=2, window_s=120)
    engine.evaluate(_record(site_id="site-101"))
    result = engine.evaluate(_record(site_id="site-102"))
    assert result.triage.severity == "warning", "one event at a different site is not a pattern"


def test_sightings_outside_the_window_are_pruned(clock) -> None:
    engine = CorrelationEngine(threshold=3, window_s=60)
    engine.evaluate(_record())
    clock.advance(61)  # first sighting ages out
    engine.evaluate(_record())
    result = engine.evaluate(_record())
    assert result.triage.severity == "warning", "only 2 sightings remain inside the window"


def test_info_severity_alarms_do_not_count_toward_the_pattern(clock) -> None:
    """Info is exactly the noise triage exists to filter; counting it toward
    escalation would defeat the point of classifying it as noise."""
    engine = CorrelationEngine(threshold=3, window_s=120)
    engine.evaluate(_record(severity="info"))
    engine.evaluate(_record(severity="info"))
    result = engine.evaluate(_record(severity="info"))
    assert result.triage.severity == "info"
    assert result.escalation is None


def test_already_critical_alarms_are_not_re_escalated(clock) -> None:
    engine = CorrelationEngine(threshold=2, window_s=120)
    engine.evaluate(_record(severity="critical"))
    result = engine.evaluate(_record(severity="critical"))
    assert result.escalation is None, "nothing above critical to escalate to"


def test_a_critical_alarm_still_counts_toward_escalating_a_later_warning(clock) -> None:
    """Combined signals, not just repeated ones: a critical hit followed by
    warnings at the same site is exactly the 'combined signals' case."""
    engine = CorrelationEngine(threshold=2, window_s=120)
    engine.evaluate(_record(severity="critical"))
    result = engine.evaluate(_record(severity="warning"))
    assert result.triage.severity == "critical"
    assert result.escalation is not None


def test_mixed_event_types_at_one_site_are_described_as_combined(clock) -> None:
    engine = CorrelationEngine(threshold=2, window_s=120)
    engine.evaluate(_record(event_type="door_forced"))
    result = engine.evaluate(_record(event_type="glass_break"))
    assert "door forced" in result.escalation
    assert "glass break" in result.escalation


def test_escalation_preserves_the_alarms_triage_state(clock) -> None:
    """A fast-path alarm is already final; escalating it must not mark it
    preliminary again and send it back through the LLM queue."""
    engine = CorrelationEngine(threshold=2, window_s=120)
    engine.evaluate(_record())
    result = engine.evaluate(_record())
    assert result.triage_state == "final"


@pytest.mark.parametrize("kwargs", [{"threshold": 1}, {"window_s": 0}, {"window_s": -5}])
def test_rejects_nonsense_configuration(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        CorrelationEngine(**kwargs)
