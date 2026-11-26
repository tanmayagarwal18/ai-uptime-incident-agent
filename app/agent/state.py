"""LangGraph state carried through the incident-response workflow."""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    incident_id: int
    site: dict[str, Any]                       # SiteConfig as dict
    trigger_checks: list[dict[str, Any]]       # the failed checks that opened the incident
    messages: Annotated[list[AnyMessage], add_messages]
    triage_summary: str
    iterations: int                            # investigation LLM turns used
    diagnosis: dict[str, Any]                  # Diagnosis as dict
    report_md: str
    approved: bool
    reviewer: str
    issue: dict[str, Any]                      # GitHub issue info (or dry-run payload)
    recovered: bool
    verification: dict[str, Any]               # last recovery check
    llm_fallback: bool                         # AI service failed; offline demo AI took over
