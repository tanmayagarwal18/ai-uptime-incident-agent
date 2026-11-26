"""Check that the configured AI model is reachable and can use tools - run this after editing .env.

Usage:  python scripts/check_ai.py
"""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.messages import HumanMessage  # noqa: E402

from app.agent.llm import describe_llm_error, get_chat_llm, get_tool_llm, message_text  # noqa: E402
from app.config import get_settings  # noqa: E402


def _mask(url: str) -> str:
    """Hide any username/password inside a proxy URL."""
    parts = urlsplit(url)
    if parts.username or parts.password:
        return url.replace(parts.netloc, "***@" + parts.netloc.split("@")[-1])
    return url


def raw_probe(s) -> bool:
    """Send one request with plain HTTP (no AI library) and show exactly what comes back."""
    import importlib.metadata as md
    import urllib.request


    def ver(pkg: str) -> str:
        try:
            return md.version(pkg)
        except md.PackageNotFoundError:
            return "not installed"

    print("\n0) Raw connection test")
    print(f"   Python {platform.python_version()} on {platform.system()} | openai {ver('openai')} | "
          f"langchain-openai {ver('langchain-openai')} | httpx {ver('httpx')}")
    proxies = {k: _mask(v) for k, v in urllib.request.getproxies().items() if k in ("http", "https", "all")}
    print(f"   Proxy settings seen by Python: {proxies or 'none'}")
    for var in ("OPENAI_BASE_URL", "OPENAI_API_BASE", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        if os.environ.get(var):
            print(f"   Environment variable {var} is set: {_mask(os.environ[var])}")

    url = (s.llm_base_url or "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
    models = [s.llm_model_id]
    if s.provider == "gemini" and s.gemini_fallback_model.strip() and s.gemini_fallback_model != s.llm_model_id:
        models.append(s.gemini_fallback_model.strip())
    working = []
    for model in models:
        if probe_model(url, model, s.llm_api_key):
            working.append(model)
    if not working:
        return False
    if s.llm_model_id not in working:
        print(f"\n   The main model is unavailable right now, so the agent will use the backup model "
              f"{working[0]} automatically.")
    return True


def probe_model(url: str, model: str, key: str) -> bool:
    import httpx

    body = {"model": model, "messages": [{"role": "user", "content": "Reply with the single word: pineapple"}]}
    print(f"\n   Model {model}:")
    try:
        r = httpx.post(url, json=body, timeout=60, headers={"Authorization": f"Bearer {key}",
                                                             "Accept": "application/json"})
    except Exception as exc:  # noqa: BLE001
        print(f"   FAILED to connect: {type(exc).__name__}: {exc}")
        return False
    print(f"   HTTP status: {r.status_code} | Content-Type: {r.headers.get('content-type', '(none)')}")
    try:
        data = r.json()
    except Exception:  # noqa: BLE001
        print(f"   Body starts with: {r.text[:200]!r}")
        print("   The response is NOT JSON. Something between this PC and the AI service")
        print("   (a proxy, VPN, antivirus web filter or network gateway) may be changing it.")
        return False
    if r.status_code == 200:
        try:
            print(f"   OK - model replied: {data['choices'][0]['message']['content']!r}")
            return True
        except (KeyError, IndexError, TypeError):
            print(f"   Unexpected reply: {str(data)[:200]}")
            return False
    err = data[0] if isinstance(data, list) and data else data
    err = err.get("error", err) if isinstance(err, dict) else {}
    message = err.get("message", str(data)[:200]) if isinstance(err, dict) else str(data)[:200]
    print(f"   The AI service answered with an error: {message}")
    advice = {
        400: "Check the API key in .env (copy it again with the Copy key button).",
        401: "The API key was rejected - check it in .env.",
        403: "The key is not allowed to use this model, or the API is not enabled for its project.",
        404: "This model name does not exist - check the model setting in .env.",
        429: "The free usage limit was reached - wait a while and try again.",
        503: "Your key works, but the model is overloaded right now (Google's side). Try again in a few minutes.",
    }.get(r.status_code)
    if advice:
        print(f"   What to do: {advice}")
    return False


def main() -> int:
    s = get_settings()
    print(f"Provider : {s.provider}")
    print(f"Model    : {s.llm_model_id}")
    print(f"Endpoint : {s.llm_base_url or 'https://api.openai.com/v1'}")
    if s.use_mock_llm:
        print("\nNo AI provider is configured, so the offline demo AI is used.")
        print("To use free Google Gemini, set LLM_PROVIDER=gemini and GEMINI_API_KEY in .env")
        print("(get a free key at https://aistudio.google.com/apikey).")
        return 1

    if not raw_probe(s):
        return 1

    print("\n1) Sending a test message...")
    try:
        reply = get_chat_llm().invoke([HumanMessage(content="Reply with exactly: OK")])
        print(f"   Model replied: {message_text(reply.content).strip()[:60]}")
    except Exception as exc:  # noqa: BLE001
        print(f"   FAILED: {describe_llm_error(exc)}")
        return 1

    print("2) Checking the model can choose diagnostic tools...")
    try:
        ai = get_tool_llm({}).invoke(
            [HumanMessage(content="The site https://example.com is down. Call http_probe on it.")])
        names = [tc["name"] for tc in ai.tool_calls]
        if not names:
            print("   The model answered but did not call a tool. The agent may work less well with this model.")
            return 1
        print(f"   Model chose: {', '.join(names)}")
    except Exception as exc:  # noqa: BLE001
        print(f"   FAILED: {describe_llm_error(exc)}")
        return 1

    print(f"\nAll good: the agent will use {s.llm_label}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
