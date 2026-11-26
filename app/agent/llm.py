"""LLM factories. GPT-4.1 mini via GitHub Models (free) or OpenAI, any OpenAI-compatible server,
or the offline demo AI. Also turns AI-service errors into plain-language reasons."""

from __future__ import annotations

import logging
from typing import Any

from app.config import get_settings
from app.models import Diagnosis
from app.tools import ALL_TOOLS

log = logging.getLogger(__name__)
_warned = False


def llm_mode() -> str:
    return get_settings().llm_label


def _chat_model() -> Any:
    from langchain_openai import ChatOpenAI

    s = get_settings()
    kwargs: dict[str, Any] = {
        "model": s.llm_model_id,
        "api_key": s.llm_api_key,
        "temperature": 0,
        "timeout": 60,
        "max_retries": 2,
        "max_tokens": s.llm_max_output_tokens,
    }
    if s.llm_base_url:
        kwargs["base_url"] = s.llm_base_url
    return ChatOpenAI(**kwargs)


def _use_mock(state: dict[str, Any] | None) -> bool:
    global _warned
    if get_settings().use_mock_llm:
        if not _warned:
            log.warning("No AI provider configured - using the offline demo AI")
            _warned = True
        return True
    return bool(state and state.get("llm_fallback"))


def get_tool_llm(state: dict[str, Any]) -> Any:
    """LLM bound to the diagnostic tools; it decides which tools to call."""
    if _use_mock(state):
        return get_fallback_tool_llm(state)
    return _chat_model().bind_tools(ALL_TOOLS)


def get_analyst_llm(state: dict[str, Any] | None = None, evidence: list[dict[str, Any]] | None = None) -> Any:
    """LLM returning a structured `Diagnosis`."""
    if _use_mock(state):
        return get_fallback_analyst_llm(state or {}, evidence or [])
    return _chat_model().with_structured_output(Diagnosis, method="function_calling")


def get_fallback_tool_llm(state: dict[str, Any]) -> Any:
    from app.agent.mock_llm import MockToolCallingLLM

    return MockToolCallingLLM(state)


def get_fallback_analyst_llm(state: dict[str, Any], evidence: list[dict[str, Any]]) -> Any:
    from app.agent.mock_llm import MockAnalystLLM

    return MockAnalystLLM(state, evidence)


def describe_llm_error(exc: Exception) -> str:
    """Plain-language reason for an AI-service failure."""
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    text = str(exc).lower()
    provider = get_settings().provider
    if status == 401 or "unauthorized" in text or "bad credentials" in text:
        if provider == "github_models":
            return "the GitHub token was rejected - check it is valid and has the 'Models: read' permission"
        return "the API key was rejected - check it in .env"
    if status == 403:
        return "access denied - the token lacks permission for this model"
    if status == 429 or "rate limit" in text or "ratelimit" in text:
        return "the free usage limit was reached - wait a while or try again tomorrow"
    if status == 413 or "too large" in text or "context length" in text or "tokens_limit" in text:
        return "the request was too large for the model's limit"
    if status == 404 or "unknown model" in text or "model_not_found" in text:
        return f"the model '{get_settings().llm_model_id}' was not found - check OPENAI_MODEL"
    if "connect" in text or "timed out" in text or "timeout" in text:
        return "the AI service could not be reached - check the internet connection"
    return f"{type(exc).__name__}: {str(exc)[:160]}"
