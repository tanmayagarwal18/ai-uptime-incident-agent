"""LLM factories: GPT-4.1 mini via LangChain, or the offline mock when no API key is configured."""

from __future__ import annotations

import logging
from typing import Any

from app.config import get_settings
from app.models import Diagnosis
from app.tools import ALL_TOOLS

log = logging.getLogger(__name__)
_warned = False


def llm_mode() -> str:
    s = get_settings()
    return "mock (offline rule-based)" if s.use_mock_llm else s.openai_model


def _chat_model() -> Any:
    from langchain_openai import ChatOpenAI

    s = get_settings()
    return ChatOpenAI(model=s.openai_model, api_key=s.openai_api_key, temperature=0, timeout=60, max_retries=2)


def get_tool_llm(state: dict[str, Any]) -> Any:
    """LLM bound to the diagnostic tools; it decides which tools to call."""
    if get_settings().use_mock_llm:
        from app.agent.mock_llm import MockToolCallingLLM

        global _warned
        if not _warned:
            log.warning("OPENAI_API_KEY not set - using offline mock LLM")
            _warned = True
        return MockToolCallingLLM(state)
    return _chat_model().bind_tools(ALL_TOOLS)


def get_analyst_llm(state: dict[str, Any] | None = None, evidence: list[dict[str, Any]] | None = None) -> Any:
    """LLM returning a structured `Diagnosis`."""
    if get_settings().use_mock_llm:
        from app.agent.mock_llm import MockAnalystLLM

        return MockAnalystLLM(state or {}, evidence or [])
    return _chat_model().with_structured_output(Diagnosis, method="function_calling")
