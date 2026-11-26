"""Tools that let the agent read monitoring history from SQLite."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from app import db
from app.tools.http_tools import safe_tool


@safe_tool
def get_recent_checks_impl(site_name: str, limit: int = 10) -> dict[str, Any]:
    rows = db.recent_checks(site_name, limit=max(1, min(int(limit), 50)))
    compact = [
        {k: r[k] for k in ("timestamp", "status", "status_code", "response_time_ms", "error_type")}
        for r in rows
    ]
    ups = sum(1 for r in rows if r["status"] == "UP")
    last_up = next((r["timestamp"] for r in rows if r["status"] == "UP"), None)
    return {"ok": True, "site": site_name, "count": len(rows), "up_ratio": round(ups / len(rows), 2) if rows else None,
            "last_successful_check": last_up, "checks_newest_first": compact}


@safe_tool
def get_past_incidents_impl(site_name: str, limit: int = 5) -> dict[str, Any]:
    rows = db.list_incidents(site=site_name, limit=max(1, min(int(limit), 20)) + 1)
    past = [
        {k: r[k] for k in ("id", "status", "severity", "opened_at", "resolved_at", "summary")}
        for r in rows if r["status"] in ("RESOLVED", "REJECTED")
    ][:limit]
    return {"ok": True, "site": site_name, "past_incident_count": len(past), "incidents": past,
            "recurring": len(past) >= 2}


@tool
def get_recent_checks(site_name: str, limit: int = 10) -> dict:
    """Return the most recent monitoring checks for a monitored site (by site name), newest first, with the
    up-ratio and the time of the last successful check. Use to see when the problem started."""
    return get_recent_checks_impl(site_name, limit)


@tool
def get_past_incidents(site_name: str, limit: int = 5) -> dict:
    """Return previous closed incidents for a monitored site (by site name) to spot recurring problems."""
    return get_past_incidents_impl(site_name, limit)
