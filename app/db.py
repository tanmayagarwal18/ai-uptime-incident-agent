"""SQLite storage: sites, health checks, incidents, agent tool runs and incident timeline events."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from app.config import SiteConfig, get_settings

OPEN_STATUSES = ("OPEN", "INVESTIGATING", "AWAITING_APPROVAL", "TICKET_CREATED")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sites (
    name TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    critical INTEGER NOT NULL DEFAULT 0,
    latency_threshold_ms INTEGER NOT NULL,
    config_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site TEXT NOT NULL,
    url TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    status TEXT NOT NULL,
    status_code INTEGER,
    response_time_ms REAL,
    error_type TEXT,
    error_message TEXT,
    response_size INTEGER
);
CREATE INDEX IF NOT EXISTS idx_checks_site_ts ON checks(site, timestamp);
CREATE INDEX IF NOT EXISTS idx_checks_ts ON checks(timestamp);

CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site TEXT NOT NULL,
    status TEXT NOT NULL,
    severity TEXT,
    opened_at TEXT NOT NULL,
    resolved_at TEXT,
    summary TEXT,
    trigger_json TEXT,
    diagnosis_json TEXT,
    report_md TEXT,
    github_issue_number INTEGER,
    github_issue_url TEXT,
    github_payload_json TEXT,
    llm_mode TEXT
);
CREATE INDEX IF NOT EXISTS idx_incidents_site ON incidents(site);
CREATE INDEX IF NOT EXISTS idx_incidents_opened ON incidents(opened_at);

CREATE TABLE IF NOT EXISTS tool_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    args_json TEXT,
    result_json TEXT,
    duration_ms REAL,
    iteration INTEGER
);
CREATE INDEX IF NOT EXISTS idx_tool_runs_incident ON tool_runs(incident_id);

CREATE TABLE IF NOT EXISTS incident_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,
    message TEXT,
    data_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_incident ON incident_events(incident_id, timestamp);
"""


def utcnow() -> str:
    """Current UTC time as an ISO-8601 string."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    path = get_settings().resolve(get_settings().db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Yield a connection that commits on success and always closes."""
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Create all tables and indexes if they do not exist."""
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def _rows(cur: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------- sites
def upsert_sites(sites: list[SiteConfig]) -> None:
    with get_conn() as conn:
        for s in sites:
            conn.execute(
                """INSERT INTO sites(name, url, critical, latency_threshold_ms, config_json, updated_at)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(name) DO UPDATE SET url=excluded.url, critical=excluded.critical,
                     latency_threshold_ms=excluded.latency_threshold_ms, config_json=excluded.config_json,
                     updated_at=excluded.updated_at""",
                (s.name, s.url, int(s.critical), s.latency_threshold_ms, s.model_dump_json(), utcnow()),
            )


def list_sites() -> list[dict[str, Any]]:
    with get_conn() as conn:
        return _rows(conn.execute("SELECT * FROM sites ORDER BY name"))


# ---------------------------------------------------------------- checks
def insert_check(check: dict[str, Any]) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO checks(site, url, timestamp, status, status_code, response_time_ms,
                                  error_type, error_message, response_size)
               VALUES (:site, :url, :timestamp, :status, :status_code, :response_time_ms,
                       :error_type, :error_message, :response_size)""",
            check,
        )
        return int(cur.lastrowid)


def recent_checks(site: str, limit: int = 10) -> list[dict[str, Any]]:
    """Most recent checks for a site, newest first."""
    with get_conn() as conn:
        return _rows(
            conn.execute("SELECT * FROM checks WHERE site=? ORDER BY id DESC LIMIT ?", (site, limit))
        )


def checks_since(hours: float = 24, site: str | None = None) -> list[dict[str, Any]]:
    cutoff = (datetime.now(UTC) - timedelta(hours=hours)).isoformat(timespec="seconds")
    q = "SELECT * FROM checks WHERE timestamp >= ?"
    args: list[Any] = [cutoff]
    if site:
        q += " AND site = ?"
        args.append(site)
    with get_conn() as conn:
        return _rows(conn.execute(q + " ORDER BY timestamp", args))


def latest_check_per_site() -> list[dict[str, Any]]:
    with get_conn() as conn:
        return _rows(
            conn.execute(
                """SELECT c.* FROM checks c
                   JOIN (SELECT site, MAX(id) AS mid FROM checks GROUP BY site) m ON c.id = m.mid
                   ORDER BY c.site"""
            )
        )


# ---------------------------------------------------------------- incidents
def open_incident_for_site(site: str) -> dict[str, Any] | None:
    placeholders = ",".join("?" * len(OPEN_STATUSES))
    with get_conn() as conn:
        row = conn.execute(
            f"SELECT * FROM incidents WHERE site=? AND status IN ({placeholders}) ORDER BY id DESC LIMIT 1",
            (site, *OPEN_STATUSES),
        ).fetchone()
        return dict(row) if row else None


def create_incident_if_none_open(site: str, summary: str, trigger: dict[str, Any]) -> int | None:
    """Atomically create an incident unless one is already open for the site. Returns new id or None."""
    placeholders = ",".join("?" * len(OPEN_STATUSES))
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            f"SELECT id FROM incidents WHERE site=? AND status IN ({placeholders})", (site, *OPEN_STATUSES)
        ).fetchone()
        if existing:
            conn.rollback()
            return None
        now = utcnow()
        cur = conn.execute(
            "INSERT INTO incidents(site, status, opened_at, summary, trigger_json) VALUES (?,?,?,?,?)",
            (site, "OPEN", now, summary, json.dumps(trigger)),
        )
        incident_id = int(cur.lastrowid)
        conn.execute(
            "INSERT INTO incident_events(incident_id, timestamp, event_type, message) VALUES (?,?,?,?)",
            (incident_id, now, "OPENED", summary),
        )
        conn.commit()
        return incident_id
    finally:
        conn.close()


def update_incident(incident_id: int, **fields: Any) -> None:
    if not fields:
        return
    for key in ("diagnosis_json", "github_payload_json", "trigger_json"):
        if key in fields and not isinstance(fields[key], str | type(None)):
            fields[key] = json.dumps(fields[key])
    cols = ", ".join(f"{k}=?" for k in fields)
    with get_conn() as conn:
        conn.execute(f"UPDATE incidents SET {cols} WHERE id=?", (*fields.values(), incident_id))


def get_incident(incident_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
    if not row:
        return None
    inc = dict(row)
    for key in ("diagnosis_json", "github_payload_json", "trigger_json"):
        inc[key.removesuffix("_json")] = json.loads(inc[key]) if inc.get(key) else None
    return inc


def list_incidents(site: str | None = None, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    q = "SELECT * FROM incidents WHERE 1=1"
    args: list[Any] = []
    if site:
        q += " AND site=?"
        args.append(site)
    if status:
        q += " AND status=?"
        args.append(status)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with get_conn() as conn:
        return _rows(conn.execute(q, args))


# ---------------------------------------------------------------- events & tool runs
def add_event(incident_id: int, event_type: str, message: str = "", data: Any = None) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO incident_events(incident_id, timestamp, event_type, message, data_json) VALUES (?,?,?,?,?)",
            (incident_id, utcnow(), event_type, message, json.dumps(data) if data is not None else None),
        )


def list_events(incident_id: int) -> list[dict[str, Any]]:
    with get_conn() as conn:
        return _rows(
            conn.execute("SELECT * FROM incident_events WHERE incident_id=? ORDER BY id", (incident_id,))
        )


def insert_tool_run(
    incident_id: int, tool_name: str, args: dict[str, Any], result: Any, duration_ms: float, iteration: int
) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO tool_runs(incident_id, timestamp, tool_name, args_json, result_json, duration_ms, iteration)
               VALUES (?,?,?,?,?,?,?)""",
            (incident_id, utcnow(), tool_name, json.dumps(args), json.dumps(result, default=str), duration_ms, iteration),
        )


def list_tool_runs(incident_id: int) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = _rows(conn.execute("SELECT * FROM tool_runs WHERE incident_id=? ORDER BY id", (incident_id,)))
    for r in rows:
        r["args"] = json.loads(r["args_json"]) if r["args_json"] else {}
        r["result"] = json.loads(r["result_json"]) if r["result_json"] else None
    return rows
