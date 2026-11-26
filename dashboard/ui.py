"""Plain-language labels and HTML building blocks for the dashboard (styled by style.css)."""

from __future__ import annotations

from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any

CSS_PATH = Path(__file__).with_name("style.css")

# ---------------------------------------------------------------- plain-language vocabulary
STATUS = {  # health -> (css class, label)
    "UP": ("ok", "Healthy"),
    "DEGRADED": ("slow", "Slow"),
    "DOWN": ("down", "Down"),
    "UNKNOWN": ("none", "Not checked yet"),
}
SEVERITY = {
    "SEV1": ("sev1", "SEV1 Critical", "Complete outage of an important service"),
    "SEV2": ("sev2", "SEV2 Major", "Service down or failing for many users"),
    "SEV3": ("sev3", "SEV3 Moderate", "Service works but is slow or partly failing"),
    "SEV4": ("sev4", "SEV4 Minor", "Small or short-lived issue"),
}
INCIDENT = {  # incident status -> (pill class, plain label)
    "OPEN": ("down", "Detected"),
    "INVESTIGATING": ("ai", "AI investigating"),
    "AWAITING_APPROVAL": ("slow", "Needs your approval"),
    "TICKET_CREATED": ("ai", "Ticket filed, waiting for fix"),
    "RESOLVED": ("ok", "Resolved"),
    "REJECTED": ("none", "Dismissed"),
}
TOOL_NAMES = {
    "http_probe": "Test the website's response",
    "check_redirects": "Follow redirects",
    "inspect_headers": "Inspect server headers",
    "resolve_dns": "Look up the domain name (DNS)",
    "tcp_port_check": "Check the server is accepting connections",
    "ssl_certificate_check": "Check the security certificate",
    "measure_latency_samples": "Measure speed over several requests",
    "get_recent_checks": "Review recent check history",
    "get_past_incidents": "Look for similar past incidents",
}
DEMO_MODES = {
    "healthy": "Healthy",
    "error_500": "Server error (HTTP 500)",
    "error_503": "Overloaded (HTTP 503)",
    "slow": "Slow responses",
    "timeout": "Not responding (timeout)",
    "intermittent": "Fails half the time",
    "not_found": "Page missing (HTTP 404)",
}
STEPS = ["Problem detected", "AI investigated", "Approval", "Ticket filed", "Resolved"]


def e(value: Any) -> str:
    return escape("" if value is None else str(value))


def html(*parts: str) -> str:
    """Join HTML fragments with no indentation (markdown would turn indented lines into code)."""
    return "".join(parts)


# ---------------------------------------------------------------- time helpers
def parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def ago(ts: str | None) -> str:
    dt = parse_ts(ts)
    if not dt:
        return "never"
    secs = int((datetime.now(UTC) - dt).total_seconds())
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60} min ago"
    if secs < 86400:
        return f"{secs // 3600} h ago"
    return f"{secs // 86400} d ago"


def clock(ts: str | None) -> str:
    dt = parse_ts(ts)
    return dt.strftime("%d %b, %H:%M UTC") if dt else "-"


def duration(start: str | None, end: str | None = None) -> str:
    a, b = parse_ts(start), parse_ts(end) or datetime.now(UTC)
    if not a:
        return "-"
    mins = max(0, int((b - a).total_seconds() // 60))
    if mins < 60:
        return f"{mins} min"
    return f"{mins // 60} h {mins % 60} min"


def ms(v: Any) -> str:
    try:
        return f"{float(v):,.0f} ms"
    except (TypeError, ValueError):
        return "-"


# ---------------------------------------------------------------- plain-language explanations
def explain_check(check: dict[str, Any] | None, threshold: int | None = None) -> str:
    """One sentence a non-engineer understands, describing the latest check."""
    if not check:
        return "Waiting for the first check."
    status, err, code = check.get("status"), check.get("error_type"), check.get("status_code")
    if status == "UP":
        return f"Responding normally in {ms(check.get('response_time_ms'))}."
    if status == "DEGRADED":
        limit = f" (limit {threshold:,} ms)" if threshold else ""
        return f"Working, but slow: {ms(check.get('response_time_ms'))}{limit}."
    return {
        "http_error": f"Returning errors instead of the page (HTTP {code}).",
        "timeout": "Not responding: the request timed out.",
        "connection_error": "Can't connect: the server may be offline.",
        "dns_error": "The domain name can't be found (DNS problem).",
        "ssl_error": "Security certificate problem: browsers will block it.",
    }.get(err or "", "Check failed.")


def status_pill(status: str | None) -> str:
    cls, label = STATUS.get(status or "UNKNOWN", STATUS["UNKNOWN"])
    return f'<span class="pill {cls}"><span class="dot {cls}"></span>{label}</span>'


def severity_pill(sev: str | None) -> str:
    if sev not in SEVERITY:
        return '<span class="pill none">Not rated yet</span>'
    cls, label, _ = SEVERITY[sev]
    return f'<span class="pill {cls}">{label}</span>'


def incident_pill(status: str | None) -> str:
    cls, label = INCIDENT.get(status or "", ("none", status or "-"))
    return f'<span class="pill {cls}">{e(label)}</span>'


# ---------------------------------------------------------------- composite blocks
def hero(stats: list[dict[str, Any]], open_incidents: int, awaiting: int, interval: int) -> str:
    checked = [s for s in stats if s["status"] != "UNKNOWN"]
    down = [s for s in checked if s["status"] == "DOWN"]
    slow = [s for s in checked if s["status"] == "DEGRADED"]
    n = len(stats)
    if not checked:
        tone, state, title = "none", "Waiting for data", "No checks have run yet"
    elif down:
        tone, state = "down", "Attention needed"
        title = f"{len(down)} of {n} services {'is' if len(down) == 1 else 'are'} down"
    elif slow:
        tone, state = "slow", "Running slowly"
        title = f"{len(slow)} of {n} services {'is' if len(slow) == 1 else 'are'} slow"
    else:
        tone, state, title = "ok", "All clear", f"All {n} services are healthy"
    uptimes = [s["uptime_pct"] for s in stats if s["uptime_pct"] is not None]
    lats = [s["avg_latency_ms"] for s in stats if s["avg_latency_ms"] is not None]
    last = max((s["last_checked"] for s in stats if s["last_checked"]), default=None)
    dot = {"ok": "ok", "slow": "slow", "down": "down"}.get(tone, "none")

    def stat(value: str, label: str) -> str:
        return f'<div class="hero-stat"><b>{value}</b><span>{label}</span></div>'

    return html(
        f'<div class="hero {tone}"><div>',
        f'<div class="hero-state"><span class="dot {dot}"></span>{state}</div>',
        f'<div class="hero-title">{title}</div>',
        f'<div class="hero-sub">Last check {ago(last)}, every {interval} seconds</div></div>',
        '<div class="hero-stats">',
        stat(f"{sum(uptimes) / len(uptimes):.2f}%" if uptimes else "-", "Uptime, last 24 h"),
        stat(ms(sum(lats) / len(lats)) if lats else "-", "Average response"),
        stat(str(open_incidents), "Open incidents"),
        stat(str(awaiting), "Waiting for approval"),
        "</div></div>",
    )


def site_card(site: Any, stat: dict[str, Any], history: list[dict[str, Any]], latest: dict[str, Any] | None,
              slots: int = 40) -> str:
    cls, _ = STATUS.get(stat["status"], STATUS["UNKNOWN"])
    hist = list(reversed(history[:slots]))  # oldest first
    bars = ['<i class="empty"></i>'] * (slots - len(hist))
    for c in hist:
        bcls = STATUS.get(c["status"], STATUS["UNKNOWN"])[0]
        tip = f"{clock(c['timestamp'])}: {STATUS.get(c['status'], ('', c['status']))[1]}, {ms(c['response_time_ms'])}"
        bars.append(f'<i class="{bcls}" title="{e(tip)}"></i>')
    uptime = f"{stat['uptime_pct']:.2f}%" if stat["uptime_pct"] is not None else "-"
    crit = '<span class="crit">Critical</span>' if site.critical else ""
    return html(
        f'<div class="site-card {cls}">',
        f'<div class="sc-head"><span class="sc-name" title="{e(site.name)}">{e(site.name)}</span>{status_pill(stat["status"])}</div>',
        f'<div class="sc-url" title="{e(site.url)}">{e(site.url)}</div>',
        f'<div class="sc-msg">{e(explain_check(latest, site.latency_threshold_ms))}</div>',
        f'<div class="bars" aria-label="Last {slots} checks">{"".join(bars)}</div>',
        f'<div class="bars-cap"><span>Older</span><span>Last {slots} checks</span><span>Now</span></div>',
        f'<div class="sc-foot"><span>Uptime <b>{uptime}</b></span><span>Avg <b>{ms(stat["avg_latency_ms"])}</b></span>',
        f'<span>{crit or ago(stat["last_checked"])}</span></div>',
        "</div>",
    )


def legend() -> str:
    items = [("ok", "Healthy"), ("slow", "Slow (above its speed limit)"), ("down", "Down (error or no response)"),
             ("none", "No data")]
    return '<div class="legend">' + "".join(f'<span><span class="dot {c}"></span>{t}</span>' for c, t in items) + "</div>"


def section(title: str, note: str = "") -> str:
    return f'<div class="section"><h3>{e(title)}</h3><p>{e(note)}</p></div>'


def incident_row(inc: dict[str, Any]) -> str:
    sev_cls = SEVERITY.get(inc.get("severity") or "", ("",))[0]
    closed = " closed" if inc["status"] in ("RESOLVED", "REJECTED") else ""
    return html(
        f'<div class="inc-row {sev_cls}{closed}"><span class="inc-id">#{inc["id"]}</span>',
        severity_pill(inc.get("severity")), incident_pill(inc["status"]),
        f'<div class="inc-text">{e((inc.get("summary") or "")[:120])}',
        f'<small>Opened {ago(inc["opened_at"])}</small></div></div>',
    )


def how_it_works() -> str:
    steps = [
        ("Check", "Every site is visited on a schedule and its speed and response are recorded."),
        ("Detect", "If a site fails several checks in a row, an incident is opened."),
        ("Investigate", "The AI chooses diagnostic tests (DNS, connection, certificate, speed) and reads the results."),
        ("Report", "It explains the most likely cause, rates severity and lists what to do."),
        ("Approve and verify", "You approve the GitHub ticket. After the fix, it re-checks and closes the incident."),
    ]
    return '<div class="flow">' + "".join(
        f"<div><b><em>{i}</em>{t}</b>{d}</div>" for i, (t, d) in enumerate(steps, 1)) + "</div>"


def tiles(items: list[tuple[str, str, str]]) -> str:
    return '<div class="tiles">' + "".join(
        f'<div class="tile"><span>{e(label)}</span><b>{value}</b><small>{e(note)}</small></div>'
        for label, value, note in items) + "</div>"


def case_header(inc: dict[str, Any], site_url: str | None) -> str:
    end = inc.get("resolved_at")
    when = f"Opened {clock(inc['opened_at'])}, " + (
        f"resolved after {duration(inc['opened_at'], end)}" if end else f"open for {duration(inc['opened_at'])}")
    return html(
        '<div class="case">',
        f'<div class="case-top">{severity_pill(inc.get("severity"))}{incident_pill(inc["status"])}</div>',
        f'<div class="case-title">Incident #{inc["id"]}: {e(inc["site"])}</div>',
        f'<div class="case-meta">{e(when)}{" | " + e(site_url) if site_url else ""}</div>',
        stepper(inc),
        "</div>",
    )


def stepper(inc: dict[str, Any]) -> str:
    status = inc["status"]
    reached = {"OPEN": 0, "INVESTIGATING": 1, "AWAITING_APPROVAL": 2, "TICKET_CREATED": 3, "RESOLVED": 5, "REJECTED": 2}
    idx = reached.get(status, 0)
    labels = list(STEPS)
    if status == "REJECTED":
        labels[2] = "Dismissed"
    out = ['<div class="stepper">']
    for i, label in enumerate(labels):
        if status == "REJECTED" and i == 2:
            cls = "step stopped"
        elif i < idx:
            cls = "step done"
        elif i == idx and status != "RESOLVED":
            cls = "step current"
        else:
            cls = "step"
        out.append(f'<div class="{cls}"><span class="n">{i + 1}</span>{label}</div>')
    out.append("</div>")
    return "".join(out)


def callout(kind: str, title: str, text: str) -> str:
    return f'<div class="callout {kind}"><div><b>{e(title)}</b><p>{e(text)}</p></div></div>'


def panels(inc: dict[str, Any], diag: dict[str, Any]) -> str:
    causes = diag.get("probable_causes") or []
    top = causes[0] if causes else None
    happened = e(diag.get("impact_summary") or inc.get("summary") or "")
    sev = SEVERITY.get(diag.get("severity") or "")
    sev_line = f"<p><b>{sev[1]}:</b> {e(sev[2])}.</p>" if sev else ""
    if top:
        conf = round(float(top.get("confidence", 0)) * 100)
        cause = html(f'<div class="cause">{e(top["cause"])}</div>',
                     f'<div class="meter"><i style="width:{conf}%"></i></div>',
                     f'<div class="meter-cap">AI confidence {conf}%</div>')
    else:
        cause = "<p>The AI has not finished its analysis yet.</p>"
    actions = diag.get("recommended_actions") or []
    todo = ("<ol>" + "".join(f"<li>{e(a)}</li>" for a in actions[:4]) + "</ol>") if actions else "<p>No actions yet.</p>"
    return html(
        '<div class="panels">',
        f'<div class="panel"><h4>What happened</h4><p>{happened}</p>{sev_line}</div>',
        f'<div class="panel ai"><h4>Most likely cause <span class="pill ai">AI analysis</span></h4>{cause}</div>',
        f'<div class="panel ai"><h4>What to do next</h4>{todo}</div>',
        "</div>",
    )


def tool_verdict(name: str, r: Any) -> str:
    """'problem', 'fine' or 'info': what the test *found*, not whether it ran."""
    if not isinstance(r, dict):
        return "info"
    if name in ("get_recent_checks", "get_past_incidents"):
        if name == "get_recent_checks" and r.get("up_ratio") is not None and r["up_ratio"] < 0.5:
            return "problem"
        return "info"
    if name in ("http_probe", "inspect_headers", "check_redirects"):
        code = r.get("status_code") or r.get("final_status")
        return "problem" if (code is None or code >= 400 or r.get("possible_loop")) else "fine"
    if name == "measure_latency_samples":
        return "problem" if r.get("success_rate", 1) < 1 or r.get("intermittent") else "fine"
    if name == "ssl_certificate_check":
        if not r.get("applicable", True):
            return "info"
        return "fine" if r.get("valid") and not r.get("expiring_soon") else "problem"
    if name == "tcp_port_check":
        return "fine" if r.get("open") else "problem"
    return "fine" if r.get("ok", True) else "problem"


def tool_row(run: dict[str, Any], finding: str) -> str:
    verdict = tool_verdict(run["tool_name"], run.get("result"))
    pill = {"problem": '<span class="pill down">Problem found</span>',
            "fine": '<span class="pill ok">Looks fine</span>',
            "info": '<span class="pill none">Info</span>'}[verdict]
    name = TOOL_NAMES.get(run["tool_name"], run["tool_name"])
    return html(
        f'<div class="tool">{pill}<div><div class="tool-name">{e(name)}</div>',
        f'<div class="tool-find">{e(finding)}</div></div>',
        f'<div class="tool-time">{run.get("duration_ms") or 0:,.0f} ms</div></div>',
    )


EVENT_TEXT = {
    "OPENED": ("bad", "Problem detected"), "INVESTIGATION_STARTED": ("ai", "AI started investigating"),
    "AGENT_DECISION": ("ai", "AI chose diagnostic tests"), "INVESTIGATION_COMPLETE": ("ai", "AI finished investigating"),
    "DIAGNOSED": ("ai", "Diagnosis ready"), "REPORT_GENERATED": ("ai", "Report written"),
    "AWAITING_APPROVAL": ("", "Waiting for approval"), "APPROVED": ("good", "Ticket approved"),
    "AUTO_APPROVED": ("good", "Ticket auto-approved"), "REJECTED": ("", "Ticket dismissed"),
    "TICKET_CREATED": ("good", "GitHub ticket filed"), "TICKET_FAILED": ("bad", "GitHub ticket failed"),
    "STILL_FAILING": ("bad", "Recovery check: still failing"), "RESOLVED": ("good", "Recovery verified, incident resolved"),
    "AGENT_ERROR": ("bad", "Agent error"), "GITHUB_UPDATE_FAILED": ("bad", "GitHub update failed"),
}


def timeline(events: list[dict[str, Any]]) -> str:
    out = ['<div class="tl">']
    for ev in events:
        cls, title = EVENT_TEXT.get(ev["event_type"], ("", ev["event_type"].replace("_", " ").capitalize()))
        out.append(html(f'<div class="tl-item {cls}"><div class="tl-time">{clock(ev["timestamp"])}</div>',
                        f'<div class="tl-text"><b>{e(title)}</b>. {e(ev.get("message") or "")}</div></div>'))
    out.append("</div>")
    return "".join(out)
