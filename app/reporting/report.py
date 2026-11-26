"""Markdown incident report rendering and human-readable tool-result summaries."""

from __future__ import annotations

from typing import Any


def summarize_tool_result(name: str, r: Any) -> str:
    """One-line human summary of a diagnostic tool result (used in reports and the dashboard)."""
    if not isinstance(r, dict):
        return str(r)[:120]
    if r.get("error") and not r.get("ok") and name not in ("http_probe", "resolve_dns", "ssl_certificate_check"):
        return f"error: {r['error'][:100]}"
    try:
        if name == "http_probe":
            if r.get("status_code") is not None:
                return f"HTTP {r['status_code']} {r.get('reason', '')} in {r.get('elapsed_ms')} ms"
            return f"{r.get('error_type', 'error')} after {r.get('elapsed_ms')} ms"
        if name == "resolve_dns":
            if r.get("ok"):
                a = r.get("records", {}).get("A") or r.get("records", {}).get("CNAME") or []
                return f"resolves to {', '.join(a[:3])} ({r.get('source', 'dns')})"
            return f"DNS failure: {'; '.join(r.get('errors', {}).values()) or r.get('error', '')}"[:120]
        if name == "tcp_port_check":
            return f"port {r.get('port')} open ({r.get('connect_ms')} ms)" if r.get("open") else f"port {r.get('port')} {r.get('state')}"
        if name == "ssl_certificate_check":
            if not r.get("applicable", True):
                return "not applicable (plain HTTP)"
            if r.get("valid"):
                return f"valid, {r.get('issuer')}, expires in {r.get('days_until_expiry')} days"
            return f"invalid: {r.get('error', '')[:90]}"
        if name == "measure_latency_samples":
            return (f"avg {r.get('avg_ms')} ms, p95 {r.get('p95_ms')} ms, success {int(r.get('success_rate', 0) * 100)}%"
                    + (" (intermittent)" if r.get("intermittent") else ""))
        if name == "inspect_headers":
            h = r.get("headers", {})
            return f"HTTP {r.get('status_code')}, server={h.get('server', '?')}" + (
                f", retry-after={h['retry-after']}" if "retry-after" in h else "")
        if name == "check_redirects":
            return f"{r.get('redirect_count')} redirect(s) -> {r.get('final_status')} {r.get('final_url')}"[:120]
        if name == "get_recent_checks":
            last = r.get("last_successful_check")
            last_txt = f"{last[11:16]} UTC" if last else "none in this window"
            ratio = r.get("up_ratio")
            pct = f"{round(ratio * 100)}% healthy" if ratio is not None else "no data"
            return f"last {r.get('count')} checks: {pct}, last healthy check {last_txt}"
        if name == "get_past_incidents":
            return f"{r.get('past_incident_count')} past incident(s)" + (" - recurring" if r.get("recurring") else "")
    except Exception:  # noqa: BLE001
        pass
    return str(r)[:120]


def _md_escape(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_report(
    incident: dict[str, Any],
    diagnosis: dict[str, Any],
    tool_runs: list[dict[str, Any]],
    events: list[dict[str, Any]],
    site: dict[str, Any],
) -> str:
    """Render a complete Markdown incident report."""
    sev = diagnosis.get("severity", "?")
    out: list[str] = [
        f"# Incident #{incident['id']}: {site.get('name')} - {sev}",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Site | `{site.get('name')}` |",
        f"| URL | {site.get('url')} |",
        f"| Severity | **{sev}** |",
        f"| Status | {incident.get('status')} |",
        f"| Opened at (UTC) | {incident.get('opened_at')} |",
        f"| Critical service | {'yes' if site.get('critical') else 'no'} |",
        f"| Diagnosis confidence | {round(float(diagnosis.get('confidence', 0)) * 100)}% |",
        f"| Analysed by | {incident.get('llm_mode') or 'agent'} |",
        "",
        "## Summary",
        incident.get("summary") or "",
        "",
        "## Impact",
        diagnosis.get("impact_summary", ""),
        "",
        f"**Severity rationale:** {diagnosis.get('severity_reason', '')}",
        "",
        "## Root cause analysis",
    ]
    for i, c in enumerate(diagnosis.get("probable_causes", []), 1):
        out.append(f"{i}. **{c['cause']}** - confidence {round(float(c.get('confidence', 0)) * 100)}%")
        for ev in c.get("evidence", []):
            out.append(f"   - {ev}")
    out += ["", "## Recommended actions"]
    out += [f"{i}. {a}" for i, a in enumerate(diagnosis.get("recommended_actions", []), 1)]

    out += ["", "## Diagnostic evidence (collected by the AI agent)", "",
            "| # | Tool | Arguments | Finding | Time |", "|---|---|---|---|---|"]
    for i, t in enumerate(tool_runs, 1):
        args = ", ".join(f"{k}={v}" for k, v in (t.get("args") or {}).items())
        out.append(f"| {i} | `{t['tool_name']}` | {_md_escape(args)} | "
                   f"{_md_escape(summarize_tool_result(t['tool_name'], t.get('result')))} | {t.get('duration_ms')} ms |")

    trigger_checks = (incident.get("trigger") or {}).get("checks") or []
    if trigger_checks:
        out += ["", "## Triggering health checks", "", "| Time (UTC) | Status | HTTP | Response time | Error |",
                "|---|---|---|---|---|"]
        for c in trigger_checks:
            out.append(f"| {c['timestamp']} | {c['status']} | {c.get('status_code') or '-'} | "
                       f"{c.get('response_time_ms')} ms | {_md_escape(c.get('error_type') or '-')} |")

    out += ["", "## Timeline", ""]
    out += [f"- `{e['timestamp']}` **{e['event_type']}** - {_md_escape(e.get('message') or '')}" for e in events]
    out += ["", "---", "_Generated automatically by the AI Website Uptime Monitoring & Incident Response Agent._"]
    return "\n".join(out)
