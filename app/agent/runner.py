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
    """Which human action the paused graph is waiting for: 'approval', 'await_recovery' or None."""
    snapshot = get_graph().get_state(_config(incident_id))
    if not snapshot or not snapshot.next:
        return None
    node = snapshot.next[0]
    return {"approval_gate": "approval", "await_recovery": "await_recovery"}.get(node, node)


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


def submit_approval(incident_id: int, approved: bool, reviewer: str = "dashboard") -> str | None:
    """Resume a graph paused at the approval gate."""
    if pending_action(incident_id) != "approval":
        raise ValueError(f"Incident #{incident_id} is not awaiting approval")
    get_graph().invoke(Command(resume={"approved": approved, "reviewer": reviewer}), _config(incident_id))
    return pending_action(incident_id)
