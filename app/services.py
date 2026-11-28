"""High-level actions shared by the dashboard, the REST API and the CLI scripts."""

from __future__ import annotations

import logging
import statistics
import time
from typing import Any

import requests

from app import db
from app.config import get_settings, get_site, load_sites

log = logging.getLogger(__name__)

DEMO_SITE = "demo-orders-api"


def bootstrap() -> None:
    """Create tables and sync the configured sites (idempotent)."""
    db.init_db()
    db.upsert_sites(load_sites())


def handle_new_incident(incident_id: int) -> str | None:
    """Default incident handler: run the AI agent until it finishes or pauses."""
    from app.agent.runner import start_investigation

    try:
        return start_investigation(incident_id)
    except Exception:  # noqa: BLE001 - recorded as an incident event by the runner
        return None


def run_checks_now(site_names: list[str] | None = None, run_agent: bool = True) -> list[int]:
    """Run one monitoring cycle now. Returns ids of newly opened incidents."""
    from app.monitor.scheduler import run_cycle

    sites = load_sites()
    if site_names:
        sites = [s for s in sites if s.name in site_names]
    new = run_cycle(sites, on_incident=handle_new_incident if run_agent else None)
    if get_settings().auto_verify_recovery:
        from app.agent.runner import auto_verify_open_incidents

        auto_verify_open_incidents()
    return new


# ------------------------------------------------------------------ demo server control
def demo_mode() -> dict[str, Any] | None:
    try:
        return requests.get(f"{get_settings().demo_server_url}/admin/mode", timeout=3).json()
    except (requests.RequestException, ValueError):
        return None


def set_demo_mode(mode: str) -> dict[str, Any]:
    r = requests.post(f"{get_settings().demo_server_url}/admin/mode", json={"mode": mode}, timeout=3)
    r.raise_for_status()
    return r.json()


def simulate_incident(mode: str = "error_500", site_name: str = DEMO_SITE, run_agent: bool = True) -> int | None:
    """Break the demo server, run enough checks to trip the detector, and (optionally) let the agent
    investigate. Returns the incident id (new, or the one already open for the site)."""
    site = get_site(site_name)
    if site is None:
        raise ValueError(f"Site {site_name} not configured")
    set_demo_mode(mode)
    log.info("Demo server switched to %s; running %d checks", mode, site.failure_threshold)
    for i in range(site.failure_threshold):
        new = run_checks_now([site_name], run_agent=run_agent)
        if new:
            return new[0]
        if i < site.failure_threshold - 1:
            time.sleep(0.5)
    existing = db.open_incident_for_site(site_name)
    return existing["id"] if existing else None


def start_investigation_in_background(incident_id: int, executor: Any) -> None:
    """Mark the incident as being investigated right away, then run the agent on a worker thread
    so the dashboard stays responsive and can show progress live."""
    db.update_incident(incident_id, status="INVESTIGATING")
    executor.submit(handle_new_incident, incident_id)


def demo_incident_in_progress(site_name: str = DEMO_SITE) -> dict[str, Any] | None:
    """The open incident for the demo site, if any."""
    return db.open_incident_for_site(site_name)


# ------------------------------------------------------------------ stats
def site_stats(hours: float = 24) -> list[dict[str, Any]]:
    """Per-site status, uptime % and latency over the window."""
    checks = db.checks_since(hours)
    latest = {c["site"]: c for c in db.latest_check_per_site()}
    out = []
    for site in load_sites():
        rows = [c for c in checks if c["site"] == site.name]
        times = [c["response_time_ms"] for c in rows if c["response_time_ms"] is not None and c["status"] != "DOWN"]
        last = latest.get(site.name)
        open_inc = db.open_incident_for_site(site.name)
        out.append({
            "site": site.name,
            "url": site.url,
            "critical": site.critical,
            "status": last["status"] if last else "UNKNOWN",
            "last_checked": last["timestamp"] if last else None,
            "last_status_code": last["status_code"] if last else None,
            "last_response_ms": last["response_time_ms"] if last else None,
            "uptime_pct": round(100 * sum(c["status"] != "DOWN" for c in rows) / len(rows), 2) if rows else None,
            "avg_latency_ms": round(statistics.mean(times), 1) if times else None,
            "checks": len(rows),
            "open_incident": open_inc["id"] if open_inc else None,
        })
    return out
