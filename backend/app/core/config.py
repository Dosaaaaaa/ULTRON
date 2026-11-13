"""Environment-driven configuration. No secrets live in source; see ../../.env.example."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

RoutingPolicy = Literal["auto", "cloud_first", "local_first", "local_only"]

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
MIN_TOKEN_LENGTH = 24

# backend/app/core/config.py -> repository root is three levels above "app"
_REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ULTRON_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- server / auth -------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"
    api_token: SecretStr | None = None
    auth_disabled: bool = False  # only honoured on a loopback host
    rate_limit_per_minute: int = 30

    # --- persistence ---------------------------------------------------
    database_url: str = "sqlite+aiosqlite:///./data/ultron.db"
    auto_migrate: bool = True

    # --- routing -------------------------------------------------------
    routing_policy: RoutingPolicy = "auto"
    allow_cloud: bool = True  # master privacy switch; False => nothing leaves the device

    # --- cloud adapter (any OpenAI-compatible chat-completions endpoint) -
    cloud_provider_name: str = "openai-compatible"
    cloud_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    cloud_api_key: SecretStr | None = None
    cloud_model: str = ""
    cloud_timeout_s: float = 60.0
    # Provider quota, spent deliberately (app/models/budget.py). 0 = unknown: the provider's own
    # 429 is then the only guard. Limits belong to the provider ORGANISATION, not to an API key.
    cloud_limit_tokens_per_day: int = 0
    cloud_limit_tokens_per_minute: int = 0
    cloud_limit_requests_per_day: int = 0
    cloud_limit_requests_per_minute: int = 0
    cloud_budget_safety: float = Field(default=0.9, gt=0.1, le=1.0)
    cloud_budget_file: Path = Path("./data/cloud_budget.json")

    # --- local adapter (Ollama) ------------------------------------------
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:4b-instruct"  # Qwen3-4B-Instruct-2507: non-thinking
    ollama_timeout_s: float = 120.0
    # The default model is the non-thinking Instruct variant (first token ~0.5 s). The plain
    # `qwen3:4b` tag is the Thinking-2507 variant: it always reasons (90%+ of output tokens, 23-99 s
    # per reply on a GTX 1650); use it with ULTRON_OLLAMA_THINK=true so Ollama separates reasoning.
    # Non-thinking/non-reasoning models (instruct, llama3, moondream) need think=false.
    ollama_think: bool = False

    # --- memory (Phase 4) -------------------------------------------------
    # Embeddings are made by the LOCAL Ollama only: memory text is never sent to a cloud service
    # to be embedded. Retrieved memories reach a cloud model only if they are not local_only.
    memory_enabled: bool = True
    memory_allow_cloud: bool = True  # False: no memory text is ever put in a cloud prompt
    embedding_model: str = "nomic-embed-text"
    embedding_timeout_s: float = 4.0
    memory_top_k: int = Field(default=5, ge=1, le=20)
    # Measured on nomic-embed-text (scripts/eval_memory_retrieval.py): no single floor separates
    # relevant from irrelevant cleanly, so the floor is stricter when the text leaves the device.
    memory_min_similarity: float = Field(default=0.55, ge=0.0, le=1.0)  # on-device engine
    memory_min_similarity_cloud: float = Field(default=0.60, ge=0.0, le=1.0)  # cloud engine
    memory_dedupe_similarity: float = Field(default=0.92, ge=0.5, le=1.0)
    memory_prompt_chars: int = Field(default=1200, ge=200, le=6000)  # budget for the prompt block
    memory_max_chars: int = Field(default=600, ge=20, le=4000)  # one memory's maximum length
    memory_episodic_retention_days: int = Field(default=90, ge=0)  # 0 = keep until deleted
    memory_extract_max: int = Field(default=5, ge=1, le=20)  # proposals per extraction

    # --- reasoning pass (docs/INTELLIGENCE.md) ----------------------------------------------
    # For problem-like messages (arithmetic, dates, logic, code tracing, counting) the model first
    # solves the problem privately, step by step; the reply is then written with that working.
    # It uses the normal router, so privacy rules and the cloud budget apply to it as to any reply.
    reasoning: Literal["off", "auto"] = "auto"
    reasoning_voice: bool = True  # also for spoken replies (adds a few seconds on those questions)
    reasoning_max_tokens: int = Field(default=800, ge=100, le=4000)

    # --- web search (off unless a provider is configured; docs/INTELLIGENCE.md) ------------
    # The operator's current message is sent to this provider when a search is made. Never used in
    # a local_only conversation, and disabled by ULTRON_ALLOW_CLOUD=false (nothing leaves the box).
    web_search_provider: Literal["none", "brave", "searxng"] = "none"
    web_search_api_key: SecretStr | None = None  # Brave Search API key
    web_search_url: str = ""  # base URL of a SearXNG instance you run
    web_search_count: int = Field(default=5, ge=1, le=10)
    web_search_timeout_s: float = Field(default=5.0, gt=0, le=30)
    web_search_max_per_day: int = Field(default=50, ge=1)
    web_search_auto: bool = True  # also search recent-sounding questions, not only "search for ..."

    # --- phone / devices -------------------------------------------------
    # PEM certificate the backend is served with when it listens on the home network (see
    # desktop/packaging/make_lan_cert.sh). Its SHA-256 fingerprint is given to the phone at pairing
    # so it can pin it; without TLS the token would cross the Wi-Fi in clear text.
    tls_cert_file: Path | None = None
    scheduler_enabled: bool = True  # delivers due reminders to paired devices (every 10 s)

    # --- conversation ----------------------------------------------------
    personality_version: int = 4  # older versions kept; v4 = compact cloud text + longer local text
    prompts_dir: Path = _REPO_ROOT / "shared" / "prompts"
    max_context_messages: int = 20
    # Older turns beyond this many characters are left out of the prompt (about 4 characters per
    # token). The newest turns are kept whole; the stored conversation is never trimmed.
    history_char_budget: int = Field(default=8000, ge=500)
    max_output_tokens: int = 2048
    voice_max_output_tokens: int = 300  # spoken replies are short by design
    temperature: float = 0.6
    # Enforce "no emoji, no exclamation marks" in code for every engine (app/core/style.py).
    enforce_style: bool = True

    @field_validator("api_token", "cloud_api_key", "web_search_api_key", mode="before")
    @classmethod
    def _blank_secret_is_none(cls, v: object) -> object:
        return None if isinstance(v, str) and not v.strip() else v

    @model_validator(mode="after")
    def _check_auth(self) -> Settings:
        token = self.api_token.get_secret_value() if self.api_token else None
        if self.auth_disabled:
            if self.host not in LOOPBACK_HOSTS:
                raise ValueError("ULTRON_AUTH_DISABLED is only allowed on a loopback host")
        elif token is None:
            raise ValueError(
                "ULTRON_API_TOKEN is required (min "
                f"{MIN_TOKEN_LENGTH} chars). Generate one with: "
                'python -c "import secrets; print(secrets.token_urlsafe(32))"'
            )
        elif len(token) < MIN_TOKEN_LENGTH:
            raise ValueError(f"ULTRON_API_TOKEN must be at least {MIN_TOKEN_LENGTH} characters")
        return self

    @model_validator(mode="after")
    def _check_web_search(self) -> Settings:
        if self.web_search_provider == "brave" and self.web_search_api_key is None:
            raise ValueError("ULTRON_WEB_SEARCH_PROVIDER=brave needs ULTRON_WEB_SEARCH_API_KEY")
        if self.web_search_provider == "searxng" and not self.web_search_url.startswith(
            ("http://", "https://")
        ):
            raise ValueError(
                "ULTRON_WEB_SEARCH_PROVIDER=searxng needs ULTRON_WEB_SEARCH_URL (http/https)"
            )
        return self

    @property
    def cloud_configured(self) -> bool:
        return bool(self.cloud_api_key and self.cloud_model and self.cloud_base_url)

    def secret_values(self) -> list[str]:
        """Every secret that must never reach a log line."""
        out = []
        for s in (self.api_token, self.cloud_api_key, self.web_search_api_key):
            if s is not None:
                out.append(s.get_secret_value())
        return out


@lru_cache
def get_settings() -> Settings:
    return Settings()
