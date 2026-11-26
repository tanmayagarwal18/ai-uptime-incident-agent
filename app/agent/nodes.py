"""LangGraph node functions for the incident-response workflow."""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app import db
from app.agent.llm import get_tool_llm, llm_mode
from app.agent.prompts import INVESTIGATOR_SYSTEM, TRIAGE_TEMPLATE
from app.agent.state import AgentState
from app.config import get_settings
from app.tools import TOOLS_BY_NAME

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ triage
def _format_checks(checks: list[dict[str, Any]]) -> str:
    lines = []
    for c in checks:
        rt = f"{c['response_time_ms']:.0f} ms" if c.get("response_time_ms") is not None else "-"
        lines.append(
            f"- {c['timestamp']} {c['status']} status_code={c.get('status_code')} time={rt} "
            f"error={c.get('error_type')} {c.get('error_message') or ''}".rstrip()
        )
    return "\n".join(lines) or "(none)"


def triage_node(state: AgentState) -> dict[str, Any]:
    """Load the incident, build the investigation brief and mark the incident INVESTIGATING."""
    incident_id = state["incident_id"]
    inc = db.get_incident(incident_id)
    if inc is None:
        raise ValueError(f"Incident {incident_id} not found")
    trigger = inc.get("trigger") or {}
    site = trigger.get("site") or {"name": inc["site"], "url": ""}
    checks = trigger.get("checks") or []

    summary = TRIAGE_TEMPLATE.format(
        incident_id=incident_id, site_name=site["name"], url=site.get("url"), method=site.get("method", "GET"),
        expected_status=site.get("expected_status", 200), latency_threshold_ms=site.get("latency_threshold_ms"),
        critical=site.get("critical", False), description=site.get("description") or "-",
        summary=inc.get("summary"), checks=_format_checks(checks),
    )
    db.update_incident(incident_id, status="INVESTIGATING", llm_mode=llm_mode())
    db.add_event(incident_id, "INVESTIGATION_STARTED", f"AI agent started investigating (LLM: {llm_mode()})")
    system = INVESTIGATOR_SYSTEM.format(max_iterations=get_settings().agent_max_iterations)
    return {
        "site": site,
        "trigger_checks": checks,
        "triage_summary": summary,
        "iterations": 0,
        "messages": [SystemMessage(content=system), HumanMessage(content=summary)],
    }


# ------------------------------------------------------------------ investigate (LLM chooses tools)
def investigate_node(state: AgentState) -> dict[str, Any]:
    """One reasoning turn: the LLM looks at the evidence so far and either calls tools or stops."""
    llm = get_tool_llm(state)
    ai: AIMessage = llm.invoke(state["messages"])
    iteration = state.get("iterations", 0) + 1
    if ai.tool_calls:
        names = ", ".join(tc["name"] for tc in ai.tool_calls)
        db.add_event(state["incident_id"], "AGENT_DECISION", f"Turn {iteration}: agent chose tools: {names}",
                     {"reasoning": ai.content or ""})
    else:
        db.add_event(state["incident_id"], "INVESTIGATION_COMPLETE",
                     f"Turn {iteration}: agent finished investigating", {"findings": ai.content or ""})
    return {"messages": [ai], "iterations": iteration}


def route_after_investigate(state: AgentState) -> str:
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and last.tool_calls and state.get("iterations", 0) < get_settings().agent_max_iterations:
        return "tools"
    return "analyze"


def _run_tool(call: dict[str, Any]) -> tuple[dict[str, Any], Any, float]:
    tool = TOOLS_BY_NAME.get(call["name"])
    start = time.perf_counter()
    if tool is None:
        result: Any = {"ok": False, "error": f"Unknown tool {call['name']}"}
    else:
        try:
            result = tool.invoke(call.get("args") or {})
        except Exception as exc:  # noqa: BLE001 - e.g. invalid args from the LLM
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return call, result, round((time.perf_counter() - start) * 1000, 1)


def tools_node(state: AgentState) -> dict[str, Any]:
    """Execute the tool calls requested by the LLM (in parallel), log each one, return ToolMessages."""
    last = state["messages"][-1]
    calls = list(getattr(last, "tool_calls", []) or [])
    with ThreadPoolExecutor(max_workers=max(1, len(calls))) as pool:
        outcomes = list(pool.map(_run_tool, calls))
    messages = []
    for call, result, duration in outcomes:
        db.insert_tool_run(state["incident_id"], call["name"], call.get("args") or {}, result, duration,
                           state.get("iterations", 0))
        content = json.dumps(result, default=str)
        if len(content) > 4000:
            content = content[:4000] + "...(truncated)"
        messages.append(ToolMessage(content=content, tool_call_id=call["id"], name=call["name"]))
    return {"messages": messages}


# ------------------------------------------------------------------ analyze (structured output)
def collect_evidence(state: AgentState) -> list[dict[str, Any]]:
    """Pair each ToolMessage with the tool call that produced it."""
    calls: dict[str, dict[str, Any]] = {}
    evidence: list[dict[str, Any]] = []
    for m in state.get("messages", []):
        if isinstance(m, AIMessage):
            for tc in m.tool_calls or []:
                calls[tc["id"]] = tc
        elif isinstance(m, ToolMessage):
            tc = calls.get(m.tool_call_id, {})
            try:
                result = json.loads(m.content)
            except (TypeError, ValueError):
                result = m.content
            evidence.append({"tool": m.name or tc.get("name"), "args": tc.get("args", {}), "result": result})
    return evidence


def _final_findings(state: AgentState) -> str:
    for m in reversed(state.get("messages", [])):
        if isinstance(m, AIMessage) and not m.tool_calls and m.content:
            return str(m.content)
    return ""


def analyze_node(state: AgentState) -> dict[str, Any]:
    """Turn the gathered evidence into a structured Diagnosis (causes, severity, actions)."""
    from app.agent.llm import get_analyst_llm
    from app.agent.prompts import ANALYST_SYSTEM

    evidence = collect_evidence(state)
    evidence_text = "\n".join(
        f"[{i}] {e['tool']}({json.dumps(e['args'])}) -> {json.dumps(e['result'], default=str)[:1500]}"
        for i, e in enumerate(evidence, 1)
    )
    prompt = (f"{state['triage_summary']}\n\nDIAGNOSTIC EVIDENCE:\n{evidence_text or '(no tools were run)'}\n\n"
              f"INVESTIGATOR FINDINGS:\n{_final_findings(state) or '(none)'}\n\nProduce the structured diagnosis.")
    diagnosis = get_analyst_llm(state, evidence).invoke([SystemMessage(content=ANALYST_SYSTEM), HumanMessage(content=prompt)])
    diag = diagnosis.model_dump()
    diag["probable_causes"] = sorted(diag["probable_causes"], key=lambda c: c["confidence"], reverse=True)

    top = diag["probable_causes"][0]["cause"] if diag["probable_causes"] else "unknown"
    db.update_incident(state["incident_id"], severity=diag["severity"], diagnosis_json=diag)
    db.add_event(state["incident_id"], "DIAGNOSED", f"Severity {diag['severity']} - most likely cause: {top}")
    return {"diagnosis": diag}


# ------------------------------------------------------------------ report
def report_node(state: AgentState) -> dict[str, Any]:
    """Render and store the Markdown incident report."""
    from app.reporting.report import render_report

    incident_id = state["incident_id"]
    db.add_event(incident_id, "REPORT_GENERATED", "Structured incident report generated")
    report = render_report(
        db.get_incident(incident_id), state["diagnosis"], db.list_tool_runs(incident_id),
        db.list_events(incident_id), state["site"],
    )
    db.update_incident(incident_id, report_md=report)
    return {"report_md": report}


# ------------------------------------------------------------------ human-in-the-loop approval
def approval_gate_node(state: AgentState) -> dict[str, Any]:
    """Pause for a human to approve creating the GitHub issue (LangGraph interrupt)."""
    from langgraph.types import interrupt

    incident_id = state["incident_id"]
    if not get_settings().require_human_approval:
        db.add_event(incident_id, "AUTO_APPROVED", "Human approval disabled - ticket creation auto-approved")
        return {"approved": True, "reviewer": "auto"}

    inc = db.get_incident(incident_id)
    if inc and inc["status"] != "AWAITING_APPROVAL":
        db.update_incident(incident_id, status="AWAITING_APPROVAL")
        db.add_event(incident_id, "AWAITING_APPROVAL", "Waiting for human approval to create the GitHub issue")

    decision = interrupt({
        "type": "approval",
        "incident_id": incident_id,
        "question": "Create a GitHub issue for this incident?",
        "severity": state.get("diagnosis", {}).get("severity"),
    })
    approved = bool(decision.get("approved")) if isinstance(decision, dict) else bool(decision)
    reviewer = decision.get("reviewer", "operator") if isinstance(decision, dict) else "operator"
    db.add_event(incident_id, "APPROVED" if approved else "REJECTED",
                 f"Ticket creation {'approved' if approved else 'rejected'} by {reviewer}")
    return {"approved": approved, "reviewer": reviewer}


def route_after_approval(state: AgentState) -> str:
    return "create_ticket" if state.get("approved") else "reject"


def reject_node(state: AgentState) -> dict[str, Any]:
    db.update_incident(state["incident_id"], status="REJECTED")
    _refresh_report(state)
    return {}


def _refresh_report(state: AgentState) -> None:
    """Re-render the stored report so its status and timeline stay current."""
    from app.reporting.report import render_report

    incident_id = state["incident_id"]
    inc = db.get_incident(incident_id)
    if inc and state.get("diagnosis"):
        db.update_incident(incident_id, report_md=render_report(
            inc, state["diagnosis"], db.list_tool_runs(incident_id), db.list_events(incident_id), state["site"]))


# ------------------------------------------------------------------ GitHub ticket
def create_ticket_node(state: AgentState) -> dict[str, Any]:
    """Create the GitHub issue (or record a dry-run payload)."""
    from app.integrations.github_client import GitHubClient, GitHubError

    incident_id = state["incident_id"]
    diag, site = state["diagnosis"], state["site"]
    top = diag["probable_causes"][0]["cause"] if diag.get("probable_causes") else "service failure"
    title = f"[{diag['severity']}] {site['name']}: {top} (incident #{incident_id})"
    body = (db.get_incident(incident_id) or {}).get("report_md") or state.get("report_md", "")
    labels = ["incident", f"severity:{diag['severity']}", f"site:{site['name']}"[:50]]

    client = GitHubClient.from_settings()
    try:
        issue = client.create_issue(title, body, labels)
    except GitHubError as exc:
        log.error("GitHub issue creation failed: %s", exc)
        db.add_event(incident_id, "TICKET_FAILED", f"GitHub API error - payload stored locally: {exc}")
        issue = {"dry_run": True, "number": None, "html_url": None, "error": str(exc),
                 "payload": {"title": title, "body": body, "labels": labels}}

    db.update_incident(incident_id, status="TICKET_CREATED", github_issue_number=issue.get("number"),
                       github_issue_url=issue.get("html_url"), github_payload_json=issue["payload"])
    if issue.get("html_url"):
        db.add_event(incident_id, "TICKET_CREATED", f"GitHub issue #{issue['number']} created", {"url": issue["html_url"]})
    elif not issue.get("error"):
        db.add_event(incident_id, "TICKET_CREATED", "GitHub dry-run: issue payload recorded (no token configured)")
    _refresh_report(state)
    return {"issue": {k: v for k, v in issue.items() if k != "payload"}}


# ------------------------------------------------------------------ recovery verification
def await_recovery_node(state: AgentState) -> dict[str, Any]:
    """Pause until someone (dashboard button, API or the monitor's auto-verify) asks to verify recovery."""
    from langgraph.types import interrupt

    interrupt({"type": "await_recovery", "incident_id": state["incident_id"]})
    return {}


def verify_recovery_node(state: AgentState) -> dict[str, Any]:
    """Re-run the health check; resolve the incident and close the GitHub issue if the site is healthy."""
    from app.config import SiteConfig
    from app.integrations.github_client import GitHubClient, GitHubError
    from app.monitor.checker import check_site

    incident_id = state["incident_id"]
    site = SiteConfig(**state["site"])
    result = check_site(site)
    db.insert_check(result.model_dump(mode="json"))
    verification = result.model_dump(mode="json")
    issue = state.get("issue") or {}
    client = GitHubClient.from_settings()
    recovered = result.status == "UP"

    if recovered:
        db.update_incident(incident_id, status="RESOLVED", resolved_at=db.utcnow())
        db.add_event(incident_id, "RESOLVED",
                     f"Recovery verified: HTTP {result.status_code} in {result.response_time_ms:.0f} ms", verification)
        comment = (f"**Recovery verified** by the AI incident agent at {result.timestamp} UTC.\n\n"
                   f"- HTTP status: `{result.status_code}`\n- Response time: `{result.response_time_ms:.0f} ms`\n\n"
                   "Closing this incident.")
    else:
        db.add_event(incident_id, "STILL_FAILING",
                     f"Recovery check failed: {result.status} {result.status_code or result.error_type}", verification)
        comment = (f"Recovery check at {result.timestamp} UTC: still **{result.status}** "
                   f"(`{result.status_code or result.error_type}`). Incident remains open.")
    try:
        client.add_comment(issue.get("number"), comment)
        if recovered:
            client.close_issue(issue.get("number"))
    except GitHubError as exc:
        db.add_event(incident_id, "GITHUB_UPDATE_FAILED", str(exc))
    _refresh_report(state)
    return {"recovered": recovered, "verification": verification}


def route_after_verify(state: AgentState) -> str:
    return "done" if state.get("recovered") else "retry"
