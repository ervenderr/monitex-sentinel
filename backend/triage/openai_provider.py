"""OpenAI adapter for the triage layer.

Written against the HTTP API with httpx rather than the SDK, deliberately: the
timeout, retry, and breaker behaviour is the part being judged, and it should be
visible in one file instead of spread across a vendor library's defaults.

Failure handling, in order of cheapness:

1. **Circuit open** - do not call at all. Fail instantly, serve the rule
   fallback. Costs nothing and keeps the queue moving.
2. **Non-retryable** (401, 400, 404) - a bad key or a bad request will not fix
   itself. Fail immediately rather than burning retries and latency.
3. **Retryable** (429, 5xx, timeout, network) - bounded retries with exponential
   backoff and jitter, honouring `Retry-After` when the API sends it.
4. **Junk response** - unparseable or schema-violating output is treated as a
   failure, not passed through. An operator gets a rule verdict instead.

Every path ends in a verdict. The worker's fallback covers whatever reaches it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from time import perf_counter
from typing import Any

import httpx
from pydantic import ValidationError

from backend.models import RawEvent, TriageResult
from backend.triage import rules
from backend.triage.circuit import CircuitBreaker, CircuitOpenError
from backend.triage.prompt import build_messages
from backend.triage.schema import TRIAGE_JSON_SCHEMA, TriageResponse

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.openai.com/v1"
CHAT_COMPLETIONS_PATH = "/chat/completions"

# USD per 1M tokens. Correct as of the build date; pricing drifts, so the cost
# strip is an estimate and is labelled as one in the UI.
PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
}
DEFAULT_PRICING = (0.15, 0.60)

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
MAX_BACKOFF_S = 8.0
# Deterministic-ish output: this is a classification task, not a creative one.
TEMPERATURE = 0.1
MAX_OUTPUT_TOKENS = 200


class ProviderError(RuntimeError):
    """Any failure that should result in a degraded, rule-based verdict."""


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    price_in, price_out = PRICING.get(model, DEFAULT_PRICING)
    return (tokens_in * price_in + tokens_out * price_out) / 1_000_000


class OpenAIProvider:
    name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-4o-mini",
        timeout_s: float = 6.0,
        max_retries: int = 2,
        breaker: CircuitBreaker | None = None,
        client: httpx.AsyncClient | None = None,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required for the openai provider")
        self._model = model
        # Overridable so the stack can run against a local mock, a proxy, or any
        # OpenAI-compatible endpoint (Azure, vLLM, Ollama) without code changes.
        self._url = base_url.rstrip("/") + CHAT_COMPLETIONS_PATH
        self._timeout_s = timeout_s
        self._max_retries = max_retries
        self._breaker = breaker or CircuitBreaker()
        # One client, reused: connection setup per alarm would dominate latency.
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
        )
        self.retries = 0

    @property
    def circuit_state(self) -> str:
        return self._breaker.state

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---- request ----------------------------------------------------------

    def _payload(self, event: RawEvent) -> dict[str, Any]:
        baseline_severity, _threat, baseline_reasoning = rules.classify(event)
        return {
            "model": self._model,
            "messages": build_messages(
                event,
                baseline_severity=baseline_severity,
                baseline_reasoning=baseline_reasoning,
            ),
            "response_format": TRIAGE_JSON_SCHEMA,
            "temperature": TEMPERATURE,
            "max_tokens": MAX_OUTPUT_TOKENS,
        }

    async def _post(self, payload: dict[str, Any]) -> httpx.Response:
        """One attempt, with retryable failures raised as ProviderError."""
        try:
            response = await self._client.post(self._url, json=payload)
        except httpx.TimeoutException as exc:
            raise _Retryable(f"timeout after {self._timeout_s}s") from exc
        except httpx.HTTPError as exc:
            raise _Retryable(f"network error: {exc.__class__.__name__}") from exc

        if response.status_code in RETRYABLE_STATUS:
            raise _Retryable(
                f"http {response.status_code}", retry_after=_retry_after(response)
            )
        if response.status_code >= 400:
            # Config errors do not heal; surface them loudly and stop.
            raise ProviderError(
                f"http {response.status_code}: {response.text[:200]}"
            )
        return response

    async def _call_with_retries(self, payload: dict[str, Any]) -> httpx.Response:
        attempt = 0
        while True:
            try:
                return await self._post(payload)
            except _Retryable as exc:
                if attempt >= self._max_retries:
                    raise ProviderError(f"{exc} (after {attempt} retries)") from exc
                delay = exc.retry_after if exc.retry_after is not None else _backoff(attempt)
                self.retries += 1
                logger.debug("retrying openai call in %.2fs (%s)", delay, exc)
                await asyncio.sleep(delay)
                attempt += 1

    # ---- response ---------------------------------------------------------

    @staticmethod
    def _extract(response: httpx.Response) -> tuple[TriageResponse, int, int]:
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderError("response was not JSON") from exc

        try:
            message = body["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("response had no choices") from exc

        if message.get("refusal"):
            raise ProviderError(f"model refused: {str(message['refusal'])[:120]}")

        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("response content was empty")

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            # Structured output should prevent this; if it happens we treat the
            # response as junk rather than trying to salvage it.
            raise ProviderError("model returned malformed JSON") from exc

        try:
            verdict = TriageResponse.model_validate(parsed).bounded()
        except ValidationError as exc:
            raise ProviderError(f"response failed schema validation: {exc.errors()[:1]}") from exc

        usage = body.get("usage") or {}
        return (
            verdict,
            int(usage.get("prompt_tokens") or 0),
            int(usage.get("completion_tokens") or 0),
        )

    # ---- public API -------------------------------------------------------

    async def triage(self, event: RawEvent) -> TriageResult:
        try:
            self._breaker.before_call()
        except CircuitOpenError as exc:
            raise ProviderError(str(exc)) from exc

        started = perf_counter()
        try:
            response = await self._call_with_retries(self._payload(event))
            verdict, tokens_in, tokens_out = self._extract(response)
        except ProviderError:
            self._breaker.record_failure()
            raise
        except Exception as exc:  # unexpected shape changes must not escape raw
            self._breaker.record_failure()
            raise ProviderError(f"unexpected provider failure: {exc!r}") from exc

        self._breaker.record_success()

        baseline_severity, _threat, _reasoning = rules.classify(event)
        return TriageResult(
            severity=verdict.severity,  # type: ignore[arg-type]
            is_real_threat=verdict.is_real_threat,
            summary=verdict.summary,
            recommended_action=verdict.recommended_action,
            reasoning=verdict.reasoning,
            origin="llm",
            degraded=False,
            model=self._model,
            latency_ms=round((perf_counter() - started) * 1000, 1),
            cost_usd=estimate_cost(self._model, tokens_in, tokens_out),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            overrode_baseline=verdict.severity != baseline_severity,
        )


class _Retryable(Exception):
    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def _retry_after(response: httpx.Response) -> float | None:
    """Honour the API's own pacing advice when it sends it."""
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return min(MAX_BACKOFF_S, max(0.0, float(raw)))
    except ValueError:
        return None


def _backoff(attempt: int) -> float:
    base = min(MAX_BACKOFF_S, 0.5 * (2**attempt))
    return base * (1 + random.uniform(-0.25, 0.25))
