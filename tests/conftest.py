"""Shared fixtures: every test gets its own temporary databases and no real API keys."""

from __future__ import annotations

import pytest

from app.config import SiteConfig, get_settings


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("CHECKPOINT_DB_PATH", str(tmp_path / "checkpoints.db"))
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("LLM_PROVIDER", "auto")
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("OPENAI_BASE_URL", "")
    monkeypatch.setenv("GITHUB_TOKEN", "")
    monkeypatch.setenv("GITHUB_REPO", "octo/test-repo")
    monkeypatch.setenv("REQUIRE_HUMAN_APPROVAL", "true")
    get_settings.cache_clear()
    from app import db

    db.init_db()
    yield
    get_settings.cache_clear()


@pytest.fixture
def site() -> SiteConfig:
    return SiteConfig(
        name="example", url="https://example.test/health", timeout_s=2,
        latency_threshold_ms=1000, failure_threshold=2, critical=True,
    )
