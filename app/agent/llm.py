"""LLM factories: GPT-4.1 mini via LangChain."""

from __future__ import annotations

import logging
from typing import Any

from app.config import get_settings
from app.models import Diagnosis
from app.tools import ALL_TOOLS

log = logging.getLogger(__name__)


def llm_mode() -> str:
    return get_settings().openai_model


def _chat_model() -> Any:
    from langchain_openai import ChatOpenAI

    s = get_settings()
    return ChatOpenAI(model=s.openai_model, api_key=s.openai_api_key, temperature=0, timeout=60, max_retries=2)


def get_tool_llm(state: dict[str, Any]) -> Any:
    """LLM bound to the diagnostic tools; it decides which tools to call."""
    return _chat_model().bind_tools(ALL_TOOLS)


def get_analyst_llm() -> Any:
    """LLM returning a structured `Diagnosis`."""
    return _chat_model().with_structured_output(Diagnosis, method="function_calling")
