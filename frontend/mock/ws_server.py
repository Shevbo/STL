"""Dev mock — simulates M8 Trader API WebSocket for frontend development."""
import asyncio
import json
import math
import random
from datetime import datetime, timezone

from fastapi import FastAPI, WebSocket

app = FastAPI()

ROBOTS = [
    {
        "id": "r1", "name": "GZM6-Trend", "symbol": "GZM6@RTSX",
        "deposit": 500_000, "pnl": 12_400, "tradeCount": 47, "position": 2,
    },
    {
        "id": "r2", "name": "GZM6-Mean", "symbol": "GZM6@RTSX",
        "deposit": 300_000, "pnl": -3_200, "tradeCount": 23, "position": 0,
    },
]

ACCOUNT = {
    "type": "account",
    "deposit": 800_000,
    "free": 412_000,
    "in_position": 320_000,
    "variation_margin": 4_200,
}

SERVICES = ["auth", "md", "tx", "oms", "pos", "audit"]


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    tick = 0
    try:
        await ws.send_text(json.dumps({"type": "robot_update", "robots": ROBOTS}))
        await ws.send_text(json.dumps(ACCOUNT))
        for svc in SERVICES:
            await ws.send_text(json.dumps({"type": "service_status", "service": svc, "status": "ok"}))

        while True:
            tick += 1
            mid = 23_400 + 50 * math.sin(tick / 30) + random.gauss(0, 5)
            spread = random.uniform(0.5, 2.0)
            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            await ws.send_text(json.dumps({
                "type": "quote",
                "symbol": "GZM6@RTSX",
                "bid": round(mid - spread / 2, 1),
                "bid_size": random.randint(1, 20),
                "ask": round(mid + spread / 2, 1),
                "ask_size": random.randint(1, 20),
                "last": round(mid, 1),
                "last_size": random.randint(1, 10),
                "timestamp": now,
            }))
            await asyncio.sleep(0.1)
    except Exception:
        pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
