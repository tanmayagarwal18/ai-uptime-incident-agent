"""Exercise the real (non-mock) LLM path against a fake OpenAI-compatible server that behaves like
GitHub Models: checks the URL, token and model id sent, tool calling, structured output and fallback."""

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


def _completion(message: dict) -> dict:
    return {"id": "chatcmpl-1", "object": "chat.completion", "created": 0, "model": "openai/gpt-4.1-mini",
            "choices": [{"index": 0, "message": message,
                         "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}}


def _tool_call(call_id: str, name: str, args: dict) -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class FakeModels(BaseHTTPRequestHandler):
    """GET /site -> HTTP 500 (the monitored site). POST /inference/chat/completions -> scripted LLM."""

    requests: list[dict] = []
    fail_with: int | None = None
    site_url = ""

    def do_GET(self):  # noqa: N802
        self.send_response(500)
        self.end_headers()
        self.wfile.write(b'{"detail": "database connection pool exhausted"}')

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).requests.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
        if type(self).fail_with:
            self.send_response(type(self).fail_with)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": {"message": "Rate limit exceeded", "code": "RateLimitReached"}}')
            return
        tools = [t["function"]["name"] for t in body.get("tools", [])]
        turns = sum(1 for m in body["messages"] if m["role"] == "assistant")
        if "Diagnosis" in tools:  # analyst: structured output via function calling
            diag = {"probable_causes": [{"cause": "Database connection pool exhausted", "confidence": 0.9,
                                         "evidence": ["HTTP 500 with 'connection pool exhausted' in body"]}],
                    "severity": "SEV1", "severity_reason": "Critical service fully failing",
                    "impact_summary": "Orders cannot be placed.", "recommended_actions": ["Restart the pool"],
                    "confidence": 0.85}
            msg = {"role": "assistant", "content": None, "tool_calls": [_tool_call("d1", "Diagnosis", diag)]}
        elif turns == 0:
            host, port = urlparse(type(self).site_url).hostname, urlparse(type(self).site_url).port
            msg = {"role": "assistant", "content": "Checking the endpoint and the port.",
                   "tool_calls": [_tool_call("c1", "http_probe", {"url": type(self).site_url}),
                                  _tool_call("c2", "tcp_port_check", {"host": host, "port": port})]}
        else:
            msg = {"role": "assistant", "content": "The app returns 500s; the DB pool is exhausted."}
        out = json.dumps(_completion(msg)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *args):
        pass


@pytest.fixture
def fake_models(monkeypatch):
    FakeModels.requests, FakeModels.fail_with = [], None
    srv = HTTPServer(("127.0.0.1", 0), FakeModels)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    FakeModels.site_url = f"{base}/site"
    monkeypatch.setenv("LLM_PROVIDER", "github_models")
    monkeypatch.setenv("GITHUB_TOKEN", "github_pat_test123")
    monkeypatch.setenv("OPENAI_BASE_URL", f"{base}/inference")
    monkeypatch.setenv("GITHUB_DRY_RUN", "true")
    get_settings.cache_clear()
    yield srv
    srv.shutdown()


def _incident() -> int:
    site = SiteConfig(name="orders", url=FakeModels.site_url, failure_threshold=2, critical=True, timeout_s=3)
    run_cycle([site])
    return run_cycle([site])[0]


def test_github_models_settings_resolution():
    s = Settings(_env_file=None, llm_provider="github_models", github_token="tok", openai_api_key="")
    assert s.provider == "github_models"
    assert s.llm_model_id == "openai/gpt-4.1-mini"
    assert s.llm_base_url == "https://models.github.ai/inference"
    assert s.llm_api_key == "tok" and "GitHub Models" in s.llm_label
    assert Settings(_env_file=None, llm_provider="github_models", github_token="", github_models_token="",
                    openai_api_key="").provider == "mock"
    assert Settings(_env_file=None, llm_provider="github_models", github_token="a",
                    github_models_token="b").llm_api_key == "b"  # dedicated token wins


def test_real_llm_path_with_github_models(fake_models):
    incident_id = _incident()
    assert runner.start_investigation(incident_id) == "approval"

    reqs = FakeModels.requests
    assert all(r["path"] == "/inference/chat/completions" for r in reqs)
    assert all(r["auth"] == "Bearer github_pat_test123" for r in reqs)
    assert all(r["body"]["model"] == "openai/gpt-4.1-mini" for r in reqs)
    assert len(reqs) == 3  # turn 1 (tools), turn 2 (done), analyst
    # the second request carries the tool results back to the model
    assert any(m["role"] == "tool" for m in reqs[1]["body"]["messages"])

    inc = db.get_incident(incident_id)
    assert inc["llm_mode"] == "gpt-4.1-mini via GitHub Models (free)"
    assert inc["severity"] == "SEV1"
    assert inc["diagnosis"]["probable_causes"][0]["cause"] == "Database connection pool exhausted"
    assert {t["tool_name"] for t in db.list_tool_runs(incident_id)} == {"http_probe", "tcp_port_check"}


def test_requests_stay_small_for_free_tier(fake_models):
    runner.start_investigation(_incident())
    for r in FakeModels.requests:
        for m in r["body"]["messages"]:
            if m["role"] == "tool":
                assert len(m["content"]) <= get_settings().llm_max_tool_result_chars + 20
        assert len(json.dumps(r["body"])) < 24000  # roughly < 6-7k tokens incl. tool schemas


def test_quota_exhausted_falls_back_to_offline_ai(fake_models):
    FakeModels.fail_with = 429
    incident_id = _incident()
    assert runner.start_investigation(incident_id) == "approval"  # the demo keeps working
    inc = db.get_incident(incident_id)
    assert inc["llm_mode"].startswith("Offline demo AI (fallback: the free usage limit was reached")
    assert inc["diagnosis"]["severity"] == "SEV1"
    events = [e["event_type"] for e in db.list_events(incident_id)]
    assert "AI_FALLBACK" in events


def test_bad_token_message(fake_models):
    FakeModels.fail_with = 401
    incident_id = _incident()
    runner.start_investigation(incident_id)
    assert "Models: read" in db.get_incident(incident_id)["llm_mode"]
