"""Run and resume the incident-response graph. One LangGraph thread per incident, persisted in SQLite,
so a paused approval survives restarts and can be resumed from the dashboard, API or CLI."""

from __future__ import annotations

import logging
import sqlite3
import threading
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from app import db
from app.agent.graph import build_graph
from app.config import get_settings

log = logging.getLogger(__name__)

_lock = threading.Lock()
_graph_cache: dict[str, Any] = {}


def get_graph() -> Any:
    """Compiled graph with a SQLite checkpointer (cached per checkpoint path)."""
    path = str(get_settings().resolve(get_settings().checkpoint_db_path))
    with _lock:
        if path not in _graph_cache:
            get_settings().resolve(path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            saver = SqliteSaver(conn)
            saver.setup()
            _graph_cache[path] = build_graph(checkpointer=saver)
        return _graph_cache[path]


def _config(incident_id: int) -> dict[str, Any]:
    return {"configurable": {"thread_id": f"incident-{incident_id}"}, "recursion_limit": 60}


def pending_action(incident_id: int) -> str | None:
    """Which human action the paused graph is waiting for: 'approval', 'await_recovery' or None.

    Only a graph that is actually paused at a human step counts. A run that stopped part-way
    (app closed or crashed mid-investigation) also has a 'next' node saved, but it is not waiting
    for anyone - treating it as pending would hide the restart buttons and leave it stuck."""
    snapshot = get_graph().get_state(_config(incident_id))
    if not snapshot or not snapshot.next:
        return None
    action = {"approval_gate": "approval", "await_recovery": "await_recovery"}.get(snapshot.next[0])
    if action is None:
        return None
    interrupted = any(getattr(t, "interrupts", None) for t in (snapshot.tasks or ()))
    return action if interrupted else None


def start_investigation(incident_id: int) -> str | None:
    """Run the agent for a newly opened incident until it finishes or pauses. Returns the pending action."""
    log.info("Agent starting investigation of incident #%s", incident_id)
    try:
        get_graph().invoke({"incident_id": incident_id}, _config(incident_id))
    except Exception as exc:
        log.exception("Agent failed on incident #%s", incident_id)
        db.add_event(incident_id, "AGENT_ERROR", f"Agent error: {type(exc).__name__}: {exc}")
        db.update_incident(incident_id, status="OPEN")
        raise
    return pending_action(incident_id)


def seconds_since_activity(incident_id: int) -> float | None:
    from datetime import UTC, datetime

    ts = db.last_activity(incident_id)
    if not ts:
        return None
    return (datetime.now(UTC) - datetime.fromisoformat(ts)).total_seconds()


def is_stalled(incident_id: int) -> bool:
    """True when an investigation made no progress for a while and isn't waiting for a person."""
    inc = db.get_incident(incident_id)
    if not inc or inc["status"] not in ("OPEN", "INVESTIGATING") or pending_action(incident_id):
        return False
    idle = seconds_since_activity(incident_id)
    return idle is None or idle > get_settings().investigation_stalled_seconds


def restart_investigation(incident_id: int, offline: bool = False) -> str | None:
    """Start the investigation again from scratch (optionally with the offline AI only)."""
    if db.get_incident(incident_id) is None:
        raise ValueError(f"Incident #{incident_id} not found")
    graph = get_graph()
    graph.checkpointer.delete_thread(f"incident-{incident_id}")
    db.delete_tool_runs(incident_id)
    db.update_incident(incident_id, status="OPEN", severity=None, diagnosis_json=None, report_md=None)
    db.add_event(incident_id, "INVESTIGATION_RESTARTED",
                 "Investigation restarted" + (" with the offline demo AI" if offline else ""))
    log.info("Restarting investigation of incident #%s (offline=%s)", incident_id, offline)
    try:
        graph.invoke({"incident_id": incident_id, "llm_fallback": offline}, _config(incident_id))
    except Exception as exc:
        log.exception("Restarted agent failed on incident #%s", incident_id)
        db.add_event(incident_id, "AGENT_ERROR", f"Agent error: {type(exc).__name__}: {exc}")
        db.update_incident(incident_id, status="OPEN")
        raise
    return pending_action(incident_id)


def submit_approval(incident_id: int, approved: bool, reviewer: str = "dashboard") -> str | None:
    """Resume a graph paused at the approval gate."""
    if pending_action(incident_id) != "approval":
        raise ValueError(f"Incident #{incident_id} is not awaiting approval")
    get_graph().invoke(Command(resume={"approved": approved, "reviewer": reviewer}), _config(incident_id))
    return pending_action(incident_id)


def verify_recovery(incident_id: int) -> dict[str, Any]:
    """Run the recovery check for an incident. Resumes the graph if it is waiting for verification;
    otherwise (e.g. still awaiting approval) performs a plain health check and reports the result."""
    if pending_action(incident_id) == "await_recovery":
        get_graph().invoke(Command(resume={"action": "verify"}), _config(incident_id))
        inc = db.get_incident(incident_id)
        events = db.list_events(incident_id)
        return {"status": inc["status"], "recovered": inc["status"] == "RESOLVED",
                "message": events[-1]["message"] if events else ""}

    from app.config import SiteConfig
    from app.monitor.checker import check_site

    inc = db.get_incident(incident_id)
    if inc is None:
        raise ValueError(f"Incident #{incident_id} not found")
    site = SiteConfig(**((inc.get("trigger") or {}).get("site") or {"name": inc["site"], "url": ""}))
    result = check_site(site)
    db.insert_check(result.model_dump(mode="json"))
    return {"status": inc["status"], "recovered": result.status == "UP",
            "message": f"Health check: {result.status} ({result.status_code or result.error_type}). "
                       "Incident is not awaiting verification, status unchanged."}


def auto_verify_open_incidents() -> list[int]:
    """Called by the monitor after each cycle: if a ticketed incident's site is UP again, verify & resolve it."""
    resolved = []
    for inc in db.list_incidents(status="TICKET_CREATED"):
        latest = db.recent_checks(inc["site"], limit=1)
        if latest and latest[0]["status"] == "UP" and pending_action(inc["id"]) == "await_recovery":
            log.info("Site %s is UP again - auto-verifying incident #%s", inc["site"], inc["id"])
            if verify_recovery(inc["id"])["recovered"]:
                resolved.append(inc["id"])
    return resolved
