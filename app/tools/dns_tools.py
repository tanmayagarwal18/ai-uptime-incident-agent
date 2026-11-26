"""DNS diagnostics using dnspython (with a system-resolver fallback for local hostnames)."""

from __future__ import annotations

import ipaddress
import socket
import time
from typing import Any
from urllib.parse import urlparse

import dns.exception
import dns.resolver
from langchain_core.tools import tool

from app.tools.http_tools import safe_tool

RECORD_TYPES = ("A", "AAAA", "CNAME", "MX", "NS")


def hostname_from(target: str) -> str:
    """Accept either a hostname or a full URL."""
    if "://" in target:
        return urlparse(target).hostname or target
    return target.split("/")[0].split(":")[0]


@safe_tool
def resolve_dns_impl(hostname: str) -> dict[str, Any]:
    host = hostname_from(hostname)
    try:
        ipaddress.ip_address(host)
        return {"ok": True, "hostname": host, "is_ip_literal": True, "records": {"A": [host]},
                "note": "Target is an IP address; DNS is not involved."}
    except ValueError:
        pass

    resolver = dns.resolver.Resolver()
    resolver.lifetime = 4.0
    records: dict[str, list[str]] = {}
    errors: dict[str, str] = {}
    start = time.perf_counter()
    for rtype in RECORD_TYPES:
        try:
            answer = resolver.resolve(host, rtype)
            records[rtype] = sorted(r.to_text() for r in answer)
        except dns.resolver.NXDOMAIN:
            errors[rtype] = "NXDOMAIN (domain does not exist)"
            break
        except dns.resolver.NoAnswer:
            continue
        except dns.resolver.NoNameservers:
            errors[rtype] = "SERVFAIL / no nameservers answered"
        except dns.exception.Timeout:
            errors[rtype] = "DNS query timed out"
        except Exception as exc:  # noqa: BLE001
            errors[rtype] = f"{type(exc).__name__}: {exc}"
    elapsed = round((time.perf_counter() - start) * 1000, 1)

    resolves = bool(records.get("A") or records.get("AAAA") or records.get("CNAME"))
    result: dict[str, Any] = {"ok": resolves, "hostname": host, "records": records, "errors": errors,
                              "query_time_ms": elapsed, "source": "dnspython"}
    if not resolves:
        # Local names (localhost, /etc/hosts entries) are not in public DNS.
        try:
            addrs = sorted({ai[4][0] for ai in socket.getaddrinfo(host, None)})
            result.update(ok=True, source="system resolver (hosts file / local)", records={"A": addrs},
                          public_dns_errors=result.pop("errors"), errors={})
        except socket.gaierror as exc:
            result["system_resolver_error"] = str(exc)
    return result


@tool
def resolve_dns(hostname: str) -> dict:
    """Resolve DNS records (A, AAAA, CNAME, MX, NS) for a hostname or URL using dnspython. Reports NXDOMAIN,
    SERVFAIL and timeouts. Use when a site is unreachable to rule DNS in or out."""
    return resolve_dns_impl(hostname)
