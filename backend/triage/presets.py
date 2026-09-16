"""Per-provider defaults for the OpenAI-compatible adapter.

Each entry captures where a provider deviates from OpenAI's shape. Adding a
provider means adding a row here, not touching the adapter.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderPreset:
    name: str
    base_url: str
    model: str
    key_env: str
    structured_output: str
    # None leaves the parameter off the request entirely.
    reasoning_effort: str | None = None
    max_output_tokens: int = 700
    notes: str = ""


PRESETS: dict[str, ProviderPreset] = {
    "openai": ProviderPreset(
        name="openai",
        base_url="https://api.openai.com/v1",
        model="gpt-4o-mini",
        key_env="OPENAI_API_KEY",
        structured_output="json_schema",
        max_output_tokens=300,
    ),
    "deepseek": ProviderPreset(
        name="deepseek",
        base_url="https://api.deepseek.com/v1",
        model="deepseek-flash",
        key_env="DEEPSEEK_API_KEY",
        # Rejects json_schema outright: "This response_format type is
        # unavailable now". json_object gives valid JSON; our validator gives
        # the shape.
        structured_output="json_object",
        # deepseek-flash is a reasoning model. Measured on one triage event:
        # default 3125ms / 471 output tokens (333 of them reasoning) versus
        # 1084ms / 126 tokens with reasoning off, for the same verdict.
        reasoning_effort="none",
        max_output_tokens=700,
        notes="reasoning disabled; classification under latency pressure",
    ),
}
