"""Adapter for any OpenAI-compatible chat completions API.

Written against the HTTP API with httpx rather than the SDK, deliberately: the
timeout, retry, and breaker behaviour is the part that matters, and it should be
visible in one file instead of spread across a vendor library's defaults. It
also means one adapter covers every OpenAI-shaped provider.

"OpenAI-compatible" is not the same as "identical", and the differences are
load-bearing. Two knobs absorb them:

* `structured_output` - OpenAI enforces a strict JSON schema server-side.
  DeepSeek rejects `json_schema` outright ("This response_format type is
  unavailable now") and offers only `json_object`, which guarantees valid JSON
  but not the right *shape*. In that mode the schema is described in the prompt
  instead, and the response validator - which we needed anyway - is what
  actually holds the contract.
* `reasoning_effort` - `deepseek-flash` is a reasoning model and spends most of
  its output budget thinking before it answers. Left alone it burned 333 of 471
  output tokens on reasoning and took 3.1s; with reasoning disabled the same
  event takes 1.0s and 126 tokens for the same verdict. Alarm triage is
  classification under latency pressure, not a reasoning task, and the rule
  engine already supplies a baseline to react to.

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
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Literal

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

@dataclass(frozen=True)
class ModelPricing:
    """USD per 1M tokens. `cached_input` is the discounted repeat-prompt rate."""

    input_per_m: float
    output_per_m: float
    cached_input_per_m: float | None = None


# Published rates drift, so these are estimates and the UI labels them as such.
# A model that is not listed reports tokens but no cost rather than inventing a
# number; override per-deployment with SENTINEL_LLM_PRICE_IN_PER_M /
# SENTINEL_LLM_PRICE_OUT_PER_M.
#
# DeepSeek bills half these rates off-peak (peak is 01:00-04:00 and 06:00-10:00
# UTC on weekdays). We quote peak, so the cost strip over-estimates rather than
# under-estimates - the safer direction for a number someone might budget from.
PRICING: dict[str, ModelPricing] = {
    "gpt-4o-mini": ModelPricing(0.15, 0.60, cached_input_per_m=0.075),
    "gpt-4o": ModelPricing(2.50, 10.00, cached_input_per_m=1.25),
    "gpt-4.1-mini": ModelPricing(0.40, 1.60, cached_input_per_m=0.10),
    "gpt-4.1-nano": ModelPricing(0.10, 0.40, cached_input_per_m=0.025),
    "deepseek-flash": ModelPricing(0.30, 1.20, cached_input_per_m=0.006),
    "deepseek-v4-pro": ModelPricing(1.32, 3.96, cached_input_per_m=0.044),
}

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
MAX_BACKOFF_S = 8.0
# Deterministic-ish output: this is a classification task, not a creative one.
TEMPERATURE = 0.1
# Generous because reasoning models spend this budget thinking before they
# answer; too low truncates the JSON mid-string and every call fails.
MAX_OUTPUT_TOKENS = 700

StructuredOutputMode = Literal["json_schema", "json_object", "none"]


class ProviderError(RuntimeError):
    """Any failure that should result in a degraded, rule-based verdict."""


def estimate_cost(
    model: str,
    tokens_in: int,
    tokens_out: int,
    *,
    cached_tokens: int = 0,
    price_in_per_m: float | None = None,
    price_out_per_m: float | None = None,
) -> float:
    """Estimated USD for one call. Returns 0.0 when the rate is unknown.

    Reporting zero for an unpriced model is deliberate: a fabricated rate on an
    operator's cost strip is worse than an honest blank.

    `cached_tokens` is the portion of the input the provider served from its
    prompt cache. It matters here far more than it looks: our system prompt is
    identical on every call, so after the first request most of the input bills
    at the cached rate - for deepseek-flash that is $0.006 against $0.30 per 1M,
    a 50x difference on the bulk of each request.
    """
    if price_in_per_m is not None and price_out_per_m is not None:
        pricing = ModelPricing(price_in_per_m, price_out_per_m)
    else:
        known = PRICING.get(model)
        if known is None:
            return 0.0
        pricing = known

    cached = max(0, min(cached_tokens, tokens_in))
    uncached = tokens_in - cached
    cached_rate = (
        pricing.cached_input_per_m
        if pricing.cached_input_per_m is not None
        else pricing.input_per_m
    )
    return (
        uncached * pricing.input_per_m
        + cached * cached_rate
        + tokens_out * pricing.output_per_m
    ) / 1_000_000


class OpenAICompatibleProvider:
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
        name: str = "openai",
        structured_output: StructuredOutputMode = "json_schema",
        reasoning_effort: str | None = None,
        max_output_tokens: int = MAX_OUTPUT_TOKENS,
        price_in_per_m: float | None = None,
        price_out_per_m: float | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("an API key is required for the llm provider")
        self.name = name
        self._model = model
        self._structured_output: StructuredOutputMode = structured_output
        self._reasoning_effort = reasoning_effort
        self._max_output_tokens = max_output_tokens
        self._price_in_per_m = price_in_per_m
        self._price_out_per_m = price_out_per_m
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
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": build_messages(
                event,
                baseline_severity=baseline_severity,
                baseline_reasoning=baseline_reasoning,
                describe_schema=self._structured_output != "json_schema",
            ),
            "temperature": TEMPERATURE,
            "max_tokens": self._max_output_tokens,
        }
        if self._structured_output == "json_schema":
            payload["response_format"] = TRIAGE_JSON_SCHEMA
        elif self._structured_output == "json_object":
            # Valid JSON guaranteed, correct shape not. The response validator
            # is what actually enforces the contract here.
            payload["response_format"] = {"type": "json_object"}
        if self._reasoning_effort is not None:
            payload["reasoning_effort"] = self._reasoning_effort
        return payload

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
    def _extract(response: httpx.Response) -> tuple[TriageResponse, TokenUsage]:
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
        return verdict, _usage_tokens(usage)

    # ---- public API -------------------------------------------------------

    async def triage(self, event: RawEvent) -> TriageResult:
        try:
            self._breaker.before_call()
        except CircuitOpenError as exc:
            raise ProviderError(str(exc)) from exc

        started = perf_counter()
        try:
            response = await self._call_with_retries(self._payload(event))
            verdict, usage = self._extract(response)
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
            cost_usd=estimate_cost(
                self._model,
                usage.prompt_tokens,
                usage.completion_tokens,
                cached_tokens=usage.cached_tokens,
                price_in_per_m=self._price_in_per_m,
                price_out_per_m=self._price_out_per_m,
            ),
            tokens_in=usage.prompt_tokens,
            tokens_out=usage.completion_tokens,
            cached_tokens=usage.cached_tokens,
            overrode_baseline=verdict.severity != baseline_severity,
        )


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0


def _usage_tokens(usage: dict[str, Any]) -> TokenUsage:
    """Read token counts from either provider's usage shape.

    OpenAI nests cache hits under `prompt_tokens_details.cached_tokens`;
    DeepSeek reports `prompt_cache_hit_tokens` at the top level.
    """
    details = usage.get("prompt_tokens_details") or {}
    cached = details.get("cached_tokens")
    if cached is None:
        cached = usage.get("prompt_cache_hit_tokens")
    try:
        return TokenUsage(
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            cached_tokens=int(cached or 0),
        )
    except (TypeError, ValueError):
        return TokenUsage()


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
