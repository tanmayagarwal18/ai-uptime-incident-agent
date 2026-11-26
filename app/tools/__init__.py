"""Diagnostic tools the LLM agent can choose to call."""

from app.tools.dns_tools import resolve_dns
from app.tools.history_tools import get_past_incidents, get_recent_checks
from app.tools.http_tools import check_redirects, http_probe, inspect_headers
from app.tools.network_tools import measure_latency_samples, tcp_port_check
from app.tools.ssl_tools import ssl_certificate_check

ALL_TOOLS = [
    http_probe,
    check_redirects,
    inspect_headers,
    resolve_dns,
    tcp_port_check,
    ssl_certificate_check,
    measure_latency_samples,
    get_recent_checks,
    get_past_incidents,
]
TOOLS_BY_NAME = {t.name: t for t in ALL_TOOLS}

__all__ = ["ALL_TOOLS", "TOOLS_BY_NAME"]
