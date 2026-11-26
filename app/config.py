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

    # ---- AI model -------------------------------------------------------------
    # llm_provider: auto | github_models | openai | openai_compatible | mock
    #   github_models     -> free GPT-4.1 mini via https://models.github.ai (GitHub token with Models: read)
    #   openai            -> OpenAI's paid API (OPENAI_API_KEY)
    #   openai_compatible -> any OpenAI-style server via OPENAI_BASE_URL (Groq, Ollama, Gemini ...)
    #   auto              -> openai if OPENAI_API_KEY, else github_models if GITHUB_MODELS_TOKEN, else mock
    llm_provider: str = "auto"
    openai_api_key: str = ""
    openai_base_url: str = ""
    openai_model: str = "gpt-4.1-mini"
    github_models_token: str = ""
    force_mock_llm: bool = False
    llm_fallback_to_mock: bool = True       # if the AI service fails (quota, auth), finish with offline analysis
    llm_max_tool_result_chars: int = 1500   # keeps each request inside GitHub Models' free per-request limits
    llm_max_output_tokens: int = 1500

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
    def provider(self) -> str:
        """The AI provider actually in use after applying 'auto' and missing-key rules."""
        p = (self.llm_provider or "auto").strip().lower().replace("-", "_")
        if self.force_mock_llm or p == "mock":
            return "mock"
        if p == "auto":
            if self.openai_api_key.strip():
                p = "openai"
            elif self.github_models_token.strip():
                p = "github_models"
            else:
                return "mock"
        if p not in ("github_models", "openai", "openai_compatible"):
            return "mock"
        if p != "openai_compatible" and not self.llm_api_key:
            return "mock"
        return p

    @property
    def llm_api_key(self) -> str:
        p = (self.llm_provider or "auto").strip().lower().replace("-", "_")
        if p == "github_models" or (p == "auto" and not self.openai_api_key.strip()):
            return (self.github_models_token or self.github_token).strip()
        if p == "openai_compatible":
            return self.openai_api_key.strip() or "not-needed"
        return self.openai_api_key.strip()

    @property
    def llm_base_url(self) -> str | None:
        if self.openai_base_url.strip():
            return self.openai_base_url.strip()
        if self.provider == "github_models":
            return "https://models.github.ai/inference"
        return None

    @property
    def llm_model_id(self) -> str:
        """Model name as the provider expects it (GitHub Models uses 'openai/gpt-4.1-mini')."""
        model = self.openai_model.strip()
        if self.provider == "github_models" and "/" not in model:
            return f"openai/{model}"
        return model

    @property
    def llm_label(self) -> str:
        """Human-readable description of the AI in use."""
        return {
            "mock": "Offline demo AI",
            "github_models": f"{self.openai_model} via GitHub Models (free)",
            "openai": f"{self.openai_model} via OpenAI",
            "openai_compatible": f"{self.openai_model} via {self.llm_base_url}",
        }[self.provider]

    @property
    def use_mock_llm(self) -> bool:
        """True when the agent should use the offline rule-based LLM."""
        return self.provider == "mock"

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
