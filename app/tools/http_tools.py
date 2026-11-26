"""HTTP-level diagnostic tools exposed to the LLM agent."""

from __future__ import annotations

import functools
import time
from collections.abc import Callable
from typing import Any

import requests
from langchain_core.tools import tool

from app.monitor.checker import USER_AGENT, classify_exception

INTERESTING_HEADERS = (
    "server", "content-type", "cache-control", "retry-after", "x-powered-by", "via", "x-cache",
    "cf-ray", "x-request-id", "strict-transport-security", "location", "x-ratelimit-remaining",
)


def safe_tool(fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    """Make a diagnostic function never raise: exceptions are returned as data."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:300]}"}

    return wrapper


@safe_tool
def http_probe_impl(url: str, method: str = "GET", timeout_s: float = 8.0) -> dict[str, Any]:
    start = time.perf_counter()
    try:
        r = requests.request(method.upper(), url, timeout=timeout_s, headers={"User-Agent": USER_AGENT})
    except Exception as exc:  # noqa: BLE001
        err_type, msg = classify_exception(exc)
        return {"ok": False, "url": url, "error_type": err_type, "error": msg[:300],
                "elapsed_ms": round((time.perf_counter() - start) * 1000, 1)}
    body = r.text[:300] if r.text else ""
    return {
        "ok": r.ok, "url": url, "status_code": r.status_code, "reason": r.reason,
        "elapsed_ms": round((time.perf_counter() - start) * 1000, 1),
        "content_length": len(r.content or b""), "content_type": r.headers.get("content-type"),
        "body_preview": body,
    }


@safe_tool
def check_redirects_impl(url: str, timeout_s: float = 8.0) -> dict[str, Any]:
    r = requests.get(url, timeout=timeout_s, allow_redirects=True, headers={"User-Agent": USER_AGENT})
    chain = [{"url": h.url, "status_code": h.status_code, "location": h.headers.get("location")} for h in r.history]
    return {
        "ok": True, "redirect_count": len(chain), "chain": chain, "final_url": r.url,
        "final_status": r.status_code, "possible_loop": len(chain) >= 5,
        "downgraded_to_http": url.startswith("https://") and r.url.startswith("http://"),
    }


@safe_tool
def inspect_headers_impl(url: str, timeout_s: float = 8.0) -> dict[str, Any]:
    r = requests.get(url, timeout=timeout_s, headers={"User-Agent": USER_AGENT})
    headers = {k.lower(): v for k, v in r.headers.items() if k.lower() in INTERESTING_HEADERS}
    return {"ok": True, "status_code": r.status_code, "headers": headers,
            "has_retry_after": "retry-after" in headers}


@tool
def http_probe(url: str, method: str = "GET") -> dict:
    """Send a single HTTP request to `url` and report status code, reason, latency, content type and a short
    body preview (useful to read error pages). Use first to confirm the failure is still happening."""
    return http_probe_impl(url, method)


@tool
def check_redirects(url: str) -> dict:
    """Follow redirects for `url` and return the full redirect chain, the final URL/status, and flags for
    redirect loops or HTTPS->HTTP downgrades."""
    return check_redirects_impl(url)


@tool
def inspect_headers(url: str) -> dict:
    """Fetch `url` and return diagnostic response headers (server, retry-after, cache, CDN, rate-limit).
    Useful for 503/429 responses, CDN/proxy errors and identifying the server software."""
    return inspect_headers_impl(url)
