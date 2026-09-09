from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
DEFAULT_FRONTEND_ORIGIN = "https://autonomous-decision-intelligence-en.vercel.app"
DEFAULT_VERCEL_ORIGIN_REGEX = r"^https://autonomous-decision-intelligence-en(?:-[a-z0-9-]+)?\.vercel\.app$"
DEFAULT_LOCAL_ORIGIN_REGEX = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ENV_FILE), env_file_encoding="utf-8", extra="ignore")

    # SQLite is useful for an entirely local first run.  Production must use a
    # managed Postgres database: Render's filesystem is deliberately ephemeral.
    database_url: str = "sqlite+aiosqlite:///./decision_intelligence.db"
    redis_url: str = "redis://localhost:6379/0"
    openai_api_key: str = ""
    clerk_secret_key: str = ""
    clerk_publishable_key: str = ""
    clerk_issuer: str = ""
    encryption_key: str = ""
    sentry_dsn: str = ""
    environment: str = "development"
    log_level: str = "INFO"
    allowed_origins: str = Field(
        default=f"http://localhost:3000,http://localhost:3002,http://localhost:3003,{DEFAULT_FRONTEND_ORIGIN}"
    )
    allowed_origin_regex: str = ""
    max_query_rows: int = Field(default=1000, ge=1, le=5000)
    database_pool_size: int = Field(default=2, ge=1, le=5)
    database_max_overflow: int = Field(default=1, ge=0, le=3)
    database_pool_timeout: int = Field(default=10, ge=1, le=30)
    database_pool_recycle_seconds: int = Field(default=1800, ge=60, le=3600)
    database_connect_timeout_seconds: float = Field(default=10.0, gt=0, le=30)
    query_timeout_seconds: float = Field(default=30.0, gt=0, le=60)
    agent_max_iterations: int = Field(default=5, ge=1, le=10)
    agent_timeout_seconds: float = Field(default=120.0, gt=0, le=300)
    max_concurrent_agent_runs: int = Field(default=2, ge=1, le=4)
    max_concurrent_db_queries: int = Field(default=2, ge=1, le=4)
    max_query_result_bytes: int = Field(default=2 * 1024 * 1024, ge=64 * 1024, le=5 * 1024 * 1024)
    schema_max_tables: int = Field(default=100, ge=1, le=250)
    schema_timeout_seconds: float = Field(default=20.0, gt=0, le=60)
    max_schema_prompt_chars: int = Field(default=50_000, ge=5_000, le=100_000)
    max_document_chunks: int = Field(default=200, ge=1, le=500)
    max_in_memory_chunks: int = Field(default=2_000, ge=100, le=10_000)
    max_artifacts_per_session: int = Field(default=50, ge=5, le=200)
    max_local_cache_entries: int = Field(default=500, ge=50, le=2_000)
    max_specialist_rows: int = Field(default=500, ge=25, le=2_000)
    max_specialist_columns: int = Field(default=5, ge=1, le=20)
    rate_limit_requests: int = Field(default=20, ge=1, le=120)
    cache_ttl_schema: int = Field(default=3600, ge=60, le=86400)
    cache_ttl_query: int = Field(default=300, ge=1, le=3600)
    llm_timeout_seconds: float = Field(default=30.0, gt=0, le=60)
    llm_model: str = "gpt-4o-mini-2024-07-18"
    # Ollama is an opt-in local-LLM fallback tier. Leave empty to disable.
    # When set, the LLM service tries OpenAI first (if a key is present), then
    # this local endpoint, and finally the deterministic offline fallback.
    ollama_base_url: str = ""
    ollama_model: str = "llama3.2"
    # When true, row-level values are redacted (PII columns masked, long strings
    # truncated) before being serialized into LLM prompts.
    redact_pii_in_prompts: bool = True
    auth_bypass: bool = False
    # Uploads are temporary working files, never application persistence.
    uploads_dir: str = "/tmp/ask-database-uploads"
    max_upload_bytes: int = Field(default=10 * 1024 * 1024, ge=1024, le=20 * 1024 * 1024)
    # Chroma vector store for RAG (optional). Leave chroma_host empty to use
    # the in-process fallback store.
    chroma_host: str = ""
    chroma_port: int = 8000

    @model_validator(mode="after")
    def parse_allowed_origins(self) -> "Settings":
        if isinstance(self.allowed_origins, str):
            self.allowed_origins = [
                origin.strip()
                for origin in self.allowed_origins.split(",")
                if origin.strip()
            ]
        if not self.allowed_origin_regex:
            if self.environment.lower() == "development":
                self.allowed_origin_regex = f"{DEFAULT_LOCAL_ORIGIN_REGEX}|{DEFAULT_VERCEL_ORIGIN_REGEX}"
            else:
                self.allowed_origin_regex = DEFAULT_VERCEL_ORIGIN_REGEX
        return self

    @model_validator(mode="after")
    def normalize_database_url(self) -> "Settings":
        """Normalize provider URLs; never silently replace a production DB."""
        if self.database_url.startswith("postgres://"):
            self.database_url = "postgresql+asyncpg://" + self.database_url.removeprefix("postgres://")
        elif self.database_url.startswith("postgresql://"):
            self.database_url = "postgresql+asyncpg://" + self.database_url.removeprefix("postgresql://")
        if self.environment.lower() == "production" and self.database_url.startswith("sqlite"):
            raise ValueError("DATABASE_URL must point to managed PostgreSQL in production; SQLite is ephemeral on Render.")
        if self.environment.lower() == "production" and not self.encryption_key:
            raise ValueError("ENCRYPTION_KEY is required in production to protect database credentials at rest.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
