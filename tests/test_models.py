"""The feed is untrusted. These are the shapes that must never crash us."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from backend.models import RawEvent, normalise_payload
from tests.conftest import make_event


def test_empty_payload_becomes_a_valid_event() -> None:
    event = RawEvent.model_validate({})
    assert event.event_id.startswith("evt_")
    assert event.type == "unknown"
    assert event.confidence is None


@pytest.mark.parametrize("junk", ["abc", None, [], {}, float("nan")])
def test_junk_confidence_degrades_to_none(junk: object) -> None:
    assert RawEvent.model_validate({"confidence": junk}).confidence is None


@pytest.mark.parametrize(
    ("value", "expected"), [(1.7, 1.0), (-3.0, 0.0), ("0.42", 0.42)]
)
def test_confidence_is_clamped_and_coerced(value: object, expected: float) -> None:
    assert RawEvent.model_validate({"confidence": value}).confidence == expected


def test_unparseable_timestamp_falls_back_to_arrival() -> None:
    before = datetime.now(UTC)
    event = RawEvent.model_validate({"timestamp": "yesterday-ish"})
    assert event.timestamp >= before


def test_naive_timestamp_is_treated_as_utc() -> None:
    event = RawEvent.model_validate({"timestamp": "2026-09-16T13:00:00"})
    assert event.timestamp.tzinfo is not None


def test_non_dict_metadata_is_discarded() -> None:
    assert RawEvent.model_validate({"metadata": "not-a-dict"}).metadata == {}


def test_bad_source_is_inferred_from_type() -> None:
    assert RawEvent.model_validate({"type": "loitering", "source": "???"}).source == "camera"
    assert RawEvent.model_validate({"type": "fire_alarm", "source": None}).source == "sensor"


def test_records_are_immutable() -> None:
    event = make_event()
    with pytest.raises(ValidationError):
        event.type = "fire_alarm"  # type: ignore[misc]


def test_normalise_is_pure() -> None:
    payload = {"type": "fire_alarm"}
    normalise_payload(payload)
    assert payload == {"type": "fire_alarm"}
