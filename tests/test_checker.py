import requests
import responses

from app.models import HealthStatus
from app.monitor.checker import check_site, classify_exception


@responses.activate
def test_healthy_site_is_up(site):
    responses.get(site.url, body="ok", status=200)
    r = check_site(site)
    assert r.status == HealthStatus.UP
    assert r.status_code == 200
    assert r.error_type is None
    assert r.response_size == 2


@responses.activate
def test_unexpected_status_is_down(site):
    responses.get(site.url, status=503)
    r = check_site(site)
    assert r.status == HealthStatus.DOWN
    assert r.error_type == "http_error"
    assert "503" in r.error_message


@responses.activate
def test_slow_response_is_degraded(site):
    site.latency_threshold_ms = 0
    responses.get(site.url, status=200)
    r = check_site(site)
    assert r.status == HealthStatus.DEGRADED


@responses.activate
def test_connection_error_is_down(site):
    responses.get(site.url, body=requests.exceptions.ConnectionError("Connection refused"))
    r = check_site(site)
    assert r.status == HealthStatus.DOWN
    assert r.error_type == "connection_error"


@responses.activate
def test_timeout_is_classified(site):
    responses.get(site.url, body=requests.exceptions.ReadTimeout("read timed out"))
    assert check_site(site).error_type == "timeout"


def test_classify_dns_and_ssl():
    dns = requests.exceptions.ConnectionError("Failed to resolve: [Errno -2] Name or service not known")
    assert classify_exception(dns)[0] == "dns_error"
    assert classify_exception(requests.exceptions.SSLError("cert verify failed"))[0] == "ssl_error"
