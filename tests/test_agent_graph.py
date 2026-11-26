"""Runs the full LangGraph workflow in offline mock-LLM mode against a local HTTP server."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app import db
from app.agent import runner
from app.agent.mock_llm import diagnose
from app.config import SiteConfig
from app.monitor.scheduler import run_cycle


class _Handler(BaseHTTPRequestHandler):
    status = 500
    body = b'{"detail": "OrdersService: database connection pool exhausted"}'

    def do_GET(self):  # noqa: N802
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(type(self).body if type(self).status >= 400 else b'{"ok": true}')

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    _Handler.status = 500
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()


@pytest.fixture
def failing_site(server):
    return SiteConfig(name="orders", url=f"http://127.0.0.1:{server.server_port}/api/orders",
                      failure_threshold=2, critical=True, timeout_s=3)


def _open_incident(site) -> int:
    run_cycle([site])
    new = run_cycle([site])
    assert len(new) == 1
    return new[0]


def test_agent_investigates_and_pauses_for_approval(failing_site):
    incident_id = _open_incident(failing_site)
    assert runner.start_investigation(incident_id) == "approval"

    inc = db.get_incident(incident_id)
    assert inc["status"] == "AWAITING_APPROVAL"
    assert inc["severity"] == "SEV1"  # critical service, every request failing
    assert "database" in inc["diagnosis"]["probable_causes"][0]["cause"].lower()
    assert inc["report_md"].startswith(f"# Incident #{incident_id}")

    tools_used = {t["tool_name"] for t in db.list_tool_runs(incident_id)}
    assert {"http_probe", "resolve_dns", "tcp_port_check", "get_recent_checks"} <= tools_used
    assert "measure_latency_samples" in tools_used  # chosen in round 2 based on round-1 results


def test_rejection_closes_without_ticket(failing_site):
    incident_id = _open_incident(failing_site)
    runner.start_investigation(incident_id)
    assert runner.submit_approval(incident_id, approved=False) is None
    inc = db.get_incident(incident_id)
    assert inc["status"] == "REJECTED" and inc["github_issue_url"] is None


def test_cannot_approve_twice(failing_site):
    incident_id = _open_incident(failing_site)
    runner.start_investigation(incident_id)
    runner.submit_approval(incident_id, approved=False)
    with pytest.raises(ValueError):
        runner.submit_approval(incident_id, approved=True)


def test_mock_diagnosis_rules():
    site = {"name": "s", "critical": False, "latency_threshold_ms": 1000}
    refused = diagnose(site, [], [{"tool": "tcp_port_check", "args": {}, "result": {"open": False, "state": "closed", "port": 80}},
                                  {"tool": "resolve_dns", "args": {}, "result": {"ok": True}}])
    assert refused.severity == "SEV2" and "listening" in refused.probable_causes[0].cause

    dns_fail = diagnose({**site, "critical": True}, [], [{"tool": "resolve_dns", "args": {}, "result": {"ok": False, "errors": {"A": "NXDOMAIN"}}}])
    assert dns_fail.severity == "SEV1" and "DNS" in dns_fail.probable_causes[0].cause

    slow = diagnose(site, [], [
        {"tool": "http_probe", "args": {}, "result": {"ok": True, "status_code": 200, "elapsed_ms": 2500}},
        {"tool": "tcp_port_check", "args": {}, "result": {"open": True}},
        {"tool": "measure_latency_samples", "args": {}, "result": {"avg_ms": 2400, "p95_ms": 2600, "success_rate": 1.0}},
    ])
    assert slow.severity == "SEV3" and "Performance" in slow.probable_causes[0].cause
