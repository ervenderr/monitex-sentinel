"""Provider selection lives here and nowhere else.

Swapping the triage brain is a one-line change in this file. Phase 2 registers
the OpenAI adapter alongside the stub.
"""

from __future__ import annotations

import logging

from backend.config import Settings
from backend.triage.base import StubProvider, TriageProvider

logger = logging.getLogger(__name__)


def build_provider(settings: Settings) -> TriageProvider:
    if settings.llm_provider == "stub":
        logger.info(
            "triage provider: stub (deterministic rules, no API calls, %.0fms emulated latency)",
            settings.stub_latency_ms,
        )
        return StubProvider(latency_ms=settings.stub_latency_ms)

    raise ValueError(f"unknown llm_provider: {settings.llm_provider!r}")
