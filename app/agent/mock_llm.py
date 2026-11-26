"""Deterministic, rule-based stand-in for the LLM so the whole agent runs offline (no AI provider configured).

It mimics the real agent's behaviour: it *chooses* diagnostic tools in rounds based on what earlier tools
returned, then produces a structured Diagnosis from the evidence with the same severity criteria.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

from langchain_core.messages import AIMessage, ToolMessage

from app.models import Diagnosis, ProbableCause


def _tool_results(messages: list[Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for m in messages:
        if isinstance(m, ToolMessage):
            try:
                out[m.name] = json.loads(m.content)
            except (TypeError, ValueError):
                out[m.name] = {"raw": m.content}
    return out


def _host_port(url: str) -> tuple[str, int]:
    p = urlparse(url)
    return p.hostname or url, p.port or (443 if p.scheme == "https" else 80)


class MockToolCallingLLM:
    """Plans tool calls in up to three rounds, like a cautious SRE."""

    def __init__(self, state: dict[str, Any]):
        self.site = state.get("site", {})

    def invoke(self, messages: list[Any]) -> AIMessage:
        name, url = self.site.get("name", ""), self.site.get("url", "")
        host, port = _host_port(url)
        rounds_done = sum(1 for m in messages if isinstance(m, AIMessage) and m.tool_calls)
        results = _tool_results(messages)

        def call(tool: str, **args: Any) -> dict[str, Any]:
            return {"name": tool, "args": args, "id": f"call_{rounds_done + 1}_{tool}", "type": "tool_call"}

        if rounds_done == 0:
            return AIMessage(
                content="Confirming the failure and checking history, DNS and TCP reachability first.",
                tool_calls=[call("get_recent_checks", site_name=name, limit=10), call("http_probe", url=url),
                            call("resolve_dns", hostname=url), call("tcp_port_check", host=host, port=port)],
            )

        if rounds_done == 1:
            probe = results.get("http_probe", {})
            tcp = results.get("tcp_port_check", {})
            dns_ok = results.get("resolve_dns", {}).get("ok", True)
            calls = [call("get_past_incidents", site_name=name, limit=5)]
            reasons = []
            if dns_ok and tcp.get("open"):
                code = probe.get("status_code")
                if url.startswith("https://"):
                    calls.append(call("ssl_certificate_check", url_or_host=url))
                    reasons.append("verify the TLS certificate")
                if code is not None and code >= 500 or code == 429:
                    calls.append(call("inspect_headers", url=url))
                    reasons.append("inspect server/retry headers")
                if code is not None and 300 <= code < 500 and code != 429:
                    calls.append(call("check_redirects", url=url))
                    reasons.append("trace redirects")
                calls.append(call("measure_latency_samples", url=url, samples=3))
                reasons.append("sample latency to see if the failure is persistent or intermittent")
            else:
                reasons.append("the host/port is unreachable, so only history is worth checking")
            return AIMessage(content="Network layer checked. Next: " + "; ".join(reasons) + ".", tool_calls=calls)

        return AIMessage(content="Investigation complete: enough evidence collected to determine the root cause.")


def _ev(name: str, r: dict[str, Any]) -> str:
    from app.reporting.report import summarize_tool_result

    return f"{name}: {summarize_tool_result(name, r)}"


def diagnose(site: dict[str, Any], trigger_checks: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> Diagnosis:
    """Rule-based root-cause analysis over the collected tool results."""
    res = {e["tool"]: e["result"] for e in evidence if isinstance(e.get("result"), dict)}
    probe, dns_r, tcp = res.get("http_probe", {}), res.get("resolve_dns", {}), res.get("tcp_port_check", {})
    tls, lat, hdr = res.get("ssl_certificate_check", {}), res.get("measure_latency_samples", {}), res.get("inspect_headers", {})
    past = res.get("get_past_incidents", {})
    critical = bool(site.get("critical"))
    threshold = site.get("latency_threshold_ms", 1500)
    code = probe.get("status_code")
    body = (probe.get("body_preview") or "").lower()
    last_err = (trigger_checks[-1].get("error_type") if trigger_checks else None) or probe.get("error_type")
    intermittent = bool(lat.get("intermittent"))

    causes: list[ProbableCause] = []
    actions: list[str] = []

    if dns_r and not dns_r.get("ok", True):
        sev = "SEV1" if critical else "SEV2"
        causes += [ProbableCause(cause="DNS resolution failure (domain misconfigured, expired or nameservers down)",
                                 confidence=0.9, evidence=[_ev("resolve_dns", dns_r), _ev("http_probe", probe)])]
        actions += ["Check the DNS zone and registrar: confirm the domain is active and A/CNAME records exist.",
                    "Verify the authoritative nameservers respond (dig +trace).",
                    "If records were recently changed, roll back and wait for TTL propagation."]
        impact = "The hostname cannot be resolved, so the service is unreachable for all users."
    elif tcp and tcp.get("open") is False:
        sev = "SEV1" if critical else "SEV2"
        state = tcp.get("state")
        if state == "closed":
            causes.append(ProbableCause(cause="Service process down: nothing is listening on the port",
                                        confidence=0.88, evidence=[_ev("tcp_port_check", tcp), _ev("http_probe", probe)]))
            causes.append(ProbableCause(cause="Container/VM stopped or crashed after a deploy or OOM kill",
                                        confidence=0.5, evidence=["Host reachable but the port refuses connections"]))
            actions += ["Check the service/container status (systemctl status / docker ps / kubectl get pods).",
                        "Inspect the application logs for crash or out-of-memory errors and restart the service.",
                        "If a deployment just happened, roll back to the last healthy release."]
        else:
            causes.append(ProbableCause(cause="Host unreachable or traffic blocked by firewall/security group",
                                        confidence=0.75, evidence=[_ev("tcp_port_check", tcp)]))
            actions += ["Check the host/VM is running and network routes are healthy.",
                        "Review recent firewall, security-group or load-balancer changes."]
        impact = "The service is completely unavailable; every request fails at the connection level."
    elif tls and tls.get("applicable") and not tls.get("valid", True):
        sev = "SEV2"
        causes.append(ProbableCause(cause="Invalid or expired TLS certificate", confidence=0.85,
                                    evidence=[_ev("ssl_certificate_check", tls)]))
        actions += ["Renew/replace the certificate and verify the full chain is served.",
                    "Enable automated renewal and expiry alerts."]
        impact = "Browsers and API clients reject the connection due to certificate errors."
    elif last_err == "timeout" or (probe.get("error_type") == "timeout"):
        sev = "SEV2" if critical else "SEV3"
        causes.append(ProbableCause(cause="Application hanging: requests time out (blocked workers or slow dependency)",
                                    confidence=0.75, evidence=[_ev("http_probe", probe)] + ([_ev("measure_latency_samples", lat)] if lat else [])))
        causes.append(ProbableCause(cause="Upstream dependency (database / external API) not responding",
                                    confidence=0.45, evidence=["TCP connect succeeds but no HTTP response is returned"]))
        actions += ["Check worker/thread pool saturation and restart hung workers.",
                    "Inspect slow-query logs and dependency health (DB, cache, third-party APIs).",
                    "Add/verify request timeouts and circuit breakers on outbound calls."]
        impact = "Users experience hanging requests and timeouts; the service is effectively unavailable."
    elif code is not None and code >= 500:
        if intermittent:
            sev = "SEV2" if critical else "SEV3"
        else:
            sev = "SEV1" if critical else "SEV2"
        if any(k in body for k in ("database", "connection pool", "db ")):
            causes.append(ProbableCause(cause="Backend dependency failure: database connection pool exhausted",
                                        confidence=0.82, evidence=[_ev("http_probe", probe), f"error body: {probe.get('body_preview', '')[:120]}"]))
            actions += ["Check database health, active connections and slow queries.",
                        "Increase the connection pool size or fix connection leaks; restart the app to release connections."]
        elif code == 503:
            causes.append(ProbableCause(cause="Service unavailable: overloaded, in maintenance or no healthy upstreams",
                                        confidence=0.78, evidence=[_ev("http_probe", probe)] + ([_ev("inspect_headers", hdr)] if hdr else [])))
            actions += ["Check load balancer target health and autoscaling limits.",
                        "Scale out instances or shed load; confirm no maintenance mode flag is enabled."]
        elif code in (502, 504):
            causes.append(ProbableCause(cause="Gateway error: reverse proxy cannot reach a healthy upstream",
                                        confidence=0.75, evidence=[_ev("http_probe", probe)]))
            actions += ["Check upstream application instances behind the proxy/load balancer."]
        else:
            causes.append(ProbableCause(cause="Application server error (unhandled exception in request handler)",
                                        confidence=0.72, evidence=[_ev("http_probe", probe)]))
            actions += ["Inspect application error logs / APM traces for the failing endpoint."]
        causes.append(ProbableCause(cause="Regression introduced by a recent deployment or config change",
                                    confidence=0.4, evidence=["Network, DNS and TLS layers are healthy; failure is at the application layer"]))
        if intermittent:
            causes[0].evidence.append(_ev("measure_latency_samples", lat))
        actions += ["Review deployments/config changes made shortly before the incident and roll back if correlated.",
                    "Add a regression test and alert on 5xx rate for this endpoint."]
        impact = ("A portion of requests fail with server errors." if intermittent
                  else f"All requests to {site.get('name')} fail with HTTP {code}; users cannot use this functionality.")
    elif code is not None and 400 <= code < 500:
        sev = "SEV2" if critical else "SEV3"
        causes.append(ProbableCause(cause=f"Client-side error HTTP {code}: endpoint removed, renamed or access rules changed",
                                    confidence=0.7, evidence=[_ev("http_probe", probe)]))
        actions += ["Verify the route still exists after the latest deploy.", "Check auth/WAF rules and the monitor's URL."]
        impact = "The monitored endpoint is not serving the expected content."
    elif lat and (lat.get("avg_ms", 0) > threshold or (probe.get("elapsed_ms") or 0) > threshold):
        sev = "SEV2" if critical and lat.get("avg_ms", 0) > 3 * threshold else "SEV3"
        causes.append(ProbableCause(cause="Performance degradation: response times above threshold",
                                    confidence=0.8, evidence=[_ev("measure_latency_samples", lat), f"threshold {threshold} ms"]))
        causes.append(ProbableCause(cause="Resource saturation (CPU/memory) or slow database queries", confidence=0.5,
                                    evidence=["All requests succeed but are consistently slow"]))
        actions += ["Check CPU, memory and request queue metrics on the application hosts.",
                    "Profile slow endpoints and database queries; add caching where possible.",
                    "Scale out horizontally if traffic increased."]
        impact = "The service works but is slow, degrading user experience and risking timeouts."
    else:
        sev = "SEV4"
        causes.append(ProbableCause(cause="Transient failure - service healthy on re-check", confidence=0.6,
                                    evidence=[_ev("http_probe", probe)] if probe else ["No failure reproduced"]))
        actions += ["Keep monitoring; investigate further if the failure recurs."]
        impact = "No ongoing user impact observed during investigation."

    if past.get("recurring"):
        actions.append("This site has recurring incidents - schedule a post-incident review to address the root cause.")
    actions.append("Use 'Verify Recovery' after the fix to confirm the service is healthy and close the incident.")

    from app.models import SEVERITY_CRITERIA

    return Diagnosis(
        probable_causes=causes,
        severity=sev,
        severity_reason=f"{sev} - {SEVERITY_CRITERIA[sev].split(':')[0]}: "
                        f"{'critical service, ' if critical else ''}{causes[0].cause.lower()}.",
        impact_summary=impact,
        recommended_actions=actions,
        confidence=round(causes[0].confidence * (0.95 if len(evidence) >= 5 else 0.8), 2),
    )


class MockAnalystLLM:
    """Mimics `ChatOpenAI.with_structured_output(Diagnosis)`."""

    def __init__(self, state: dict[str, Any], evidence: list[dict[str, Any]]):
        self.state, self.evidence = state, evidence

    def invoke(self, _messages: list[Any]) -> Diagnosis:
        return diagnose(self.state.get("site", {}), self.state.get("trigger_checks", []), self.evidence)
