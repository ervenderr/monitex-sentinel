"""The contract we hold the model to, and the validation behind it.

`strict` structured output makes the model's JSON well-formed, but well-formed
is not the same as sane. Everything here still gets validated and bounded on
arrival, because "the API guarantees it" is not a thing to rely on when the
alternative is an operator staring at a blank alarm.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

MAX_SUMMARY_CHARS = 200
MAX_ACTION_CHARS = 200
MAX_REASONING_CHARS = 400

VALID_SEVERITIES = {"info", "warning", "critical"}

# Sent to the API as response_format. `strict` requires every property to be
# listed in `required` and additionalProperties to be false.
TRIAGE_JSON_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "alarm_triage",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "severity": {
                    "type": "string",
                    "enum": ["info", "warning", "critical"],
                    "description": "info = log only, warning = operator should look, "
                    "critical = act now",
                },
                "is_real_threat": {
                    "type": "boolean",
                    "description": "false if this is most likely a false positive",
                },
                "summary": {
                    "type": "string",
                    "description": "One line an operator can read at a glance. "
                    "No preamble, under 140 characters.",
                },
                "recommended_action": {
                    "type": "string",
                    "description": "The single next action the operator should take.",
                },
                "reasoning": {
                    "type": "string",
                    "description": "Brief justification, under 200 characters.",
                },
            },
            "required": [
                "severity",
                "is_real_threat",
                "summary",
                "recommended_action",
                "reasoning",
            ],
            "additionalProperties": False,
        },
    },
}


class TriageResponse(BaseModel):
    """Validated shape of what the model returned."""

    model_config = ConfigDict(frozen=True)

    severity: str
    is_real_threat: bool
    summary: str
    recommended_action: str
    reasoning: str = ""

    @field_validator("severity", mode="before")
    @classmethod
    def _normalise_severity(cls, value: Any) -> str:
        """Accept casing and whitespace drift; reject anything genuinely unknown."""
        if not isinstance(value, str):
            raise ValueError("severity must be a string")
        cleaned = value.strip().lower()
        if cleaned not in VALID_SEVERITIES:
            raise ValueError(f"unknown severity: {value!r}")
        return cleaned

    @field_validator("is_real_threat", mode="before")
    @classmethod
    def _coerce_threat_flag(cls, value: Any) -> Any:
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "yes"}:
                return True
            if lowered in {"false", "no"}:
                return False
        return value

    @field_validator("summary", "recommended_action")
    @classmethod
    def _require_content(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("field must not be empty")
        return cleaned

    def bounded(self) -> TriageResponse:
        """Truncate long fields so one chatty response cannot break the UI."""
        return self.model_copy(
            update={
                "summary": _truncate(self.summary, MAX_SUMMARY_CHARS),
                "recommended_action": _truncate(self.recommended_action, MAX_ACTION_CHARS),
                "reasoning": _truncate(" ".join(self.reasoning.split()), MAX_REASONING_CHARS),
            }
        )


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"
