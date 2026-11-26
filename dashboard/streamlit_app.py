"""Streamlit dashboard for the AI Website Uptime Monitoring & Incident Response Agent.

Run:  python -m streamlit run dashboard/streamlit_app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json  # noqa: E402

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from app import db, services  # noqa: E402
from app.config import get_settings, load_sites, setup_logging  # noqa: E402

st.set_page_config(page_title="AI Uptime Incident Agent", layout="wide")
setup_logging()

# Reference palette (dataviz skill): one series hue + reserved status colours, always paired with a text label.
SERIES = "#257de0"
MUTED = "#8a8a8a"
STATUS_COLORS = {"UP": "#0aac23", "DEGRADED": "#fbb315", "DOWN": "#dc3c3d", "UNKNOWN": MUTED}
# Colour tones for Streamlit badges (meaning is always carried by the text label too)
STATUS_TONE = {"UP": "green", "DEGRADED": "orange", "DOWN": "red", "UNKNOWN": "gray"}
SEV_TONE = {"SEV1": "red", "SEV2": "orange", "SEV3": "blue", "SEV4": "green"}
INCIDENT_TONE = {"OPEN": "red", "INVESTIGATING": "blue", "AWAITING_APPROVAL": "orange", "TICKET_CREATED": "violet",
                 "RESOLVED": "green", "REJECTED": "gray"}
# Text colours for table cells
CELL_COLORS = {"UP": "#0aac23", "DEGRADED": "#c98a00", "DOWN": "#dc3c3d", "SEV1": "#dc3c3d", "SEV2": "#e0702b",
               "SEV3": "#257de0", "SEV4": "#0aac23", "OPEN": "#dc3c3d", "INVESTIGATING": "#257de0",
               "AWAITING_APPROVAL": "#c98a00", "TICKET_CREATED": "#6a4ce2", "RESOLVED": "#0aac23", "REJECTED": MUTED}

PAGES = ["Overview", "Site detail", "Incidents"]


@st.cache_resource
def _bootstrap() -> bool:
    services.bootstrap()
    return True


_bootstrap()
settings = get_settings()


def badge(status: str | None) -> str:
    """Plain-text status (used where markdown is not rendered, e.g. metrics)."""
    return status or "UNKNOWN"


def md_badge(text: str | None, tones: dict[str, str]) -> str:
    """Coloured Streamlit markdown badge, e.g. :green-badge[UP]."""
    text = text or "UNKNOWN"
    return f":{tones.get(text, 'gray')}-badge[{text.replace('_', ' ')}]"


def color_cells(value) -> str:
    color = CELL_COLORS.get(str(value).strip())
    return f"color: {color}; font-weight: 600" if color else ""


def fmt_ms(v) -> str:
    return "-" if v is None or pd.isna(v) else f"{v:,.0f} ms"


def go_to_incident(incident_id: int) -> None:
    """Request navigation; applied at the top of the next run, before widgets are created."""
    st.session_state["_goto_incident"] = incident_id


# ====================================================================== sidebar
def sidebar() -> str:
    with st.sidebar:
        st.title("Uptime Agent")
        st.caption("AI website monitoring & incident response")
        if "_goto_incident" in st.session_state:
            st.session_state["page"] = "Incidents"
            st.session_state["selected_incident"] = st.session_state.pop("_goto_incident")
        page = st.radio("Navigate", PAGES, key="page")
        st.divider()
        st.markdown("**Agent configuration**")
        st.markdown(f"- LLM: `{'mock (offline)' if settings.use_mock_llm else settings.openai_model}`")
        st.markdown(f"- GitHub: `{settings.github_repo if settings.github_enabled else 'dry-run'}`")
        st.markdown(f"- Human approval: `{'required' if settings.require_human_approval else 'off'}`")
        st.toggle("Auto-refresh (15 s)", key="auto_refresh", value=True)
        demo_control()
    return page


def demo_control() -> None:
    """Sidebar panel that drives the flaky demo server so incidents can be shown live."""
    st.divider()
    st.markdown("**Demo control**")
    current = services.demo_mode()
    if current is None:
        st.caption(f"Demo server not reachable at `{settings.demo_server_url}`. Start it with `make demo-server`.")
        return
    modes = list(current["available_modes"])
    st.caption(f"Orders API mode: **{current['mode']}**")
    mode = st.selectbox("Failure mode", modes, index=modes.index("error_500") if "error_500" in modes else 0,
                        help="\n".join(f"- {k}: {v}" for k, v in current["available_modes"].items()))
    st.caption(current["available_modes"][mode])
    c1, c2 = st.columns(2)
    if c1.button("Simulate incident", type="primary", key="simulate", disabled=mode == "healthy"):
        with st.spinner(f"Switching to '{mode}', running checks, agent investigating..."):
            try:
                incident_id = services.simulate_incident(mode)
            except Exception as exc:  # noqa: BLE001
                st.error(f"Simulation failed: {exc}")
                return
        if incident_id:
            go_to_incident(incident_id)
            st.rerun()
        else:
            st.warning("No incident opened (failure threshold not reached yet) - try Run checks now.")
    if c2.button("Fix (healthy)", key="heal"):
        services.set_demo_mode("healthy")
        st.toast("Demo server is healthy again - click Verify recovery on the incident.")
        st.rerun()
    st.caption("Tip: stop the demo server process to simulate a full outage (connection refused).")


# ====================================================================== overview
def run_checks_button(key: str) -> None:
    if st.button("Run checks now", key=key, type="primary"):
        with st.spinner("Checking all sites (the agent investigates any new incident)..."):
            new = services.run_checks_now()
        if new:
            st.warning(f"New incident(s) opened: {', '.join(f'#{i}' for i in new)}")
        else:
            st.success("Checks complete")


def overview_page() -> None:
    st.header("Overview")
    stats = pd.DataFrame(services.site_stats(24))
    incidents = db.list_incidents(limit=500)
    open_incidents = [i for i in incidents if i["status"] in db.OPEN_STATUSES]

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Sites monitored", len(stats))
    c2.metric("Currently down", int((stats["status"] == "DOWN").sum()) if not stats.empty else 0)
    c3.metric("Degraded", int((stats["status"] == "DEGRADED").sum()) if not stats.empty else 0)
    c4.metric("Open incidents", len(open_incidents))
    uptime = stats["uptime_pct"].dropna()
    c5.metric("Avg uptime (24h)", f"{uptime.mean():.2f}%" if not uptime.empty else "-")

    left, right = st.columns([3, 1])
    with right:
        run_checks_button("run_overview")
    with left:
        st.subheader("Site status")
    if stats.empty or stats["last_checked"].isna().all():
        st.info("No checks yet. Click **Run checks now**, start the monitor (`make monitor`) or seed demo data (`make seed`).")
    table = pd.DataFrame({
        "Status": stats["status"].map(badge),
        "Site": stats["site"],
        "URL": stats["url"],
        "Critical": stats["critical"].map(lambda c: "yes" if c else ""),
        "HTTP": stats["last_status_code"].map(lambda v: "-" if pd.isna(v) else str(int(v))),
        "Response": stats["last_response_ms"].map(fmt_ms),
        "Uptime 24h": stats["uptime_pct"].map(lambda v: "-" if pd.isna(v) else f"{v:.2f}%"),
        "Avg latency": stats["avg_latency_ms"].map(fmt_ms),
        "Open incident": stats["open_incident"].map(lambda v: "-" if pd.isna(v) else f"#{int(v)}"),
        "Last checked (UTC)": stats["last_checked"].fillna("-"),
    })
    st.dataframe(table.style.map(color_cells, subset=["Status"]), hide_index=True, width="stretch")

    st.subheader("Latest incidents")
    if not incidents:
        st.caption("No incidents yet.")
    for inc in incidents[:5]:
        cols = st.columns([1, 2, 5, 2, 1])
        cols[0].markdown(f"**#{inc['id']}**")
        cols[1].markdown(md_badge(inc["status"], INCIDENT_TONE))
        cols[2].markdown((md_badge(inc["severity"], SEV_TONE) + " " if inc["severity"] else "") + inc["summary"][:90])
        cols[3].caption(inc["opened_at"])
        cols[4].button("Open", key=f"open_{inc['id']}", on_click=go_to_incident, args=(inc["id"],))


# ====================================================================== site detail
def site_detail_page() -> None:
    st.header("Site detail")
    sites = load_sites()
    names = [s.name for s in sites]
    col_a, col_b = st.columns([3, 1])
    name = col_a.selectbox("Site", names, key="site_detail")
    hours = col_b.selectbox("Window", [1, 6, 24, 72], index=2, format_func=lambda h: f"Last {h} h")
    site = next(s for s in sites if s.name == name)
    rows = pd.DataFrame(db.checks_since(hours, site=name))
    st.caption(f"{site.url} - threshold {site.latency_threshold_ms} ms - expected HTTP {site.expected_status}"
               + (" - **critical service**" if site.critical else ""))
    if rows.empty:
        st.info("No checks in this window yet.")
        return

    rows["ts"] = pd.to_datetime(rows["timestamp"])
    up = rows[rows["status"] != "DOWN"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Current status", badge(rows.iloc[-1]["status"]))
    c2.metric("Uptime", f"{100 * len(up) / len(rows):.2f}%")
    c3.metric("Avg latency", fmt_ms(up["response_time_ms"].mean() if not up.empty else None))
    c4.metric("p95 latency", fmt_ms(up["response_time_ms"].quantile(0.95) if not up.empty else None))

    st.subheader("Response time")
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=rows["ts"], y=rows["response_time_ms"], mode="lines", name="Response time",
        line={"color": SERIES, "width": 2},
        hovertemplate="%{x|%H:%M:%S}<br>%{y:,.0f} ms<extra></extra>",
    ))
    failed = rows[rows["status"] != "UP"]
    for status in ("DEGRADED", "DOWN"):
        pts = failed[failed["status"] == status]
        if not pts.empty:
            fig.add_trace(go.Scatter(
                x=pts["ts"], y=pts["response_time_ms"], mode="markers", name=status,
                marker={"color": STATUS_COLORS[status], "size": 9, "line": {"color": "white", "width": 2}},
                customdata=pts[["status_code", "error_type"]].fillna("-"),
                hovertemplate="%{x|%H:%M:%S}<br>%{y:,.0f} ms<br>" + status + " %{customdata[0]} %{customdata[1]}<extra></extra>",
            ))
    fig.add_hline(y=site.latency_threshold_ms, line={"color": MUTED, "dash": "dash", "width": 1},
                  annotation_text=f"threshold {site.latency_threshold_ms} ms", annotation_position="top left")
    fig.update_layout(height=340, margin={"l": 10, "r": 10, "t": 10, "b": 10}, hovermode="x unified",
                      yaxis_title="ms", legend={"orientation": "h", "y": 1.12},
                      xaxis={"showgrid": False}, yaxis={"gridcolor": "rgba(128,128,128,0.15)"})
    st.plotly_chart(fig, width="stretch")

    st.subheader("Status timeline")
    tl = go.Figure()
    for status in ("UP", "DEGRADED", "DOWN"):
        pts = rows[rows["status"] == status]
        if not pts.empty:
            tl.add_trace(go.Scatter(
                x=pts["ts"], y=[status] * len(pts), mode="markers", name=status,
                marker={"color": STATUS_COLORS[status], "size": 10, "symbol": "square"},
                hovertemplate="%{x|%H:%M:%S} " + status + "<extra></extra>",
            ))
    tl.update_layout(height=170, margin={"l": 10, "r": 10, "t": 10, "b": 10}, showlegend=False,
                     yaxis={"categoryorder": "array", "categoryarray": ["DOWN", "DEGRADED", "UP"]},
                     xaxis={"showgrid": False})
    st.plotly_chart(tl, width="stretch")

    with st.expander("Recent checks (table view)"):
        view = rows.sort_values("id", ascending=False)[
            ["timestamp", "status", "status_code", "response_time_ms", "error_type", "error_message"]].head(100)
        st.dataframe(view, hide_index=True, width="stretch")


# ====================================================================== incidents
def incidents_page() -> None:
    from app.agent import runner
    from app.reporting.report import summarize_tool_result

    st.header("Incidents")
    all_incidents = db.list_incidents(limit=500)
    if not all_incidents:
        st.info("No incidents yet. Use **Demo control > Simulate incident** in the sidebar to trigger one.")
        return

    f1, f2, f3 = st.columns(3)
    status_f = f1.multiselect("Status", list(INCIDENT_TONE), placeholder="All statuses")
    site_f = f2.multiselect("Site", sorted({i["site"] for i in all_incidents}), placeholder="All sites")
    sev_f = f3.multiselect("Severity", list(SEV_TONE), placeholder="All severities")
    incidents = [i for i in all_incidents
                 if (not status_f or i["status"] in status_f) and (not site_f or i["site"] in site_f)
                 and (not sev_f or i["severity"] in sev_f)]
    incident_table = pd.DataFrame([{
        "ID": f"#{i['id']}", "Status": i["status"],
        "Severity": i["severity"] or "-", "Site": i["site"],
        "Summary": i["summary"], "Opened (UTC)": i["opened_at"], "Resolved (UTC)": i["resolved_at"] or "-",
        "GitHub": i["github_issue_url"] or ("dry-run" if i["github_payload_json"] else "-"),
    } for i in incidents])
    if incidents:
        st.dataframe(incident_table.style.map(color_cells, subset=["Status", "Severity"]), hide_index=True,
                     width="stretch", height=min(38 * (len(incidents) + 1), 300))
    else:
        st.caption("No incidents match the selected filters.")

    ids = [i["id"] for i in incidents] or [all_incidents[0]["id"]]
    if st.session_state.get("selected_incident") not in ids:
        st.session_state["selected_incident"] = ids[0]
    labels = {i["id"]: f"#{i['id']} - {i['site']} - {i['status']}" for i in all_incidents}
    incident_id = st.selectbox("Incident", ids, format_func=lambda x: labels[x], key="selected_incident")
    inc = db.get_incident(incident_id)
    pending = runner.pending_action(incident_id)
    diag = inc.get("diagnosis") or {}

    st.divider()
    head, actions = st.columns([3, 2])
    with head:
        st.subheader(f"#{inc['id']} - {inc['site']}")
        st.markdown(f"{md_badge(inc['severity'] or 'Unclassified', SEV_TONE)} "
                    f"{md_badge(inc['status'], INCIDENT_TONE)} opened {inc['opened_at']} UTC"
                    + (f", resolved {inc['resolved_at']} UTC" if inc["resolved_at"] else ""))
        st.write(inc["summary"])
        if diag.get("impact_summary"):
            st.markdown(f"**Impact:** {diag['impact_summary']}")
        if inc.get("llm_mode"):
            st.caption(f"Analysed by: {inc['llm_mode']}")

    with actions:
        st.markdown("**Actions**")
        if pending == "approval":
            st.warning("The agent is waiting for approval to create a GitHub issue.")
            b1, b2 = st.columns(2)
            if b1.button("Approve & create ticket", type="primary", key="approve"):
                with st.spinner("Creating GitHub issue..."):
                    runner.submit_approval(incident_id, True, reviewer="dashboard")
                st.rerun()
            if b2.button("Reject", key="reject"):
                runner.submit_approval(incident_id, False, reviewer="dashboard")
                st.rerun()
        if inc["github_issue_url"]:
            st.link_button(f"Open GitHub issue #{inc['github_issue_number']}", inc["github_issue_url"])
        elif inc["github_payload_json"]:
            st.info("GitHub dry-run: issue payload recorded (see the GitHub tab). Set GITHUB_TOKEN to create real issues.")
        if inc["status"] not in ("RESOLVED", "REJECTED"):
            if st.button("Verify recovery", key="verify"):
                with st.spinner("Re-checking the site..."):
                    result = runner.verify_recovery(incident_id)
                (st.success if result["recovered"] else st.error)(result["message"])
                if inc["status"] == "TICKET_CREATED":
                    st.rerun()
        if inc["status"] in ("OPEN",) and pending is None:
            if st.button("Run AI investigation", key="investigate"):
                with st.spinner("Agent investigating..."):
                    services.handle_new_incident(incident_id)
                st.rerun()
        if inc["status"] == "RESOLVED":
            st.success("Recovery verified - incident resolved.")

    trace_tab, rca_tab, report_tab, gh_tab, timeline_tab = st.tabs(
        ["Agent investigation trace", "Root cause & actions", "Incident report", "GitHub", "Timeline"])

    with trace_tab:
        runs = db.list_tool_runs(incident_id)
        decisions = [e for e in db.list_events(incident_id) if e["event_type"] in ("AGENT_DECISION", "INVESTIGATION_COMPLETE")]
        if not runs:
            st.caption("The agent has not run any tools yet.")
        for turn in sorted({r["iteration"] for r in runs}):
            reasoning = next((d for d in decisions if d["message"].startswith(f"Turn {turn}:")), None)
            st.markdown(f"**Turn {turn}** - {reasoning['message'].split(': ', 1)[1] if reasoning else ''}")
            if reasoning and reasoning.get("data_json"):
                why = json.loads(reasoning["data_json"]).get("reasoning")
                if why:
                    st.caption(f"Reasoning: {why}")
            for r in [r for r in runs if r["iteration"] == turn]:
                ok = (r["result"] or {}).get("ok", True) if isinstance(r["result"], dict) else True
                with st.expander(f"{':green[OK]' if ok else ':orange[Issue]'} `{r['tool_name']}` - "
                                 f"{summarize_tool_result(r['tool_name'], r['result'])}  ({r['duration_ms']:.0f} ms)"):
                    st.markdown("**Arguments**")
                    st.json(r["args"])
                    st.markdown("**Result**")
                    st.json(r["result"])
        done = next((d for d in decisions if d["event_type"] == "INVESTIGATION_COMPLETE"), None)
        if done and done.get("data_json"):
            st.success(f"Findings: {json.loads(done['data_json']).get('findings', '')}")

    with rca_tab:
        if not diag:
            st.caption("No diagnosis yet.")
        else:
            st.markdown(f"**Severity:** {md_badge(diag['severity'], SEV_TONE)} - {diag.get('severity_reason', '')}")
            st.markdown(f"**Overall confidence:** {round(diag.get('confidence', 0) * 100)}%")
            st.markdown("#### Probable causes")
            for i, c in enumerate(diag.get("probable_causes", []), 1):
                st.markdown(f"**{i}. {c['cause']}**")
                st.progress(float(c["confidence"]), text=f"confidence {round(c['confidence'] * 100)}%")
                for ev in c.get("evidence", []):
                    st.markdown(f"- {ev}")
            st.markdown("#### Recommended actions")
            st.markdown("\n".join(f"{i}. {a}" for i, a in enumerate(diag.get("recommended_actions", []), 1)))

    with report_tab:
        if inc["report_md"]:
            st.download_button("Download report (.md)", inc["report_md"], file_name=f"incident-{incident_id}.md")
            with st.container(border=True):
                st.markdown(inc["report_md"])
        else:
            st.caption("Report not generated yet.")

    with gh_tab:
        payload = inc.get("github_payload")
        if inc["github_issue_url"]:
            st.markdown(f"Issue: [{inc['github_issue_url']}]({inc['github_issue_url']})")
        if payload:
            st.markdown(f"**Title:** {payload['title']}")
            st.markdown("**Labels:** " + " ".join(f"`{lbl}`" for lbl in payload["labels"]))
            with st.expander("Issue body"):
                st.code(payload["body"], language="markdown")
        else:
            st.caption("No GitHub issue yet" + (" - awaiting approval." if pending == "approval" else "."))

    with timeline_tab:
        for e in db.list_events(incident_id):
            st.markdown(f"`{e['timestamp']}` **{e['event_type']}** - {e['message'] or ''}")


# ====================================================================== main
def main() -> None:
    page = sidebar()
    auto = st.session_state.get("auto_refresh", True)
    if page == "Overview":
        st.fragment(run_every=15 if auto else None)(overview_page)()
    elif page == "Site detail":
        st.fragment(run_every=15 if auto else None)(site_detail_page)()
    else:
        incidents_page()


main()
