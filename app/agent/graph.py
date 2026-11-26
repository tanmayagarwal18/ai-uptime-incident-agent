"""Builds the LangGraph StateGraph for incident response."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agent.nodes import (
    analyze_node,
    approval_gate_node,
    investigate_node,
    reject_node,
    report_node,
    route_after_approval,
    route_after_investigate,
    tools_node,
    triage_node,
)
from app.agent.state import AgentState


def build_graph(checkpointer: Any = None):
    g = StateGraph(AgentState)
    g.add_node("triage", triage_node)
    g.add_node("investigate", investigate_node)
    g.add_node("tools", tools_node)
    g.add_node("analyze", analyze_node)
    g.add_node("report", report_node)
    g.add_node("approval_gate", approval_gate_node)
    g.add_node("reject", reject_node)

    g.add_edge(START, "triage")
    g.add_edge("triage", "investigate")
    g.add_conditional_edges("investigate", route_after_investigate, {"tools": "tools", "analyze": "analyze"})
    g.add_edge("tools", "investigate")
    g.add_edge("analyze", "report")
    g.add_edge("report", "approval_gate")
    g.add_conditional_edges("approval_gate", route_after_approval, {"create_ticket": END, "reject": "reject"})
    g.add_edge("reject", END)
    return g.compile(checkpointer=checkpointer)
