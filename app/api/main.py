"""FastAPI backend exposing monitoring data and incident actions.

Run:  python -m uvicorn app.api.main:app --port 8000   (Swagger UI at http://127.0.0.1:8000/docs)
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app import db, services
from app.config import get_settings, load_sites, setup_logging


@asynccontextmanager
async def lifespan(_app: FastAPI):
    setup_logging()
    services.bootstrap()
    yield


app = FastAPI(
    title="AI Uptime Incident Agent API",
    version="1.0.0",
    description="Website/API monitoring with an AI incident-response agent (LangGraph + GPT-4.1 mini).",
    lifespan=lifespan,
)


class ApprovalIn(BaseModel):
    reviewer: str = "api"


class SimulateIn(BaseModel):
    mode: str = "error_500"
    site: str = services.DEMO_SITE


def _incident_or_404(incident_id: int) -> dict[str, Any]:
    inc = db.get_incident(incident_id)
    if inc is None:
        raise HTTPException(404, f"Incident {incident_id} not found")
    return inc


@app.get("/health", tags=["meta"])
def health() -> dict[str, Any]:
    s = get_settings()
    return {"status": "ok", "llm": "mock" if s.use_mock_llm else s.llm_model_id, "llm_provider": s.provider,
            "github": "enabled" if s.github_enabled else "dry-run", "require_human_approval": s.require_human_approval}


@app.get("/sites", tags=["monitoring"])
def sites(hours: float = Query(24, gt=0, le=720)) -> list[dict[str, Any]]:
    """Configured sites with current status, uptime % and average latency."""
    return services.site_stats(hours)


@app.get("/sites/config", tags=["monitoring"])
def sites_config() -> list[dict[str, Any]]:
    return [s.model_dump() for s in load_sites()]


@app.get("/checks", tags=["monitoring"])
def checks(site: str | None = None, hours: float = Query(1, gt=0, le=720), limit: int = Query(200, le=5000)):
    rows = db.checks_since(hours, site=site)
    return rows[-limit:]


@app.post("/checks/run", tags=["monitoring"])
async def run_checks(run_agent: bool = True) -> dict[str, Any]:
    """Run one monitoring cycle now (new incidents are investigated by the agent)."""
    new = await run_in_threadpool(services.run_checks_now, None, run_agent)
    return {"new_incidents": new}


@app.get("/incidents", tags=["incidents"])
def incidents(status: str | None = None, site: str | None = None, limit: int = Query(100, le=500)):
    return db.list_incidents(site=site, status=status, limit=limit)


@app.get("/incidents/{incident_id}", tags=["incidents"])
def incident(incident_id: int) -> dict[str, Any]:
    from app.agent.runner import pending_action

    inc = _incident_or_404(incident_id)
    for k in ("diagnosis_json", "github_payload_json", "trigger_json"):
        inc.pop(k, None)
    inc["pending_action"] = pending_action(incident_id)
    inc["events"] = db.list_events(incident_id)
    inc["tool_runs"] = db.list_tool_runs(incident_id)
    return inc


@app.get("/incidents/{incident_id}/report", tags=["incidents"])
def incident_report(incident_id: int) -> dict[str, Any]:
    return {"incident_id": incident_id, "report_md": _incident_or_404(incident_id)["report_md"]}


@app.post("/incidents/{incident_id}/approve", tags=["incidents"])
async def approve(incident_id: int, body: ApprovalIn | None = None) -> dict[str, Any]:
    """Approve GitHub ticket creation for an incident paused at the approval gate."""
    from app.agent.runner import submit_approval

    _incident_or_404(incident_id)
    try:
        pending = await run_in_threadpool(submit_approval, incident_id, True, (body or ApprovalIn()).reviewer)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    inc = db.get_incident(incident_id)
    return {"status": inc["status"], "github_issue_url": inc["github_issue_url"], "pending_action": pending}


@app.post("/incidents/{incident_id}/reject", tags=["incidents"])
async def reject(incident_id: int, body: ApprovalIn | None = None) -> dict[str, Any]:
    from app.agent.runner import submit_approval

    _incident_or_404(incident_id)
    try:
        await run_in_threadpool(submit_approval, incident_id, False, (body or ApprovalIn()).reviewer)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"status": db.get_incident(incident_id)["status"]}


@app.post("/incidents/{incident_id}/verify", tags=["incidents"])
async def verify(incident_id: int) -> dict[str, Any]:
    """Re-check the site; resolves the incident and closes the GitHub issue when healthy."""
    from app.agent.runner import verify_recovery

    _incident_or_404(incident_id)
    return await run_in_threadpool(verify_recovery, incident_id)


@app.post("/demo/simulate", tags=["demo"])
async def simulate(body: SimulateIn) -> dict[str, Any]:
    """Switch the demo server into a failure mode and run checks until an incident opens."""
    try:
        incident_id = await run_in_threadpool(services.simulate_incident, body.mode, body.site)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Simulation failed: {exc}") from exc
    return {"incident_id": incident_id}


@app.post("/demo/heal", tags=["demo"])
def heal() -> dict[str, Any]:
    return services.set_demo_mode("healthy")
