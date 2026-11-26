"""Check that the configured AI model is reachable and can use tools - run this after editing .env.

Usage:  python scripts/check_ai.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.messages import HumanMessage  # noqa: E402

from app.agent.llm import _chat_model, describe_llm_error  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.tools import ALL_TOOLS  # noqa: E402


def main() -> int:
    s = get_settings()
    print(f"Provider : {s.provider}")
    print(f"Model    : {s.llm_model_id}")
    print(f"Endpoint : {s.llm_base_url or 'https://api.openai.com/v1'}")
    if s.use_mock_llm:
        print("\nNo AI provider is configured, so the offline demo AI is used.")
        print("To use free GPT-4.1 mini, set LLM_PROVIDER=github_models and GITHUB_TOKEN in .env.")
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
