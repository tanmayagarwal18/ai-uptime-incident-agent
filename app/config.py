"""Application settings and monitored-site configuration loading."""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Runtime settings, read from environment variables and an optional `.env` file."""

    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"
    force_mock_llm: bool = False

    github_token: str = ""
    github_repo: str = ""
    github_dry_run: bool = False

    require_human_approval: bool = True
    agent_max_iterations: int = 6
    auto_verify_recovery: bool = True

    check_interval_seconds: int = 30
    sites_config: str = "config/sites.yaml"
    demo_server_url: str = "http://127.0.0.1:8001"

    db_path: str = "data/monitor.db"
    checkpoint_db_path: str = "data/checkpoints.db"
    log_level: str = "INFO"

    @property
    def use_mock_llm(self) -> bool:
        """True when the agent should use the offline rule-based LLM."""
        return self.force_mock_llm or not self.openai_api_key.strip()

    @property
    def github_enabled(self) -> bool:
        """True when real GitHub API calls should be made."""
        return bool(self.github_token.strip() and self.github_repo.strip()) and not self.github_dry_run

    def resolve(self, path: str) -> Path:
        """Resolve a possibly-relative path against the project root."""
        p = Path(path)
        return p if p.is_absolute() else PROJECT_ROOT / p


class SiteConfig(BaseModel):
    """A single monitored website or API endpoint."""

    name: str
    url: str
    method: str = "GET"
    expected_status: int = 200
    timeout_s: float = 5.0
    latency_threshold_ms: int = 1500
    failure_threshold: int = 2
    critical: bool = False
    description: str = ""

    @field_validator("method")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()


class SitesFile(BaseModel):
    sites: list[SiteConfig] = Field(default_factory=list)


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings instance (call `get_settings.cache_clear()` in tests)."""
    return Settings()


def load_sites(path: str | None = None) -> list[SiteConfig]:
    """Load monitored sites from the YAML config file."""
    settings = get_settings()
    cfg_path = settings.resolve(path or settings.sites_config)
    with open(cfg_path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return SitesFile(**raw).sites


def get_site(name: str) -> SiteConfig | None:
    """Find a configured site by name."""
    return next((s for s in load_sites() if s.name == name), None)


def setup_logging() -> None:
    """Configure root logging once."""
    level = getattr(logging, get_settings().log_level.upper(), logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    for noisy in ("httpx", "urllib3", "openai", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
