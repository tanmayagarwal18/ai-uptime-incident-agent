"""Streamlit dashboard for the AI Website Uptime Monitoring & Incident Response Agent.

Run:  python -m streamlit run dashboard/streamlit_app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from app import db, services  # noqa: E402
from app.config import get_settings, load_sites, setup_logging  # noqa: E402

st.set_page_config(page_title="AI Uptime Incident Agent", page_icon="🛰️", layout="wide")
setup_logging()

# Reference palette (dataviz skill): one series hue + reserved status colours, always paired with a text label.
SERIES = "#257de0"
MUTED = "#8a8a8a"
STATUS_COLORS = {"UP": "#0aac23", "DEGRADED": "#fbb315", "DOWN": "#dc3c3d", "UNKNOWN": MUTED}
STATUS_ICONS = {"UP": "🟢", "DEGRADED": "🟡", "DOWN": "🔴", "UNKNOWN": "⚪"}
SEV_ICONS = {"SEV1": "🔴", "SEV2": "🟠", "SEV3": "🟡", "SEV4": "🟢"}
INCIDENT_ICONS = {"OPEN": "🆕", "INVESTIGATING": "🔎", "AWAITING_APPROVAL": "⏳", "TICKET_CREATED": "🎫",
                  "RESOLVED": "✅", "REJECTED": "🚫"}

PAGES = ["Overview", "Site detail", "Incidents"]


@st.cache_resource
def _bootstrap() -> bool:
    services.bootstrap()
    return True


_bootstrap()
settings = get_settings()


def badge(status: str | None) -> str:
    return f"{STATUS_ICONS.get(status or 'UNKNOWN', '⚪')} {status or 'UNKNOWN'}"


def fmt_ms(v) -> str:
    return "-" if v is None or pd.isna(v) else f"{v:,.0f} ms"


def go_to_incident(incident_id: int) -> None:
    st.session_state["page"] = "Incidents"
    st.session_state["selected_incident"] = incident_id


# ====================================================================== sidebar
def sidebar() -> str:
    with st.sidebar:
        st.title("🛰️ Uptime Agent")
        st.caption("AI website monitoring & incident response")
        page = st.radio("Navigate", PAGES, key="page")
        st.divider()
        st.markdown("**Agent configuration**")
        st.markdown(f"- LLM: `{'mock (offline)' if settings.use_mock_llm else settings.openai_model}`")
        st.markdown(f"- GitHub: `{settings.github_repo if settings.github_enabled else 'dry-run'}`")
        st.markdown(f"- Human approval: `{'required' if settings.require_human_approval else 'off'}`")
        st.toggle("Auto-refresh (15 s)", key="auto_refresh", value=True)
    return page


# ====================================================================== overview
def run_checks_button(key: str) -> None:
    if st.button("▶ Run checks now", key=key, type="primary"):
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
    st.dataframe(table, hide_index=True, width="stretch")

    st.subheader("Latest incidents")
    if not incidents:
        st.caption("No incidents yet.")
    for inc in incidents[:5]:
        cols = st.columns([1, 2, 5, 2, 1])
        cols[0].markdown(f"**#{inc['id']}**")
        cols[1].markdown(f"{INCIDENT_ICONS.get(inc['status'], '')} {inc['status']}")
        cols[2].markdown(f"{SEV_ICONS.get(inc['severity'] or '', '')} {inc['severity'] or ''} {inc['summary'][:90]}")
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
    st.header("Incidents")
    st.info("Incident management view coming next.")


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
