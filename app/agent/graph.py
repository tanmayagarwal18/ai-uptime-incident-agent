"""Builds the LangGraph StateGraph for incident response."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agent.nodes import investigate_node, route_after_investigate, tools_node, triage_node
from app.agent.state import AgentState


def build_graph(checkpointer: Any = None):
    g = StateGraph(AgentState)
    g.add_node("triage", triage_node)
    g.add_node("investigate", investigate_node)
    g.add_node("tools", tools_node)

    g.add_edge(START, "triage")
    g.add_edge("triage", "investigate")
    g.add_conditional_edges("investigate", route_after_investigate, {"tools": "tools", "analyze": END})
    g.add_edge("tools", "investigate")
    return g.compile(checkpointer=checkpointer)
