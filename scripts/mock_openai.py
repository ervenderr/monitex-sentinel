"""A local stand-in for the OpenAI chat completions endpoint.

Lets the whole stack - real provider, real retries, real breaker - be exercised
without a key, and lets failure modes be triggered on demand for a demo:

    python scripts/mock_openai.py --port 8900 --latency-ms 400 --fail-rate 0.1
    SENTINEL_LLM_PROVIDER=openai SENTINEL_LLM_BASE_URL=http://localhost:8900/v1 \
      OPENAI_API_KEY=sk-local python -m backend.main
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

SEVERITIES = ["info", "warning", "critical"]

ACTIONS = {
    "critical": "Dispatch a guard and notify the site contact now.",
    "warning": "Pull up the zone camera and verify before escalating.",
    "info": "Log it; no action required.",
}


def build_app(args: argparse.Namespace) -> FastAPI:
    app = FastAPI()
    state = {"calls": 0}

    @app.post("/v1/chat/completions")
    async def completions(request: Request) -> JSONResponse:
        state["calls"] += 1
        body = await request.json()

        if args.latency_ms:
            jitter = random.uniform(0.5, 1.5)
            await asyncio.sleep(args.latency_ms / 1000 * jitter)

        roll = random.random()
        if roll < args.fail_rate:
            return JSONResponse({"error": {"message": "upstream exploded"}}, status_code=500)
        if roll < args.fail_rate + args.rate_limit_rate:
            return JSONResponse(
                {"error": {"message": "rate limited"}},
                status_code=429,
                headers={"retry-after": "0.2"},
            )
        if roll < args.fail_rate + args.rate_limit_rate + args.junk_rate:
            # Malformed content with a 200 status - the nastiest failure mode.
            return JSONResponse(_wrap("not json at all {{{"))

        # Echo back a verdict anchored on the rule baseline in the prompt.
        prompt = body["messages"][-1]["content"]
        match = re.search(r"Rule baseline: (\w+)", prompt)
        severity = match.group(1) if match else "warning"
        if severity not in SEVERITIES:
            severity = "warning"
        # Disagree occasionally so override tracking has something to show.
        if random.random() < args.override_rate:
            severity = random.choice([s for s in SEVERITIES if s != severity])

        event_type = _field(prompt, "type") or "event"
        zone = _field(prompt, "zone") or "unknown zone"
        site = _field(prompt, "site") or "unknown site"

        verdict = {
            "severity": severity,
            "is_real_threat": severity != "info",
            "summary": f"{event_type.replace('_', ' ').capitalize()} in {zone} at {site}.",
            "recommended_action": ACTIONS[severity],
            "reasoning": "Mock provider echoing the rule baseline.",
        }
        return JSONResponse(_wrap(json.dumps(verdict)))

    @app.get("/stats")
    async def stats() -> dict:
        return state

    return app


def _field(prompt: str, name: str) -> str | None:
    match = re.search(rf"^{name}: (.+)$", prompt, re.MULTILINE)
    return match.group(1).strip() if match else None


def _wrap(content: str) -> dict:
    return {
        "id": "chatcmpl-mock",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
        "usage": {
            "prompt_tokens": random.randint(330, 380),
            "completion_tokens": random.randint(60, 95),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8900)
    parser.add_argument("--latency-ms", type=float, default=400)
    parser.add_argument("--fail-rate", type=float, default=0.0)
    parser.add_argument("--rate-limit-rate", type=float, default=0.0)
    parser.add_argument("--junk-rate", type=float, default=0.0)
    parser.add_argument("--override-rate", type=float, default=0.15)
    args = parser.parse_args()

    import uvicorn

    print(f"mock openai live on http://localhost:{args.port}/v1")
    uvicorn.run(build_app(args), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
