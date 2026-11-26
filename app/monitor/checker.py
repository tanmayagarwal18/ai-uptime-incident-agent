"""HTTP health checker: one request per site, timed and classified."""

from __future__ import annotations

import logging
import time

import requests

from app.config import SiteConfig
from app.db import utcnow
from app.models import CheckResult, HealthStatus

log = logging.getLogger(__name__)

USER_AGENT = "AI-Uptime-Agent/1.0 (+https://github.com)"
_DNS_MARKERS = ("name or service not known", "nodename nor servname", "getaddrinfo failed",
                "name resolution", "nameresolutionerror", "no address associated", "temporary failure in name")


def classify_exception(exc: Exception) -> tuple[str, str]:
    """Map a requests exception to (error_type, message)."""
    msg = str(exc)
    low = msg.lower()
    if isinstance(exc, requests.exceptions.SSLError):
        return "ssl_error", msg[:500]
    if isinstance(exc, requests.exceptions.Timeout):
        return "timeout", msg[:500]
    if isinstance(exc, requests.exceptions.ConnectionError):
        if any(m in low for m in _DNS_MARKERS):
            return "dns_error", msg[:500]
        return "connection_error", msg[:500]
    return "connection_error", msg[:500]


def check_site(site: SiteConfig, session: requests.Session | None = None) -> CheckResult:
    """Perform one health check against `site` and return a classified result."""
    http = session or requests
    ts = utcnow()
    start = time.perf_counter()
    try:
        resp = http.request(
            site.method,
            site.url,
            timeout=site.timeout_s,
            headers={"User-Agent": USER_AGENT},
            allow_redirects=True,
        )
        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
    except Exception as exc:  # noqa: BLE001 - every failure is data
        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
        err_type, err_msg = classify_exception(exc)
        log.info("check %s -> DOWN (%s)", site.name, err_type)
        return CheckResult(
            site=site.name, url=site.url, timestamp=ts, status=HealthStatus.DOWN,
            response_time_ms=elapsed_ms, error_type=err_type, error_message=err_msg,
        )

    if resp.status_code != site.expected_status:
        status, err_type = HealthStatus.DOWN, "http_error"
        err_msg = f"Expected HTTP {site.expected_status}, got {resp.status_code} {resp.reason}"
    elif elapsed_ms > site.latency_threshold_ms:
        status, err_type = HealthStatus.DEGRADED, None
        err_msg = f"Response time {elapsed_ms:.0f} ms exceeds threshold {site.latency_threshold_ms} ms"
    else:
        status, err_type, err_msg = HealthStatus.UP, None, None

    log.info("check %s -> %s (%s, %.0f ms)", site.name, status, resp.status_code, elapsed_ms)
    return CheckResult(
        site=site.name, url=site.url, timestamp=ts, status=status, status_code=resp.status_code,
        response_time_ms=elapsed_ms, error_type=err_type, error_message=err_msg,
        response_size=len(resp.content or b""),
    )
