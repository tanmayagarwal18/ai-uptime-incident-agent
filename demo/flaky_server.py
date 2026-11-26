"""Demo target server with switchable failure modes, so incidents can be triggered on cue during a demo.

Run:  python -m uvicorn demo.flaky_server:app --host 127.0.0.1 --port 8001

Failure modes apply to the Orders API (/api/orders) only; the homepage and /api/health keep working,
which gives the agent realistic evidence ("infrastructure healthy, application endpoint failing").
"""

from __future__ import annotations

import asyncio
import random
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

MODES = {
    "healthy": "Everything works",
    "error_500": "Orders API returns HTTP 500 (database connection pool exhausted)",
    "error_503": "Orders API returns HTTP 503 Service Unavailable with Retry-After",
    "slow": "Orders API responds after ~3 s (above the 1.5 s latency threshold)",
    "timeout": "Orders API hangs for 15 s (monitor times out after 5 s)",
    "intermittent": "Orders API fails ~50% of requests with HTTP 500",
    "not_found": "Orders API route missing - HTTP 404 (bad deploy)",
}

app = FastAPI(title="Demo Shop (flaky target)", version="1.0")
_state = {"mode": "healthy", "changed_at": datetime.now(UTC).isoformat(timespec="seconds")}

ORDERS = [
    {"id": 1001, "item": "Mechanical keyboard", "qty": 1, "status": "shipped"},
    {"id": 1002, "item": "USB-C hub", "qty": 2, "status": "processing"},
    {"id": 1003, "item": "27\" monitor", "qty": 1, "status": "delivered"},
]


class ModeIn(BaseModel):
    mode: str


@app.get("/", response_class=HTMLResponse)
def homepage() -> str:
    return "<html><body><h1>Demo Shop</h1><p>Welcome! Orders API at /api/orders</p></body></html>"


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "service": "demo-shop"}


def _db_error() -> JSONResponse:
    return JSONResponse(status_code=500, content={
        "error": "Internal Server Error",
        "detail": "OrdersService: database connection pool exhausted (max_connections=20, waiting=57)",
    })


@app.get("/api/orders")
async def orders():
    mode = _state["mode"]
    if mode == "error_500":
        return _db_error()
    if mode == "error_503":
        return JSONResponse(status_code=503, content={"error": "Service Unavailable", "detail": "upstream overloaded"},
                            headers={"Retry-After": "120", "Server": "nginx/1.25.3"})
    if mode == "slow":
        await asyncio.sleep(3.0)
    elif mode == "timeout":
        await asyncio.sleep(15.0)
    elif mode == "intermittent" and random.random() < 0.5:
        return _db_error()
    elif mode == "not_found":
        raise HTTPException(status_code=404, detail="Not Found")
    return {"orders": ORDERS, "count": len(ORDERS)}


@app.get("/admin/mode")
def get_mode() -> dict:
    return {**_state, "available_modes": MODES}


@app.post("/admin/mode")
def set_mode(body: ModeIn) -> dict:
    if body.mode not in MODES:
        raise HTTPException(status_code=400, detail=f"Unknown mode. Choose one of: {', '.join(MODES)}")
    _state.update(mode=body.mode, changed_at=datetime.now(UTC).isoformat(timespec="seconds"))
    return {**_state, "description": MODES[body.mode]}
