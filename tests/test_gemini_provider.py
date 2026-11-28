"""Exercise the Gemini path against a fake Gemini API (generateContent): tool calling over several
turns with Gemini 3 thought signatures passed back, JSON-schema structured diagnosis, and fallback."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

import pytest

from app import db
from app.agent import runner
from app.config import Settings, SiteConfig, get_settings
from app.monitor.scheduler import run_cycle

DIAGNOSIS = {
    "probable_causes": [{"cause": "Database connection pool exhausted", "confidence": 0.9,
                         "evidence": ["HTTP 500 with 'connection pool exhausted' in the body"]}],
    "severity": "SEV1", "severity_reason": "Critical service fully failing",
    "impact_summary": "Orders cannot be placed.", "recommended_actions": ["Restart the pool"], "confidence": 0.85,
}


class FakeGemini(BaseHTTPRequestHandler):
    requests: list[dict] = []
    fail_with: int | None = None
    busy_models: set[str] = set()
    delay = 0.0
    site_url = ""

    def do_GET(self):  # noqa: N802 - the monitored site
        self.send_response(500)
        self.end_headers()
        self.wfile.write(b'{"detail": "database connection pool exhausted"}')

    def _send(self, code: int, payload: dict) -> None:
        out = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).requests.append({"path": self.path, "key": self.headers.get("x-goog-api-key"), "body": body})
        if type(self).delay:
            import time

            time.sleep(type(self).delay)
        if any(f"/models/{m}:" in self.path for m in type(self).busy_models):
            self._send(503, {"error": {"code": 503, "message": "This model is currently experiencing high demand.",
                                       "status": "UNAVAILABLE"}})
            return
        if type(self).fail_with:
            self._send(type(self).fail_with, {"error": {"code": type(self).fail_with,
                                                        "message": "Resource has been exhausted (e.g. check quota).",
                                                        "status": "RESOURCE_EXHAUSTED"}})
            return
        model_turns = sum(1 for c in body["contents"] if c["role"] == "model")
        gen = body.get("generationConfig", {})
        if gen.get("responseMimeType") == "application/json":  # structured diagnosis
            parts = [{"text": json.dumps(DIAGNOSIS)}]
        elif model_turns == 0:
            u = urlparse(type(self).site_url)
            parts = [{"text": "Checking the endpoint and the port."},
                     {"functionCall": {"name": "http_probe", "args": {"url": type(self).site_url}},
                      "thoughtSignature": "c2lnLTE="},
                     {"functionCall": {"name": "tcp_port_check", "args": {"host": u.hostname, "port": u.port}}}]
        else:
            parts = [{"text": "The app returns 500s; the database pool is exhausted."}]
        self._send(200, {"candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": "STOP",
                                         "index": 0}],
                         "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 10, "totalTokenCount": 20},
                         "modelVersion": "gemini-3.8-flash"})

    def log_message(self, *args):
        pass


@pytest.fixture
def fake_gemini(monkeypatch):
    FakeGemini.requests, FakeGemini.fail_with, FakeGemini.busy_models, FakeGemini.delay = [], None, set(), 0.0
    srv = HTTPServer(("127.0.0.1", 0), FakeGemini)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    FakeGemini.site_url = f"{base}/site"
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTEST")
    monkeypatch.setenv("GEMINI_BASE_URL", base)
    monkeypatch.setenv("GITHUB_DRY_RUN", "true")
    get_settings.cache_clear()
    yield srv
    srv.shutdown()


def _incident() -> int:
    site = SiteConfig(name="orders", url=FakeGemini.site_url, failure_threshold=2, critical=True, timeout_s=3)
    run_cycle([site])
    return run_cycle([site])[0]


def test_gemini_settings():
    s = Settings(_env_file=None, llm_provider="gemini", gemini_api_key="AIza", openai_api_key="", groq_api_key="")
    assert s.provider == "gemini" and s.llm_model_id == "gemini-3.8-flash"
    assert s.llm_label == "gemini-3.8-flash via Google Gemini (free)"
    assert Settings(_env_file=None, llm_provider="auto", gemini_api_key="AIza", groq_api_key="g",
                    openai_api_key="").provider == "gemini"
    assert Settings(_env_file=None, llm_provider="gemini", gemini_api_key="").provider == "mock"


def test_gemini_full_path_with_thought_signatures(fake_gemini):
    incident_id = _incident()
    assert runner.start_investigation(incident_id) == "approval"

    reqs = FakeGemini.requests
    assert all(r["path"] == "/v1beta/models/gemini-3.8-flash:generateContent" for r in reqs)
    assert all(r["key"] == "AIzaTEST" for r in reqs)
    assert len(reqs) == 3  # turn 1 (tools), turn 2 (done), structured diagnosis
    # Gemini 3 requires the thought signature to be sent back with the function call
    model_turn = next(c for c in reqs[1]["body"]["contents"] if c["role"] == "model")
    assert any(p.get("thoughtSignature") == "c2lnLTE=" for p in model_turn["parts"])
    assert any("functionResponse" in p for c in reqs[1]["body"]["contents"] for p in c["parts"])
    assert reqs[2]["body"]["generationConfig"]["responseMimeType"] == "application/json"

    inc = db.get_incident(incident_id)
    assert inc["llm_mode"] == "gemini-3.8-flash via Google Gemini (free)"
    assert inc["diagnosis"]["probable_causes"][0]["cause"] == "Database connection pool exhausted"
    assert {t["tool_name"] for t in db.list_tool_runs(incident_id)} == {"http_probe", "tcp_port_check"}
    reasoning = [json.loads(e["data_json"]) for e in db.list_events(incident_id)
                 if e["event_type"] == "AGENT_DECISION" and e["data_json"]]
    assert reasoning[0]["reasoning"] == "Checking the endpoint and the port."  # plain text, not blocks


def test_gemini_quota_falls_back(fake_gemini, monkeypatch):
    FakeGemini.fail_with = 429
    monkeypatch.setattr("langchain_google_genai.chat_models.ChatGoogleGenerativeAI.max_retries", 0, raising=False)
    incident_id = _incident()
    assert runner.start_investigation(incident_id) == "approval"
    inc = db.get_incident(incident_id)
    assert inc["llm_mode"].startswith("Offline demo AI (fallback: the free usage limit was reached")


def test_busy_main_model_switches_to_backup(fake_gemini):
    FakeGemini.busy_models = {"gemini-3.8-flash"}
    incident_id = _incident()
    assert runner.start_investigation(incident_id) == "approval"
    inc = db.get_incident(incident_id)
    assert not inc["llm_mode"].startswith("Offline")  # real AI finished the job
    assert inc["diagnosis"]["probable_causes"][0]["cause"] == "Database connection pool exhausted"
    assert any("/models/gemini-3.5-flash-lite:" in r["path"] for r in FakeGemini.requests)


def test_hanging_ai_service_times_out_and_falls_back(fake_gemini, monkeypatch):
    """A Gemini call that never answers must not leave the incident stuck in 'AI investigating'."""
    FakeGemini.delay = 4
    monkeypatch.setenv("LLM_CALL_TIMEOUT_SECONDS", "1")
    get_settings.cache_clear()
    incident_id = _incident()
    assert runner.start_investigation(incident_id) == "approval"
    inc = db.get_incident(incident_id)
    assert inc["status"] == "AWAITING_APPROVAL"
    assert "did not answer within 1 seconds" in inc["llm_mode"]


def test_stalled_incident_can_be_restarted(fake_gemini, monkeypatch):
    incident_id = _incident()
    # simulate a run that died mid-way: status INVESTIGATING, no pending action, old activity
    db.update_incident(incident_id, status="INVESTIGATING")
    monkeypatch.setenv("INVESTIGATION_STALLED_SECONDS", "0")
    get_settings.cache_clear()
    assert runner.is_stalled(incident_id)

    assert runner.restart_investigation(incident_id, offline=True) == "approval"
    inc = db.get_incident(incident_id)
    assert inc["status"] == "AWAITING_APPROVAL"
    assert inc["llm_mode"] == "Offline demo AI (chosen manually)"
    assert not runner.is_stalled(incident_id)
    events = [e["event_type"] for e in db.list_events(incident_id)]
    assert "INVESTIGATION_RESTARTED" in events

    # and again with the real (fake) Gemini: fresh thread, no leftovers from the first run
    FakeGemini.requests = []
    assert runner.restart_investigation(incident_id) == "approval"
    assert db.get_incident(incident_id)["llm_mode"].endswith("Google Gemini (free)")
    assert {t["tool_name"] for t in db.list_tool_runs(incident_id)} == {"http_probe", "tcp_port_check"}
