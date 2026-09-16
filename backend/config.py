"""Configuration, validated once at startup.

Fails fast with a readable message rather than surfacing a missing setting as a
mystery error twenty minutes into a demo.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="SENTINEL_",
        extra="ignore",
        populate_by_name=True,
    )

    # ---- pipeline ----
    event_stream_url: str = "ws://localhost:8765"
    queue_maxsize: int = Field(default=512, gt=0)
    queue_backpressure_timeout_s: float = Field(default=2.0, gt=0)
    triage_workers: int = Field(default=4, gt=0)
    store_capacity: int = Field(default=1000, gt=0)

    # ---- reconnect ----
    reconnect_initial_s: float = Field(default=0.5, gt=0)
    reconnect_max_s: float = Field(default=15.0, gt=0)

    # ---- llm ----
    llm_provider: Literal["stub", "openai", "deepseek"] = "stub"
    # Not SENTINEL_-prefixed: these are the conventional names tools expect.
    openai_api_key: str = Field(default="", validation_alias="OPENAI_API_KEY")
    deepseek_api_key: str = Field(default="", validation_alias="DEEPSEEK_API_KEY")
    # Empty means "use the provider preset's default".
    llm_base_url: str = ""
    llm_api_key: str = ""
    # Per-1M-token rates. Unset means the model's published rate is unknown and
    # the cost strip reports tokens only, rather than a fabricated figure.
    llm_price_in_per_m: float | None = None
    llm_price_out_per_m: float | None = None
    # Reasoning models spend output budget thinking; see triage/openai_compatible.
    llm_max_output_tokens: int = Field(default=700, gt=0)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_circuit_threshold: int = Field(default=5, gt=0)
    llm_circuit_cooldown_s: float = Field(default=30.0, gt=0)
    llm_model: str = ""
    llm_timeout_s: float = Field(default=6.0, gt=0)
    # Emulated think-time for the stub provider. Lets us load-test the queue
    # against realistic LLM latency without spending anything on tokens.
    stub_latency_ms: float = Field(default=0.0, ge=0)

    # ---- api ----
    host: str = "127.0.0.1"
    port: int = Field(default=8000, gt=0, le=65535)
    metrics_interval_s: float = Field(default=1.0, gt=0)

    @field_validator("event_stream_url")
    @classmethod
    def _must_be_websocket(cls, value: str) -> str:
        if not value.startswith(("ws://", "wss://")):
            raise ValueError("event_stream_url must start with ws:// or wss://")
        return value


def load_settings() -> Settings:
    return Settings()
