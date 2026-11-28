"""Streamlit dashboard for the AI Website Uptime Monitoring & Incident Response Agent.

Run:  python -m streamlit run dashboard/streamlit_app.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from app import db, services  # noqa: E402
from app.config import get_settings, load_sites, setup_logging  # noqa: E402
from dashboard import ui  # noqa: E402

st.set_page_config(page_title="AI Uptime Incident Agent", layout="wide")
setup_logging()
st.markdown(f"<style>{ui.CSS_PATH.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)

PAGES = ["Overview", "Site detail", "Incidents"]
PAGE_LABELS = {"Overview": "Overview", "Site detail": "Site history", "Incidents": "Incidents"}
COLORS = {"line": "#0B7A88", "fill": "rgba(24,183,199,0.12)", "UP": "#16A34A", "DEGRADED": "#D99A06",
          "DOWN": "#E0464B", "muted": "#7C879E", "grid": "rgba(20,33,61,0.07)", "ink": "#14213D"}


@st.cache_resource
def _bootstrap() -> bool:
    services.bootstrap()
    return True


_bootstrap()
settings = get_settings()


def show(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def go_to_incident(incident_id: int) -> None:
    """Request navigation; applied at the top of the next run, before widgets are created."""
    st.session_state["_goto_incident"] = incident_id


def flash(kind: str, message: str) -> None:
    st.session_state["_flash"] = (kind, message)


def show_flash() -> None:
    if "_flash" in st.session_state:
        kind, message = st.session_state.pop("_flash")
        getattr(st, kind)(message)


def themed(fig: go.Figure, height: int) -> go.Figure:
    fig.update_layout(
        height=height, margin={"l": 8, "r": 8, "t": 8, "b": 8}, paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#FFFFFF", font={"family": "Manrope, system-ui, sans-serif", "color": COLORS["ink"], "size": 12},
        hoverlabel={"bgcolor": "#FFFFFF", "font_size": 12, "bordercolor": "#DCE3EE"},
        legend={"orientation": "h", "y": 1.12, "x": 0},
    )
    fig.update_xaxes(showgrid=False, linecolor="#DCE3EE")
    fig.update_yaxes(gridcolor=COLORS["grid"], zeroline=False)
    return fig


# ====================================================================== sidebar
def sidebar() -> str:
    with st.sidebar:
        show('<div class="brand"><div class="brand-name">Uptime Agent</div>'
             '<div class="brand-tag">Watches your websites and investigates problems with AI</div></div>')
        if "_goto_incident" in st.session_state:
            st.session_state["page"] = "Incidents"
            st.session_state["selected_incident"] = st.session_state.pop("_goto_incident")
        page = st.radio("Go to", PAGES, key="page", format_func=PAGE_LABELS.get)

        ai = settings.llm_label
        gh = settings.github_repo if settings.github_enabled else "Simulated (no token)"
        approval = "Required" if settings.require_human_approval else "Automatic"
        show('<div class="side-title">Current setup</div><dl class="cfg">'
             f"<dt>AI model</dt><dd>{ui.e(ai)}</dd><dt>GitHub tickets</dt><dd>{ui.e(gh)}</dd>"
             f"<dt>Approval</dt><dd>{approval}</dd></dl>")
        st.toggle("Refresh automatically", key="auto_refresh", value=True, help="Reload the data every 15 seconds")
        st.divider()
        demo_control()
    return page


def demo_control() -> None:
    """Drives the flaky demo server so incidents can be shown live."""
    show('<div class="side-title">Demo control</div>'
         '<div class="side-help">Make the demo shop\'s Orders API fail on purpose and watch the AI respond.</div>')
    current = services.demo_mode()
    if current is None:
        st.caption(f"The demo website isn't running at {settings.demo_server_url}. "
                   "Start it with: python -m uvicorn demo.flaky_server:app --port 8001")
        return
    modes = list(current["available_modes"])
    show(f'<div class="side-help">Right now: <b>{ui.e(ui.DEMO_MODES.get(current["mode"], current["mode"]))}</b></div>')
    mode = st.selectbox("Problem to simulate", [m for m in modes if m != "healthy"],
                        format_func=lambda m: ui.DEMO_MODES.get(m, m), key="demo_mode_choice")
    if st.button("Simulate incident", type="primary", key="simulate", width="stretch"):
        with st.spinner("Breaking the demo site and letting the AI investigate..."):
            try:
                incident_id = services.simulate_incident(mode)
            except Exception as exc:  # noqa: BLE001
                st.error(f"Simulation failed: {exc}")
                return
        if incident_id:
            go_to_incident(incident_id)
            st.rerun()
        else:
            st.warning("No incident opened yet. The site must fail several checks in a row; click Run checks now.")
    if st.button("Fix the demo site", key="heal", width="stretch",
                 help="Make the demo site healthy again, then verify recovery on the incident"):
        services.set_demo_mode("healthy")
        flash("success", "The demo site is fixed. Open the incident and click Verify recovery.")
        st.rerun()


# ====================================================================== overview
def overview_page() -> None:
    show_flash()
    stats = services.site_stats(24)
    incidents = db.list_incidents(limit=500)
    open_incidents = [i for i in incidents if i["status"] in db.OPEN_STATUSES]
    awaiting = sum(1 for i in incidents if i["status"] == "AWAITING_APPROVAL")
    show(ui.hero(stats, len(open_incidents), awaiting, settings.check_interval_seconds))

    head, btn = st.columns([5, 1], vertical_alignment="bottom")
    with head:
        show(ui.section("Services", "Each bar is one check, newest on the right. Hover a bar for details."))
    with btn:
        if st.button("Run checks now", key="run_overview", type="primary", width="stretch"):
            with st.spinner("Checking every site..."):
                new = services.run_checks_now()
            flash("warning" if new else "success",
                  f"New incident opened: {', '.join(f'#{i}' for i in new)}" if new else "All sites checked.")
            st.rerun()

    sites = {s.name: s for s in load_sites()}
    latest = {c["site"]: c for c in db.latest_check_per_site()}
    cards = [ui.site_card(sites[s["site"]], s, db.recent_checks(s["site"], 40), latest.get(s["site"]))
             for s in stats if s["site"] in sites]
    show('<div class="site-grid">' + "".join(cards) + "</div>" + ui.legend())

    show(ui.section("Incidents", "Problems the AI has investigated, newest first"))
    if not incidents:
        show('<div class="empty-note">No incidents so far. To see the AI at work, choose a problem under '
             '<b>Demo control</b> in the sidebar and click <b>Simulate incident</b>.</div>')
    for inc in incidents[:6]:
        row, act = st.columns([7, 1], vertical_alignment="center")
        with row:
            show(ui.incident_row(inc))
        act.button("Open", key=f"open_{inc['id']}", on_click=go_to_incident, args=(inc["id"],), width="stretch")

    with st.expander("How this works"):
        show(ui.how_it_works())


# ====================================================================== site detail
def site_detail_page() -> None:
    sites = load_sites()
    show(ui.section("Site history", "Speed and availability of one site over time"))
    col_a, col_b = st.columns([3, 1])
    name = col_a.selectbox("Site", [s.name for s in sites], key="site_detail")
    hours = col_b.selectbox("Time range", [1, 6, 24, 72], index=2, format_func=lambda h: f"Last {h} hours")
    site = next(s for s in sites if s.name == name)
    rows = pd.DataFrame(db.checks_since(hours, site=name))
    if rows.empty:
        show('<div class="empty-note">No checks in this time range yet. Click <b>Run checks now</b> on the '
             'Overview page, or start the monitor.</div>')
        return

    rows["ts"] = pd.to_datetime(rows["timestamp"])
    ok = rows[rows["status"] != "DOWN"]
    latest = rows.iloc[-1].to_dict()
    uptime = 100 * len(ok) / len(rows)
    avg = ok["response_time_ms"].mean() if not ok.empty else None
    p95 = ok["response_time_ms"].quantile(0.95) if not ok.empty else None
    failures = int((rows["status"] != "UP").sum())

    show(ui.tiles([
        ("Status now", ui.status_pill(latest["status"]), ui.explain_check(latest, site.latency_threshold_ms)),
        ("Uptime", f"{uptime:.2f}%", f"{len(rows)} checks in range"),
        ("Average response", ui.ms(avg), f"Speed limit {site.latency_threshold_ms:,} ms"),
        ("Slowest 5% of checks", ui.ms(p95), "95th percentile"),
        ("Problems", str(failures), "Checks that were slow or down"),
    ]))
    verdict = ("well within" if avg is not None and avg < 0.5 * site.latency_threshold_ms
               else "within" if avg is not None and avg <= site.latency_threshold_ms else "above")
    show(f'<div class="summary">Over the last {hours} hours, <b>{ui.e(name)}</b> was available '
         f"<b>{uptime:.2f}%</b> of the time. On average it answered in <b>{ui.ms(avg)}</b>, {verdict} its "
         f"speed limit of {site.latency_threshold_ms:,} ms"
         + (f", and had <b>{failures}</b> problem check{'s' if failures != 1 else ''}." if failures else
            ", with no problems.") + "</div>")

    show(ui.section("Response time", "Lower is better. The dashed line is the speed limit for this site."))
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=rows["ts"], y=rows["response_time_ms"], mode="lines", name="Response time",
                             line={"color": COLORS["line"], "width": 2}, fill="tozeroy", fillcolor=COLORS["fill"],
                             hovertemplate="%{x|%d %b %H:%M}<br>%{y:,.0f} ms<extra></extra>"))
    for status, label in (("DEGRADED", "Slow"), ("DOWN", "Down")):
        pts = rows[rows["status"] == status]
        if not pts.empty:
            fig.add_trace(go.Scatter(
                x=pts["ts"], y=pts["response_time_ms"], mode="markers", name=label,
                marker={"color": COLORS[status], "size": 10, "line": {"color": "#FFFFFF", "width": 2}},
                hovertemplate="%{x|%d %b %H:%M}<br>" + label + ", %{y:,.0f} ms<extra></extra>"))
    fig.add_hline(y=site.latency_threshold_ms, line={"color": COLORS["muted"], "dash": "dash", "width": 1},
                  annotation_text=f"speed limit {site.latency_threshold_ms:,} ms", annotation_position="top left")
    fig.update_yaxes(title_text="milliseconds")
    st.plotly_chart(themed(fig, 340), width="stretch")

    show(ui.section("Availability timeline", "Each square is one check"))
    tl = go.Figure()
    for status, label in (("UP", "Healthy"), ("DEGRADED", "Slow"), ("DOWN", "Down")):
        pts = rows[rows["status"] == status]
        if not pts.empty:
            tl.add_trace(go.Scatter(x=pts["ts"], y=[label] * len(pts), mode="markers", name=label,
                                    marker={"color": COLORS[status], "size": 11, "symbol": "square"},
                                    hovertemplate="%{x|%d %b %H:%M} " + label + "<extra></extra>"))
    tl.update_layout(showlegend=False)
    tl.update_yaxes(categoryorder="array", categoryarray=["Down", "Slow", "Healthy"])
    st.plotly_chart(themed(tl, 180), width="stretch")

    with st.expander("See every check as a table"):
        view = rows.sort_values("id", ascending=False).head(200)
        st.dataframe(pd.DataFrame({
            "Time": view["timestamp"].map(ui.clock),
            "Result": view["status"].map(lambda s: ui.STATUS.get(s, ("", s))[1]),
            "HTTP code": view["status_code"].map(lambda v: "-" if pd.isna(v) else str(int(v))),
            "Response": view["response_time_ms"].map(ui.ms),
            "What happened": [ui.explain_check(r, site.latency_threshold_ms) for r in view.to_dict("records")],
        }), hide_index=True, width="stretch")


# ====================================================================== incidents
def incidents_page() -> None:
    from app.agent import runner
    from app.reporting.report import summarize_tool_result

    show_flash()
    all_incidents = db.list_incidents(limit=500)
    show(ui.section("Incidents", "Every problem the AI detected, what it found, and what to do"))
    if not all_incidents:
        show('<div class="empty-note">No incidents yet. Choose a problem under <b>Demo control</b> in the sidebar '
             'and click <b>Simulate incident</b> to see the AI investigate.</div>')
        return

    ids = [i["id"] for i in all_incidents]
    if st.session_state.get("selected_incident") not in ids:
        st.session_state["selected_incident"] = ids[0]
    labels = {i["id"]: f"#{i['id']}  {i['site']}  ({ui.INCIDENT.get(i['status'], ('', i['status']))[1]})"
              for i in all_incidents}
    incident_id = st.selectbox("Choose an incident", ids, format_func=lambda x: labels[x], key="selected_incident")

    with st.expander(f"Browse all incidents ({len(all_incidents)})"):
        f1, f2 = st.columns(2)
        status_f = f1.multiselect("Status", list(ui.INCIDENT), format_func=lambda s: ui.INCIDENT[s][1],
                                  placeholder="Any status")
        sev_f = f2.multiselect("Severity", list(ui.SEVERITY), format_func=lambda s: ui.SEVERITY[s][1],
                               placeholder="Any severity")
        shown = [i for i in all_incidents
                 if (not status_f or i["status"] in status_f) and (not sev_f or i["severity"] in sev_f)]
        if shown:
            st.dataframe(pd.DataFrame([{
                "Incident": f"#{i['id']}", "Site": i["site"],
                "Severity": ui.SEVERITY.get(i["severity"] or "", ("", "Not rated"))[1],
                "Status": ui.INCIDENT.get(i["status"], ("", i["status"]))[1],
                "Opened": ui.clock(i["opened_at"]), "Duration": ui.duration(i["opened_at"], i["resolved_at"]),
            } for i in shown]), hide_index=True, width="stretch")
        else:
            st.caption("No incidents match these filters.")

    inc = db.get_incident(incident_id)
    pending = runner.pending_action(incident_id)
    diag = inc.get("diagnosis") or {}
    site_url = ((inc.get("trigger") or {}).get("site") or {}).get("url")
    show(ui.case_header(inc, site_url))
    action_bar(inc, pending, incident_id)
    show(ui.panels(inc, diag))

    t1, t2, t3, t4, t5 = st.tabs(["How the AI investigated", "All possible causes", "Full report",
                                  "GitHub ticket", "Timeline"])
    with t1:
        runs = db.list_tool_runs(incident_id)
        events = db.list_events(incident_id)
        decisions = [ev for ev in events if ev["event_type"] == "AGENT_DECISION"]
        if not runs:
            st.caption("The AI hasn't run any tests yet.")
        else:
            st.caption("The AI decided which tests to run in each round, based on what the previous round found.")
        for turn in sorted({r["iteration"] for r in runs}):
            turn_runs = [r for r in runs if r["iteration"] == turn]
            why = next((json.loads(d["data_json"]).get("reasoning", "") for d in decisions
                        if d["message"].startswith(f"Turn {turn}:") and d.get("data_json")), "")
            show(f'<div class="round"><div class="round-head">Round {turn} <span class="pill ai">'
                 f'{len(turn_runs)} test{"s" if len(turn_runs) != 1 else ""}</span></div>'
                 + (f'<div class="round-why">{ui.e(why)}</div>' if why else "") + "</div>")
            show("".join(ui.tool_row(r, summarize_tool_result(r["tool_name"], r["result"])) for r in turn_runs))
        done = next((ev for ev in events if ev["event_type"] == "INVESTIGATION_COMPLETE" and ev.get("data_json")), None)
        if done:
            show(f'<div class="finding"><b>Conclusion:</b> {ui.e(json.loads(done["data_json"]).get("findings", ""))}</div>')
        if runs:
            with st.expander("Show the raw test data"):
                for r in runs:
                    st.markdown(f"**{ui.TOOL_NAMES.get(r['tool_name'], r['tool_name'])}** (`{r['tool_name']}`)")
                    st.json({"input": r["args"], "result": r["result"]}, expanded=False)

    with t2:
        if not diag:
            st.caption("The diagnosis isn't ready yet.")
        else:
            sev = ui.SEVERITY.get(diag["severity"])
            show(f'<div class="summary"><b>Severity: {sev[1] if sev else diag["severity"]}.</b> '
                 f'{ui.e(diag.get("severity_reason", ""))}<br>Overall AI confidence: '
                 f'<b>{round(diag.get("confidence", 0) * 100)}%</b></div>')
            for i, c in enumerate(diag.get("probable_causes", []), 1):
                conf = round(float(c["confidence"]) * 100)
                evidence = "".join(f"<li>{ui.e(x)}</li>" for x in c.get("evidence", []))
                show(f'<div class="panel" style="margin-top:12px"><h4>{"Most likely" if i == 1 else f"Possibility {i}"}'
                     f'</h4><div class="cause">{ui.e(c["cause"])}</div><div class="meter"><i style="width:{conf}%">'
                     f'</i></div><div class="meter-cap">Confidence {conf}%</div>'
                     f'<p style="margin:10px 0 4px"><b>Evidence</b></p><ul>{evidence}</ul></div>')
            actions = "".join(f"<li>{ui.e(a)}</li>" for a in diag.get("recommended_actions", []))
            show(f'<div class="panel ai" style="margin-top:12px"><h4>All recommended actions</h4><ol>{actions}</ol></div>')

    with t3:
        if inc["report_md"]:
            st.download_button("Download report (.md)", inc["report_md"], file_name=f"incident-{incident_id}.md")
            with st.container(border=True):
                st.markdown(inc["report_md"])
        else:
            st.caption("The report isn't written yet.")

    with t4:
        payload = inc.get("github_payload")
        if inc["github_issue_url"]:
            st.link_button(f"Open GitHub issue #{inc['github_issue_number']}", inc["github_issue_url"])
        elif payload:
            st.caption("No GitHub token is set, so this is a preview of the issue that would be filed.")
        if payload:
            show(f'<div class="summary"><b>Title:</b> {ui.e(payload["title"])}<br><b>Labels:</b> '
                 + " ".join(f'<span class="pill none">{ui.e(lbl)}</span>' for lbl in payload["labels"]) + "</div>")
            with st.expander("Issue text"):
                st.code(payload["body"], language="markdown")
        elif not inc["github_issue_url"]:
            st.caption("No ticket yet." + (" It will be filed when you approve." if pending == "approval" else ""))

    with t5:
        show(ui.timeline(db.list_events(incident_id)))


def run_restart(incident_id: int, offline: bool) -> None:
    from app.agent import runner

    label = "The offline demo AI is analysing..." if offline else "The AI is investigating..."
    with st.spinner(label):
        try:
            runner.restart_investigation(incident_id, offline=offline)
            flash("success", "Investigation finished.")
        except Exception as exc:  # noqa: BLE001
            flash("error", f"The investigation failed: {exc}. Try 'Finish with offline AI'.")
    st.rerun()


def action_bar(inc: dict, pending: str | None, incident_id: int) -> None:
    """The one thing the user should do next for this incident, explained in plain words."""
    from app.agent import runner

    status = inc["status"]
    if pending == "approval":
        show(ui.callout("decide", "Your decision is needed",
                        "The AI recommends filing a GitHub issue with its report. Approve to file it, or dismiss it "
                        "if this is a false alarm."))
    elif status == "TICKET_CREATED":
        where = "on GitHub" if inc["github_issue_url"] else "(simulated, no GitHub token)"
        show(ui.callout("wait", "Waiting for the fix",
                        f"A ticket was filed {where}. Once the service has been fixed, verify that it works again "
                        "to close the incident."))
    elif status == "RESOLVED":
        show(ui.callout("done", "Resolved",
                        f"The site was re-checked and is healthy again. Total time: "
                        f"{ui.duration(inc['opened_at'], inc['resolved_at'])}."))
    elif status == "REJECTED":
        show(ui.callout("idle", "Dismissed", "This incident was dismissed, so no ticket was filed."))
    elif status in ("INVESTIGATING", "OPEN") and pending is None and runner.is_stalled(incident_id):
        idle = runner.seconds_since_activity(incident_id)
        since = f"for {int(idle // 60)} min" if idle else "yet"
        show(ui.callout("decide", "The investigation is not making progress",
                        f"Nothing has happened {since}. The AI service may be overloaded, or the app was closed "
                        "while it was working. Restart it, or let the offline demo AI finish it right away."))
        r1, r2, _ = st.columns([2, 2, 3])
        if r1.button("Restart investigation", type="primary", key="restart", width="stretch"):
            run_restart(incident_id, offline=False)
        if r2.button("Finish with offline AI", key="restart_offline", width="stretch"):
            run_restart(incident_id, offline=True)
    elif status == "INVESTIGATING":
        tests = len(db.list_tool_runs(incident_id))
        idle = runner.seconds_since_activity(incident_id) or 0
        show(ui.callout("wait", "The AI is investigating",
                        f"{tests} test{'s' if tests != 1 else ''} run so far. This page updates automatically; "
                        f"if nothing changes for {get_settings().investigation_stalled_seconds // 60} minutes "
                        f"you can restart it (last activity {int(idle)} s ago)."))
    elif status == "OPEN" and pending is None:
        show(ui.callout("idle", "Not investigated yet", "Start the AI investigation to find the cause."))
        if st.button("Run AI investigation", key="investigate", type="primary"):
            run_restart(incident_id, offline=False)

    cols = st.columns([2.2, 1.2, 1.8, 2.8])
    slot = 0
    if pending == "approval":
        if cols[0].button("Approve & create ticket", type="primary", key="approve", width="stretch"):
            with st.spinner("Filing the GitHub issue..."):
                runner.submit_approval(incident_id, True, reviewer="dashboard")
            flash("success", "Approved. The ticket has been filed.")
            st.rerun()
        if cols[1].button("Dismiss", key="reject", width="stretch"):
            runner.submit_approval(incident_id, False, reviewer="dashboard")
            flash("info", "Dismissed. No ticket was filed.")
            st.rerun()
        slot = 2
    if status not in ("RESOLVED", "REJECTED"):
        verify_type = "primary" if status == "TICKET_CREATED" else "secondary"
        if cols[slot].button("Verify recovery", type=verify_type, key="verify", width="stretch",
                          help="Re-check the site now. If it's healthy, the incident is closed."):
            with st.spinner("Re-checking the site..."):
                result = runner.verify_recovery(incident_id)
            flash("success" if result["recovered"] else "error",
                  ("The site is healthy again. " if result["recovered"] else "Still failing. ") + result["message"])
            st.rerun()
        slot += 1
    if inc["github_issue_url"]:
        cols[slot].link_button("Open GitHub issue", inc["github_issue_url"], width="stretch")


# ====================================================================== main
def main() -> None:
    page = sidebar()
    auto = st.session_state.get("auto_refresh", True)
    if page == "Overview":
        st.fragment(run_every=15 if auto else None)(overview_page)()
    elif page == "Site detail":
        st.fragment(run_every=15 if auto else None)(site_detail_page)()
    else:
        selected = st.session_state.get("selected_incident")
        inc = db.get_incident(selected) if selected else None
        investigating = bool(inc and inc["status"] == "INVESTIGATING")
        st.fragment(run_every=5 if (auto and investigating) else None)(incidents_page)()


main()
