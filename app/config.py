from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _load_yaml(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {}
    with open(p) as f:
        return yaml.safe_load(f) or {}


def _flatten_yaml(data: dict, prefix: str = "") -> dict[str, Any]:
    """Flatten nested YAML dict to env-style keys for pydantic-settings."""
    result: dict[str, Any] = {}
    for key, value in data.items():
        full_key = f"{prefix}{key}".upper() if not prefix else f"{prefix}__{key}".upper()
        if isinstance(value, dict):
            result.update(_flatten_yaml(value, full_key))
        else:
            result[full_key] = value
    return result


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_nested_delimiter="__",
        extra="ignore",
    )

    # Server
    server__host: str = "0.0.0.0"
    server__port: int = 8000
    server__workers: int = 4
    server__log_level: str = "info"
    server__allow_passthrough_keys: bool = True

    # LLM
    llm__default_model: str = "gpt-4o"
    llm__default_embedding_model: str = ""
    llm__allowed_models: list[str] = [
        "gpt-4o", "gpt-4o-mini",
        "claude-3-5-sonnet-20241022", "claude-haiku-4-5-20251001",
    ]
    llm__model_aliases: dict[str, str] = {}
    llm__per_model_max_tokens: dict[str, int] = {}

    # Provider keys
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    azure_openai_api_key: str = ""
    azure_openai_endpoint: str = ""

    # Database
    database_url: str = "sqlite+aiosqlite:///./proxy.db"

    # Security
    proxy_master_key: str = "change-me"

    # RAG
    rag__enabled: bool = True
    rag__top_k: int = 5
    rag__score_threshold: float = 0.75  # cosine distance; 0=identical, 1=orthogonal. 0.75 tuned for all-MiniLM-L6-v2 on mixed code+doc corpora
    rag__embedding_model: str = "all-MiniLM-L6-v2"
    rag__context_prefix: str = "Relevant internal documentation:\n\n"
    rag__context_separator: str = "\n\n---\n\n"
    chroma_persist_dir: str = "./chroma_data"
    chroma_collection_name: str = "internal_kb"
    chroma_host: str = ""        # if set, use HTTP client (multi-pod); otherwise use local PersistentClient
    chroma_port: int = 8001

    # PII
    pii__enabled: bool = True
    pii__spacy_model: str = "en_core_web_sm"
    pii__score_threshold: float = 0.7
    pii__entities: list[str] = [
        "PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER",
        "CREDIT_CARD", "US_SSN", "IP_ADDRESS",
    ]
    pii__allow_list: list[str] = []  # exact strings that should never be scrubbed (e.g. class names)

    # Rate limiting
    rate_limiting__enabled: bool = True
    rate_limiting__backend: str = "memory"
    rate_limiting__redis_url: str = ""
    rate_limiting__defaults__requests_per_minute: int = 60
    rate_limiting__defaults__tokens_per_minute: int = 100_000
    rate_limiting__defaults__tokens_per_day: int = 1_000_000

    # Content policy
    content_policy__enabled: bool = True
    content_policy__max_input_tokens: int = 32_000
    content_policy__blocked_patterns: list[str] = [
        "ignore previous instructions",
        "ignore all previous",
        "jailbreak",
    ]

    # Fallbacks — models tried in order if the primary fails or hits a context-window limit
    llm__fallback_models: list[str] = []

    # Caching (litellm.Cache)
    cache__enabled: bool = False
    cache__type: str = "local"       # "local" | "redis"
    cache__ttl: int = 3600           # seconds
    cache__redis_host: str = "localhost"
    cache__redis_port: int = 6379

    # Analytics (optional — Langfuse or other LiteLLM-supported provider)
    analytics__enabled: bool = False
    analytics__provider: str = "langfuse"  # only "langfuse" supported for now
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = ""  # empty = Langfuse cloud; set to http://langfuse:3000 for self-hosted

    # Google OAuth portal (optional — enables GET /auth/login)
    google_client_id: str = ""
    google_client_secret: str = ""
    auth_base_url: str = "http://localhost:8000"

    # Code review — repo auto-sync
    # GitHub: set token to enable. orgs/exclude are optional filters.
    code_review__github__token: str = ""
    code_review__github__include: list[str] = []   # whitelist: if set, only these repos. e.g. ["org/api", "org/core"]
    code_review__github__orgs: list[str] = []      # discover all repos in these orgs (ignored when include is set)
    code_review__github__exclude: list[str] = []   # blacklist on top of include/discovered
    code_review__github__ref: str = "main"
    # GitLab: set token to enable.
    code_review__gitlab__token: str = ""
    code_review__gitlab__host: str = "https://gitlab.com"
    code_review__gitlab__include: list[str] = []   # whitelist: project IDs or paths. e.g. ["123", "group/project"]
    code_review__gitlab__groups: list[str] = []    # discover all projects in these groups (ignored when include is set)
    code_review__gitlab__exclude: list[str] = []
    code_review__gitlab__ref: str = "main"
    code_review__sync_on_startup: bool = True  # set False when using the sync CronJob

    @property
    def host(self) -> str:
        return self.server__host

    @property
    def port(self) -> int:
        return self.server__port

    @property
    def log_level(self) -> str:
        return self.server__log_level

    @property
    def allow_passthrough_keys(self) -> bool:
        return self.server__allow_passthrough_keys

    @property
    def default_model(self) -> str:
        return self.llm__default_model

    @property
    def default_embedding_model(self) -> str:
        return self.llm__default_embedding_model

    @property
    def allowed_models(self) -> list[str]:
        return self.llm__allowed_models

    @property
    def model_aliases(self) -> dict[str, str]:
        return self.llm__model_aliases

    @property
    def rag_enabled(self) -> bool:
        return self.rag__enabled

    @property
    def rag_top_k(self) -> int:
        return self.rag__top_k

    @property
    def rag_score_threshold(self) -> float:
        return self.rag__score_threshold

    @property
    def rag_embedding_model(self) -> str:
        return self.rag__embedding_model

    @property
    def rag_context_prefix(self) -> str:
        return self.rag__context_prefix

    @property
    def rag_context_separator(self) -> str:
        return self.rag__context_separator

    @property
    def pii_enabled(self) -> bool:
        return self.pii__enabled

    @property
    def pii_spacy_model(self) -> str:
        return self.pii__spacy_model

    @property
    def pii_score_threshold(self) -> float:
        return self.pii__score_threshold

    @property
    def pii_entities(self) -> list[str]:
        return self.pii__entities

    @property
    def pii_allow_list(self) -> list[str]:
        return self.pii__allow_list

    @property
    def rate_limit_enabled(self) -> bool:
        return self.rate_limiting__enabled

    @property
    def rate_limit_backend(self) -> str:
        return self.rate_limiting__backend

    @property
    def redis_url(self) -> str:
        return self.rate_limiting__redis_url

    @property
    def default_rpm(self) -> int:
        return self.rate_limiting__defaults__requests_per_minute

    @property
    def default_tpm(self) -> int:
        return self.rate_limiting__defaults__tokens_per_minute

    @property
    def default_tpd(self) -> int:
        return self.rate_limiting__defaults__tokens_per_day

    @property
    def content_policy_enabled(self) -> bool:
        return self.content_policy__enabled

    @property
    def max_input_tokens(self) -> int:
        return self.content_policy__max_input_tokens

    @property
    def blocked_patterns(self) -> list[str]:
        return self.content_policy__blocked_patterns

    @property
    def fallback_models(self) -> list[str]:
        return self.llm__fallback_models

    @property
    def cache_enabled(self) -> bool:
        return self.cache__enabled

    @property
    def cache_type(self) -> str:
        return self.cache__type

    @property
    def cache_ttl(self) -> int:
        return self.cache__ttl

    @property
    def cache_redis_host(self) -> str:
        return self.cache__redis_host

    @property
    def cache_redis_port(self) -> int:
        return self.cache__redis_port

    @property
    def analytics_enabled(self) -> bool:
        return self.analytics__enabled

    @property
    def analytics_provider(self) -> str:
        return self.analytics__provider

    @property
    def oauth_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def github_token(self) -> str:
        return self.code_review__github__token

    @property
    def github_include(self) -> list[str]:
        return self.code_review__github__include

    @property
    def github_orgs(self) -> list[str]:
        return self.code_review__github__orgs

    @property
    def github_exclude(self) -> list[str]:
        return self.code_review__github__exclude

    @property
    def github_ref(self) -> str:
        return self.code_review__github__ref

    @property
    def gitlab_token(self) -> str:
        return self.code_review__gitlab__token

    @property
    def gitlab_host(self) -> str:
        return self.code_review__gitlab__host

    @property
    def gitlab_include(self) -> list[str]:
        return self.code_review__gitlab__include

    @property
    def gitlab_groups(self) -> list[str]:
        return self.code_review__gitlab__groups

    @property
    def gitlab_exclude(self) -> list[str]:
        return self.code_review__gitlab__exclude

    @property
    def gitlab_ref(self) -> str:
        return self.code_review__gitlab__ref

    @property
    def sync_on_startup(self) -> bool:
        return self.code_review__sync_on_startup


@lru_cache
def get_settings() -> Settings:
    yaml_data = _load_yaml(
        os.environ.get("CONFIG_FILE", "config/config.yaml")
    )
    flat = _flatten_yaml(yaml_data)
    # Seed environment with YAML values (env vars still take priority)
    for k, v in flat.items():
        if k not in os.environ and v is not None:
            os.environ[k] = json.dumps(v) if isinstance(v, (list, dict)) else str(v)
    return Settings()
