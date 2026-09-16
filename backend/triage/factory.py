"""Provider selection lives here and nowhere else.

Swapping the triage brain is one setting. The adapter is shared; per-provider
quirks live in `presets.py`.
"""

from __future__ import annotations

import logging

from backend.config import Settings
from backend.triage.base import StubProvider, TriageProvider
from backend.triage.circuit import CircuitBreaker
from backend.triage.openai_compatible import OpenAICompatibleProvider
from backend.triage.presets import PRESETS

logger = logging.getLogger(__name__)


def _resolve_key(settings: Settings, preset_key_env: str) -> str:
    """Explicit setting wins, then the provider's conventional env var."""
    if settings.llm_api_key:
        return settings.llm_api_key
    if preset_key_env == "OPENAI_API_KEY":
        return settings.openai_api_key
    if preset_key_env == "DEEPSEEK_API_KEY":
        return settings.deepseek_api_key
    return ""


def build_provider(settings: Settings) -> TriageProvider:
    if settings.llm_provider == "stub":
        logger.info(
            "triage provider: stub (deterministic rules, no API calls, %.0fms emulated latency)",
            settings.stub_latency_ms,
        )
        return StubProvider(latency_ms=settings.stub_latency_ms)

    preset = PRESETS.get(settings.llm_provider)
    if preset is None:
        raise ValueError(f"unknown llm_provider: {settings.llm_provider!r}")

    api_key = _resolve_key(settings, preset.key_env)
    if not api_key:
        # Fail loudly at startup rather than degrading every alarm silently for
        # the length of a demo.
        raise ValueError(
            f"SENTINEL_LLM_PROVIDER={preset.name} but {preset.key_env} is not set. "
            f"Set the key, or use SENTINEL_LLM_PROVIDER=stub."
        )

    model = settings.llm_model or preset.model
    base_url = settings.llm_base_url or preset.base_url
    logger.info(
        "triage provider: %s (%s, structured_output=%s%s)",
        preset.name,
        model,
        preset.structured_output,
        f", reasoning_effort={preset.reasoning_effort}" if preset.reasoning_effort else "",
    )
    return OpenAICompatibleProvider(
        api_key=api_key,
        name=preset.name,
        model=model,
        base_url=base_url,
        structured_output=preset.structured_output,  # type: ignore[arg-type]
        reasoning_effort=preset.reasoning_effort,
        max_output_tokens=settings.llm_max_output_tokens or preset.max_output_tokens,
        timeout_s=settings.llm_timeout_s,
        max_retries=settings.llm_max_retries,
        price_in_per_m=settings.llm_price_in_per_m,
        price_out_per_m=settings.llm_price_out_per_m,
        breaker=CircuitBreaker(
            failure_threshold=settings.llm_circuit_threshold,
            cooldown_s=settings.llm_circuit_cooldown_s,
        ),
    )
