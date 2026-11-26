"""Network-level diagnostics: TCP reachability and latency sampling."""

from __future__ import annotations

import socket
import statistics
import time
from typing import Any

import requests
from langchain_core.tools import tool

from app.monitor.checker import USER_AGENT, classify_exception
from app.tools.http_tools import safe_tool


@safe_tool
def tcp_port_check_impl(host: str, port: int = 443, timeout_s: float = 4.0) -> dict[str, Any]:
    start = time.perf_counter()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout_s):
            return {"ok": True, "host": host, "port": port, "open": True,
                    "connect_ms": round((time.perf_counter() - start) * 1000, 1)}
    except TimeoutError:
        state, detail = "filtered/timeout", "No response within timeout (firewall or host down)"
    except ConnectionRefusedError:
        state, detail = "closed", "Connection refused: host is up but nothing is listening on this port"
    except socket.gaierror as exc:
        state, detail = "unresolvable", f"Hostname could not be resolved: {exc}"
    except OSError as exc:
        state, detail = "unreachable", str(exc)
    return {"ok": False, "host": host, "port": port, "open": False, "state": state, "detail": detail,
            "elapsed_ms": round((time.perf_counter() - start) * 1000, 1)}


@safe_tool
def measure_latency_samples_impl(url: str, samples: int = 5, timeout_s: float = 8.0) -> dict[str, Any]:
    samples = max(1, min(int(samples), 10))
    times: list[float] = []
    statuses: list[int | str] = []
    for _ in range(samples):
        start = time.perf_counter()
        try:
            r = requests.get(url, timeout=timeout_s, headers={"User-Agent": USER_AGENT})
            statuses.append(r.status_code)
        except Exception as exc:  # noqa: BLE001
            statuses.append(classify_exception(exc)[0])
        times.append(round((time.perf_counter() - start) * 1000, 1))
    ordered = sorted(times)
    p95 = ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]
    success = sum(1 for s in statuses if isinstance(s, int) and s < 400)
    return {
        "ok": True, "url": url, "samples": samples, "min_ms": ordered[0], "avg_ms": round(statistics.mean(times), 1),
        "p95_ms": p95, "max_ms": ordered[-1], "statuses": statuses, "success_rate": round(success / samples, 2),
        "intermittent": 0 < success < samples,
    }


@tool
def tcp_port_check(host: str, port: int = 443) -> dict:
    """Open a raw TCP connection to host:port. Distinguishes 'closed' (connection refused: service down),
    'filtered/timeout' (firewall or host down) and 'unresolvable'. Use to check if the server process is up."""
    return tcp_port_check_impl(host, port)


@tool
def measure_latency_samples(url: str, samples: int = 5) -> dict:
    """Send several GET requests to `url` and return min/avg/p95/max latency, the status of each attempt and
    the success rate. Use for slow responses and to detect intermittent failures."""
    return measure_latency_samples_impl(url, samples)
