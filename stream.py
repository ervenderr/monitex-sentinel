"""Reference event generator, supplied verbatim with the assessment brief.

Run with:  python stream.py
Emits a bursty event stream on ws://localhost:8765
"""

import asyncio, json, random, uuid, datetime, websockets

TYPES = ["motion_detected","perimeter_breach","door_forced","glass_break",
         "smoke_detected","fire_alarm","object_detected","loitering",
         "camera_offline","panic_button"]
CAMERA = {"motion_detected","object_detected","loitering","glass_break"}

def make_event():
    t = random.choice(TYPES)
    return {
        "event_id": "evt_" + uuid.uuid4().hex[:10],
        "site_id": f"site-{random.randint(100, 106)}",
        "zone": random.choice(["north-perimeter","lobby","loading-dock",
                               "roof","server-room"]),
        "type": t,
        "source": "camera" if t in CAMERA else "sensor",
        "confidence": round(random.uniform(0.35, 0.99), 2),
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "snapshot_url": None,
        "metadata": {"object": random.choice(["person","vehicle","animal"])}
                    if t == "object_detected" else {},
    }

async def handler(ws):
    while True:
        await ws.send(json.dumps(make_event()))
        await asyncio.sleep(random.uniform(0.15, 2.0))   # bursty

async def main():
    async with websockets.serve(handler, "localhost", 8765):
        print("Event stream live on ws://localhost:8765")
        await asyncio.Future()

asyncio.run(main())
