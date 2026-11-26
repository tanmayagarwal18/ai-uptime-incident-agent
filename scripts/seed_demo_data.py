"""Fill the database with 24 h of realistic check history (and one past resolved incident) so the
dashboard charts look alive before the live demo.

Usage:  python scripts/seed_demo_data.py [--hours 24] [--reset]
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db, services  # noqa: E402
from app.agent.mock_llm import diagnose  # noqa: E402
from app.config import get_settings, load_sites, setup_logging  # noqa: E402
from app.reporting.report import render_report  # noqa: E402

BASE_LATENCY = {"demo-orders-api": 45, "demo-homepage": 20, "google": 180, "github-api": 260, "httpbin": 600}


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def seed(hours: int) -> None:
    rng = random.Random(42)
    now = datetime.now(UTC)
    start = now - timedelta(hours=hours)
    sites = load_sites()
    # a past slowdown on the homepage, 6 hours ago, lasting 25 minutes
    slow_from, slow_to = now - timedelta(hours=6), now - timedelta(hours=6) + timedelta(minutes=25)
    total = 0
    for site in sites:
        base = BASE_LATENCY.get(site.name, 200)
        t = start
        while t < now - timedelta(minutes=5):
            latency = max(5.0, rng.gauss(base, base * 0.18))
            if rng.random() < 0.02:
                latency *= rng.uniform(1.8, 3.0)  # occasional spike
            status, code, err = "UP", 200, None
            if site.name == "demo-homepage" and slow_from <= t <= slow_to:
                latency = rng.uniform(1900, 3200)
            if latency > site.latency_threshold_ms:
                status = "DEGRADED"
            if site.name == "httpbin" and rng.random() < 0.004:
                status, code, err, latency = "DOWN", 502, "http_error", rng.uniform(300, 900)
            db.insert_check({"site": site.name, "url": site.url, "timestamp": iso(t), "status": status,
                             "status_code": code, "response_time_ms": round(latency, 1), "error_type": err,
                             "error_message": f"Expected HTTP 200, got {code}" if err else None,
                             "response_size": 1200})
            total += 1
            t += timedelta(minutes=5)

    # past resolved incident with a full report
    home = next((s for s in sites if s.name == "demo-homepage"), None)
    if home and not db.list_incidents(site=home.name):
        trigger_checks = [c for c in db.checks_since(hours, site=home.name) if c["status"] == "DEGRADED"][:2]
        iid = db.create_incident_if_none_open(
            home.name, f"{home.name} is DEGRADED: 2 consecutive failed checks (slow response) on {home.url}",
            {"checks": trigger_checks, "site": home.model_dump()})
        evidence = [
            {"tool": "http_probe", "args": {"url": home.url}, "result": {"ok": True, "status_code": 200, "reason": "OK", "elapsed_ms": 2480}},
            {"tool": "tcp_port_check", "args": {"host": "127.0.0.1", "port": 8001}, "result": {"ok": True, "open": True, "port": 8001, "connect_ms": 0.4}},
            {"tool": "resolve_dns", "args": {"hostname": home.url}, "result": {"ok": True, "records": {"A": ["127.0.0.1"]}}},
            {"tool": "measure_latency_samples", "args": {"url": home.url, "samples": 3},
             "result": {"ok": True, "avg_ms": 2390.0, "p95_ms": 2710.0, "min_ms": 2050.0, "max_ms": 2710.0, "success_rate": 1.0}},
        ]
        for i, e in enumerate(evidence):
            db.insert_tool_run(iid, e["tool"], e["args"], e["result"], round(rng.uniform(2, 2600), 1), 1 if i < 3 else 2)
        diag = diagnose(home.model_dump(), trigger_checks, evidence).model_dump()
        opened = iso(slow_from + timedelta(minutes=10))
        resolved = iso(slow_to + timedelta(minutes=5))
        db.update_incident(iid, status="RESOLVED", severity=diag["severity"], diagnosis_json=diag, opened_at=opened,
                           resolved_at=resolved, llm_mode="Offline demo AI")
        with db.get_conn() as conn:
            conn.execute("UPDATE incident_events SET timestamp=? WHERE incident_id=?", (opened, iid))
        db.add_event(iid, "RESOLVED", "Recovery verified: HTTP 200 in 24 ms (seeded history)")
        inc = db.get_incident(iid)
        db.update_incident(iid, report_md=render_report(inc, diag, db.list_tool_runs(iid), db.list_events(iid), home.model_dump()))
    print(f"Seeded {total} checks across {len(sites)} sites over {hours} h into {get_settings().db_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--reset", action="store_true", help="delete existing databases first")
    args = parser.parse_args()
    setup_logging()
    if args.reset:
        s = get_settings()
        for p in (s.db_path, s.checkpoint_db_path):
            for suffix in ("", "-wal", "-shm"):
                path = s.resolve(p + suffix)
                if path.exists():
                    path.unlink()
    services.bootstrap()
    seed(args.hours)


if __name__ == "__main__":
    main()
