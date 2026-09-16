"""Burst load generator - the same schema as stream.py, at a chosen rate.

The reference generator emits roughly one event per second, which never
exercises the queue. This one drives the pipeline hard enough to show
backpressure and shedding on the dashboard.

    python scripts/loadgen.py --rate 400 --port 8765
    python scripts/loadgen.py --rate 400 --burst 5 --quiet 2
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import uuid
from datetime import UTC, datetime

from websockets.asyncio.server import serve

TYPES = [
    "motion_detected", "perimeter_breach", "door_forced", "glass_break",
    "smoke_detected", "fire_alarm", "object_detected", "loitering",
    "camera_offline", "sensor_fault", "panic_button",
]
CAMERA = {"motion_detected", "object_detected", "loitering", "glass_break"}
ZONES = ["north-perimeter", "lobby", "loading-dock", "roof", "server-room"]

# Weighted so the feed looks like real life: mostly noise, occasional emergency.
WEIGHTS = [30, 12, 8, 6, 3, 1, 20, 10, 5, 4, 1]


def make_event(*, malformed_rate: float) -> dict | str:
    if random.random() < malformed_rate:
        # Exercise the parser's tolerance for junk on the wire.
        return random.choice(
            ['{"truncated": ', "not json at all", '{"confidence": "high"}', "[]"]
        )

    event_type = random.choices(TYPES, weights=WEIGHTS, k=1)[0]
    event = {
        "event_id": "evt_" + uuid.uuid4().hex[:10],
        "site_id": f"site-{random.randint(100, 106)}",
        "zone": random.choice(ZONES),
        "type": event_type,
        "source": "camera" if event_type in CAMERA else "sensor",
        "confidence": round(random.uniform(0.35, 0.99), 2),
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "snapshot_url": None,
        "metadata": {"object": random.choice(["person", "vehicle", "animal"])}
        if event_type == "object_detected"
        else {},
    }
    # Drop a field now and then; the brief warns that fields go missing.
    if random.random() < 0.05:
        del event[random.choice(["site_id", "zone", "confidence"])]
    return event


async def run(args: argparse.Namespace) -> None:
    async def handler(ws) -> None:
        print(f"dashboard connected; emitting ~{args.rate}/s")
        interval = 1.0 / args.rate
        sent = 0
        try:
            while True:
                if args.burst and sent and sent % (args.rate * args.burst) == 0:
                    print(f"  ...{sent} sent, quiet for {args.quiet}s")
                    await asyncio.sleep(args.quiet)
                payload = make_event(malformed_rate=args.malformed_rate)
                await ws.send(payload if isinstance(payload, str) else json.dumps(payload))
                sent += 1
                await asyncio.sleep(interval)
        except Exception:
            print(f"client gone after {sent} events")

    async with serve(handler, "localhost", args.port):
        print(f"burst generator live on ws://localhost:{args.port} (rate={args.rate}/s)")
        await asyncio.Future()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rate", type=float, default=200, help="events per second")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--burst", type=float, default=0, help="seconds of firing before a pause")
    parser.add_argument("--quiet", type=float, default=2.0, help="pause length between bursts")
    parser.add_argument(
        "--malformed-rate", type=float, default=0.02, help="fraction of junk frames"
    )
    args = parser.parse_args()
    if args.rate <= 0:
        parser.error("--rate must be positive")
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
