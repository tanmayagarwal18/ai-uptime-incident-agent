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

from app.agent.llm import _chat_model, describe_llm_error  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.tools import ALL_TOOLS  # noqa: E402


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

    import httpx

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
    body = {"model": s.llm_model_id, "messages": [{"role": "user", "content": "Reply with the single word: pineapple"}]}
    try:
        r = httpx.post(url, json=body, timeout=60, headers={
            "Authorization": f"Bearer {s.llm_api_key}", "Accept": "application/json"})
    except Exception as exc:  # noqa: BLE001
        print(f"   FAILED to connect: {type(exc).__name__}: {exc}")
        return False
    ctype = r.headers.get("content-type", "(none)")
    print(f"   URL: {url}")
    print(f"   HTTP status: {r.status_code} | Content-Type: {ctype} | Server: {r.headers.get('server', '?')}")
    print(f"   Body starts with: {r.text[:200]!r}")
    try:
        data = r.json()
        reply = data["choices"][0]["message"]["content"]
        print(f"   Correct JSON response. Model replied: {reply!r}")
        return True
    except Exception:  # noqa: BLE001
        print("   The response is NOT the standard JSON format. Something between this PC and the AI service")
        print("   (a proxy, VPN, antivirus web filter or network gateway) is likely changing it.")
        return False


def main() -> int:
    s = get_settings()
    print(f"Provider : {s.provider}")
    print(f"Model    : {s.llm_model_id}")
    print(f"Endpoint : {s.llm_base_url or 'https://api.openai.com/v1'}")
    if s.use_mock_llm:
        print("\nNo AI provider is configured, so the offline demo AI is used.")
        print("To use free GPT-4.1 mini, set LLM_PROVIDER=github_models and GITHUB_TOKEN in .env.")
        return 1

    if not raw_probe(s):
        return 1

    print("\n1) Sending a test message...")
    try:
        reply = _chat_model().invoke([HumanMessage(content="Reply with exactly: OK")])
        print(f"   Model replied: {str(reply.content).strip()[:60]}")
    except Exception as exc:  # noqa: BLE001
        print(f"   FAILED: {describe_llm_error(exc)}")
        return 1

    print("2) Checking the model can choose diagnostic tools...")
    try:
        ai = _chat_model().bind_tools(ALL_TOOLS).invoke(
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
