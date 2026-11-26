"""LLM factories: free Google Gemini or gpt-oss-120b on Groq, GPT-4.1 mini on OpenAI, any
OpenAI-compatible server, or the offline demo AI. Also turns AI-service errors into plain-language reasons."""

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
    s = get_settings()
    if s.provider == "gemini":
        # Google's own integration: it returns Gemini 3 "thought signatures" with tool results,
        # which the OpenAI-compatible route drops (causing 400 errors on multi-step tool use).
        return _gemini(s.llm_model_id)

    from langchain_openai import ChatOpenAI

    kwargs: dict[str, Any] = {
        "model": s.llm_model_id,
        "api_key": s.llm_api_key,
        "temperature": 0,
        "timeout": 60,
        "max_retries": 6 if s.provider == "groq" else 2,  # Groq free tier: wait out per-minute limits
        "max_tokens": s.max_output_tokens,
    }
    if s.is_reasoning_model:
        kwargs["reasoning_effort"] = "low"  # fewer hidden reasoning tokens -> faster, stays under limits
    if s.llm_base_url:
        kwargs["base_url"] = s.llm_base_url
    return ChatOpenAI(**kwargs)


def _gemini(model: str) -> Any:
    from langchain_google_genai import ChatGoogleGenerativeAI

    s = get_settings()
    extra = {"base_url": s.gemini_base_url} if s.gemini_base_url.strip() else {}
    return ChatGoogleGenerativeAI(model=model, api_key=s.llm_api_key, reasoning_effort="low",
                                  max_tokens=s.max_output_tokens, retries=2, request_timeout=90, **extra)


def _gemini_backup() -> Any | None:
    """Second Gemini model to try when the main one is overloaded (HTTP 503) or rate limited."""
    s = get_settings()
    backup = s.gemini_fallback_model.strip()
    if s.provider != "gemini" or not backup or backup == s.llm_model_id:
        return None
    return _gemini(backup)


def _use_mock(state: dict[str, Any] | None) -> bool:
    global _warned
    if get_settings().use_mock_llm:
        if not _warned:
            log.warning("No AI provider configured - using the offline demo AI")
            _warned = True
        return True
    return bool(state and state.get("llm_fallback"))


def get_chat_llm() -> Any:
    """Plain chat model (no tools), with the Gemini backup model when configured."""
    backup = _gemini_backup()
    return _chat_model().with_fallbacks([backup]) if backup else _chat_model()


def get_tool_llm(state: dict[str, Any]) -> Any:
    """LLM bound to the diagnostic tools; it decides which tools to call."""
    if _use_mock(state):
        return get_fallback_tool_llm(state)
    llm = _chat_model().bind_tools(ALL_TOOLS)
    backup = _gemini_backup()
    return llm.with_fallbacks([backup.bind_tools(ALL_TOOLS)]) if backup else llm


def get_analyst_llm(state: dict[str, Any] | None = None, evidence: list[dict[str, Any]] | None = None) -> Any:
    """LLM returning a structured `Diagnosis`."""
    if _use_mock(state):
        return get_fallback_analyst_llm(state or {}, evidence or [])
    method = "json_schema" if get_settings().provider == "gemini" else "function_calling"
    llm = _chat_model().with_structured_output(Diagnosis, method=method)
    backup = _gemini_backup()
    return llm.with_fallbacks([backup.with_structured_output(Diagnosis, method=method)]) if backup else llm


def get_fallback_tool_llm(state: dict[str, Any]) -> Any:
    from app.agent.mock_llm import MockToolCallingLLM

    return MockToolCallingLLM(state)


def get_fallback_analyst_llm(state: dict[str, Any], evidence: list[dict[str, Any]]) -> Any:
    from app.agent.mock_llm import MockAnalystLLM

    return MockAnalystLLM(state, evidence)


def message_text(content: Any) -> str:
    """Plain text of a model message (Gemini returns a list of content blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content
                        if not isinstance(b, dict) or b.get("type") in (None, "text")).strip()
    return str(content or "")


def describe_llm_error(exc: Exception) -> str:
    """Plain-language reason for an AI-service failure."""
    status = (getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
              or getattr(exc, "code", None))
    text = str(exc).lower()
    provider = get_settings().provider
    if isinstance(exc, ValueError) and "got str" in text:
        return "the AI service returned an unexpected non-JSON reply - check OPENAI_BASE_URL"
    if status == 401 or "unauthorized" in text or "invalid api key" in text or "api key not valid" in text:
        key = {"groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY"}.get(provider, "OPENAI_API_KEY")
        return f"the API key was rejected - check {key} in .env"
    if status == 413 or "request too large" in text:
        return "the request was larger than the free per-minute token limit"
    if status == 503 or "high demand" in text or "unavailable" in text or "overloaded" in text:
        return "the AI model is busy (high demand) - try again in a few minutes"
    if status == 403:
        return "access denied - the token lacks permission for this model"
    if status == 429 or "rate limit" in text or "ratelimit" in text or "resource_exhausted" in text:
        return "the free usage limit was reached - wait a while or try again tomorrow"
    if "context length" in text or "tokens_limit" in text:
        return "the request was too large for the model's limit"
    if status == 404 or "unknown model" in text or "model_not_found" in text:
        return f"the model '{get_settings().llm_model_id}' was not found - check the model name in .env"
    if "connect" in text or "timed out" in text or "timeout" in text:
        return "the AI service could not be reached - check the internet connection"
    return f"{type(exc).__name__}: {str(exc)[:160]}"
