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
