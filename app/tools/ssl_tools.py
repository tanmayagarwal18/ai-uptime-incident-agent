"""TLS certificate diagnostics."""

from __future__ import annotations

import socket
import ssl
from datetime import UTC, datetime
from typing import Any

from langchain_core.tools import tool

from app.tools.dns_tools import hostname_from
from app.tools.http_tools import safe_tool


@safe_tool
def ssl_certificate_check_impl(target: str, port: int = 443, timeout_s: float = 5.0) -> dict[str, Any]:
    if target.startswith("http://"):
        return {"ok": True, "applicable": False, "note": "Plain HTTP URL - no TLS certificate involved."}
    host = hostname_from(target)
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=timeout_s) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                cert = tls.getpeercert()
                version = tls.version()
    except ssl.SSLCertVerificationError as exc:
        return {"ok": False, "applicable": True, "host": host, "valid": False,
                "error": f"Certificate verification failed: {exc.verify_message}"}
    except ssl.SSLError as exc:
        return {"ok": False, "applicable": True, "host": host, "valid": False, "error": f"TLS handshake error: {exc}"}

    not_after = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=UTC)
    days_left = (not_after - datetime.now(UTC)).days
    issuer = dict(x[0] for x in cert.get("issuer", ()))
    subject = dict(x[0] for x in cert.get("subject", ()))
    return {
        "ok": days_left > 0, "applicable": True, "host": host, "valid": True, "tls_version": version,
        "subject_cn": subject.get("commonName"), "issuer": issuer.get("organizationName") or issuer.get("commonName"),
        "expires_at": not_after.isoformat(), "days_until_expiry": days_left, "expiring_soon": days_left < 14,
    }


@tool
def ssl_certificate_check(url_or_host: str) -> dict:
    """Inspect the TLS certificate of an HTTPS site: validity, hostname match, issuer, TLS version and days
    until expiry. Use for SSL errors or as a routine check on HTTPS sites. Not applicable to http:// URLs."""
    return ssl_certificate_check_impl(url_or_host)
