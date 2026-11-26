import socket

import dns.resolver
import requests
import responses

from app import db
from app.db import utcnow
from app.tools import ALL_TOOLS, TOOLS_BY_NAME
from app.tools.dns_tools import hostname_from, resolve_dns_impl
from app.tools.http_tools import check_redirects_impl, http_probe_impl, inspect_headers_impl, safe_tool
from app.tools.network_tools import measure_latency_samples_impl, tcp_port_check_impl
from app.tools.ssl_tools import ssl_certificate_check_impl

URL = "https://svc.example.test/api"


def test_registry_has_all_tools_with_descriptions():
    assert len(ALL_TOOLS) == 9
    for t in ALL_TOOLS:
        assert t.description and len(t.description) > 30


def test_safe_tool_turns_exceptions_into_data():
    @safe_tool
    def boom():
        raise RuntimeError("bad")

    assert boom() == {"ok": False, "error": "RuntimeError: bad"}


@responses.activate
def test_http_probe_reports_status_and_body():
    responses.get(URL, status=500, body="Internal Server Error: db pool exhausted")
    r = http_probe_impl(URL)
    assert r["status_code"] == 500 and r["ok"] is False
    assert "db pool" in r["body_preview"]


@responses.activate
def test_http_probe_connection_error():
    responses.get(URL, body=requests.exceptions.ConnectionError("Connection refused"))
    assert http_probe_impl(URL)["error_type"] == "connection_error"


@responses.activate
def test_check_redirects_chain():
    responses.get("https://a.test/", status=301, headers={"Location": "https://b.test/"})
    responses.get("https://b.test/", status=200)
    r = check_redirects_impl("https://a.test/")
    assert r["redirect_count"] == 1 and r["final_status"] == 200


@responses.activate
def test_inspect_headers_filters():
    responses.get(URL, status=503, headers={"Retry-After": "120", "Server": "nginx", "X-Junk": "1"})
    r = inspect_headers_impl(URL)
    assert r["has_retry_after"] and r["headers"]["server"] == "nginx" and "x-junk" not in r["headers"]


@responses.activate
def test_latency_samples_detect_intermittent():
    responses.get(URL, status=200)
    responses.get(URL, status=500)
    r = measure_latency_samples_impl(URL, samples=4)
    assert r["samples"] == 4 and r["intermittent"] is True


def test_hostname_from_url():
    assert hostname_from("https://api.example.com:8443/x") == "api.example.com"
    assert hostname_from("example.com") == "example.com"


def test_resolve_dns_ip_literal():
    assert resolve_dns_impl("http://127.0.0.1:8001/")["is_ip_literal"] is True


def test_resolve_dns_nxdomain(monkeypatch):
    def fake_resolve(self, host, rtype):
        raise dns.resolver.NXDOMAIN()

    monkeypatch.setattr(dns.resolver.Resolver, "resolve", fake_resolve)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: (_ for _ in ()).throw(socket.gaierror("nope")))
    r = resolve_dns_impl("missing.example.test")
    assert r["ok"] is False and "NXDOMAIN" in r["errors"]["A"]


def test_tcp_port_check_open_and_closed():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        assert tcp_port_check_impl("127.0.0.1", port)["open"] is True
    finally:
        server.close()
    closed = tcp_port_check_impl("127.0.0.1", port)
    assert closed["open"] is False and closed["state"] == "closed"


def test_ssl_not_applicable_for_http():
    assert ssl_certificate_check_impl("http://example.test")["applicable"] is False


def test_history_tools():
    for status in ("UP", "DOWN", "DOWN"):
        db.insert_check({"site": "s1", "url": "u", "timestamp": utcnow(), "status": status, "status_code": 200,
                         "response_time_ms": 10, "error_type": None, "error_message": None, "response_size": 1})
    r = TOOLS_BY_NAME["get_recent_checks"].invoke({"site_name": "s1"})
    assert r["count"] == 3 and r["checks_newest_first"][0]["status"] == "DOWN"
    inc = db.create_incident_if_none_open("s1", "x", {})
    db.update_incident(inc, status="RESOLVED")
    assert TOOLS_BY_NAME["get_past_incidents"].invoke({"site_name": "s1"})["past_incident_count"] == 1
