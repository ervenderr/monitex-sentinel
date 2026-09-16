"""Provider selection lives here and nowhere else.

Swapping the triage brain is a one-line change in this file. Phase 2 registers
the OpenAI adapter alongside the stub.
"""

from __future__ import annotations

import logging

from backend.config import Settings
from backend.triage.base import StubProvider, TriageProvider
from backend.triage.circuit import CircuitBreaker
from backend.triage.openai_provider import OpenAIProvider

logger = logging.getLogger(__name__)


def build_provider(settings: Settings) -> TriageProvider:
    if settings.llm_provider == "stub":
        logger.info(
            "triage provider: stub (deterministic rules, no API calls, %.0fms emulated latency)",
            settings.stub_latency_ms,
        )
        return StubProvider(latency_ms=settings.stub_latency_ms)

    if settings.llm_provider == "openai":
        if not settings.openai_api_key:
            # Fail loudly at startup rather than degrading every alarm silently
            # for the length of a demo.
            raise ValueError(
                "SENTINEL_LLM_PROVIDER=openai but OPENAI_API_KEY is not set. "
                "Set the key, or use SENTINEL_LLM_PROVIDER=stub."
            )
        logger.info("triage provider: openai (%s)", settings.llm_model)
        return OpenAIProvider(
            api_key=settings.openai_api_key,
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            timeout_s=settings.llm_timeout_s,
            max_retries=settings.llm_max_retries,
            breaker=CircuitBreaker(
                failure_threshold=settings.llm_circuit_threshold,
                cooldown_s=settings.llm_circuit_cooldown_s,
            ),
        )

    raise ValueError(f"unknown llm_provider: {settings.llm_provider!r}")
