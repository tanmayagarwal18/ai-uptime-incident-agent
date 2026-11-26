"""Builds the LangGraph StateGraph for incident response."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agent.nodes import (
    analyze_node,
    approval_gate_node,
    await_recovery_node,
    create_ticket_node,
    investigate_node,
    reject_node,
    report_node,
    route_after_approval,
    route_after_investigate,
    route_after_verify,
    tools_node,
    triage_node,
    verify_recovery_node,
)
from app.agent.state import AgentState


def build_graph(checkpointer: Any = None):
    """triage -> investigate <-> tools -> analyze -> report -> approval_gate -> create_ticket
    -> await_recovery <-> verify_recovery -> END   (approval_gate -> reject -> END when rejected)"""
    g = StateGraph(AgentState)
    g.add_node("triage", triage_node)
    g.add_node("investigate", investigate_node)
    g.add_node("tools", tools_node)
    g.add_node("analyze", analyze_node)
    g.add_node("report", report_node)
    g.add_node("approval_gate", approval_gate_node)
    g.add_node("reject", reject_node)
    g.add_node("create_ticket", create_ticket_node)
    g.add_node("await_recovery", await_recovery_node)
    g.add_node("verify_recovery", verify_recovery_node)

    g.add_edge(START, "triage")
    g.add_edge("triage", "investigate")
    g.add_conditional_edges("investigate", route_after_investigate, {"tools": "tools", "analyze": "analyze"})
    g.add_edge("tools", "investigate")
    g.add_edge("analyze", "report")
    g.add_edge("report", "approval_gate")
    g.add_conditional_edges("approval_gate", route_after_approval, {"create_ticket": "create_ticket", "reject": "reject"})
    g.add_edge("reject", END)
    g.add_edge("create_ticket", "await_recovery")
    g.add_edge("await_recovery", "verify_recovery")
    g.add_conditional_edges("verify_recovery", route_after_verify, {"done": END, "retry": "await_recovery"})
    return g.compile(checkpointer=checkpointer)
