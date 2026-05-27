"""
config.py
─────────
Centralised configuration for the university QA agent.

All parameters have typed defaults matching previous hardcoded values,
so behaviour is unchanged unless a value is explicitly overridden via
environment variable.

Env var override pattern (env_nested_delimiter="__"):
  CACHE__MAX_SIZE=256
  LLM__SQL_GENERATION__MODEL=gpt-4o-mini
  DATABASE_URL=sqlite:///other.db   (flat name, backward compat)
"""

from __future__ import annotations

import os

from pydantic import BaseModel, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Anthropic default — kept here for reference when switching providers.
_ANTHROPIC_DEFAULT = "claude-sonnet-4-20250514"


class LLMStepConfig(BaseModel):
    model: str = "gpt-4o"
    temperature: float = 0.0


class LLMConfig(BaseModel):
    provider: str = "auto"  # "openai" | "anthropic" | "auto"
    sql_generation: LLMStepConfig = LLMStepConfig(model="gpt-4o", temperature=0.0)
    retry: LLMStepConfig = LLMStepConfig(model="gpt-4o", temperature=0.3)
    answer: LLMStepConfig = LLMStepConfig(model="gpt-4o", temperature=0.2)
    relevance: LLMStepConfig = LLMStepConfig(model="gpt-4o", temperature=0.0)


class AgentConfig(BaseModel):
    max_retries: int = 3


class CacheConfig(BaseModel):
    max_size: int = 128
    ttl_seconds: int = 3600


class ConversationConfig(BaseModel):
    max_history: int = 5


class DatabaseConfig(BaseModel):
    url: str = "sqlite:///university.db"
    echo: bool = False

    @model_validator(mode="before")
    @classmethod
    def _load_database_url(cls, values):
        """Pick up DATABASE_URL (flat name) for backward compat with existing .env files."""
        if isinstance(values, dict) and "url" not in values:
            env_url = os.getenv("DATABASE_URL")
            if env_url:
                values["url"] = env_url
        return values


class ApiConfig(BaseModel):
    title: str = "University QA Trace UI"
    version: str = "1.0.0"
    question_max_length: int = 1000


class PromptConfig(BaseModel):
    domain: str = "university"
    hub_enabled: bool = False
    hub_prefix: str = "genpact-university-qa"
    hub_tag: str = "production"


class EvalConfig(BaseModel):
    """Configuration for the LLM-as-a-Judge evaluation pipeline."""
    judge_model: str = "gpt-4o"
    judge_temperature: float = 0.0
    results_dir: str = "evaluation_runs"


class Settings(BaseSettings):
    llm: LLMConfig = LLMConfig()
    agent: AgentConfig = AgentConfig()
    cache: CacheConfig = CacheConfig()
    conversation: ConversationConfig = ConversationConfig()
    database: DatabaseConfig = DatabaseConfig()
    api: ApiConfig = ApiConfig()
    prompt: PromptConfig = PromptConfig()
    eval: EvalConfig = EvalConfig()

    @model_validator(mode="after")
    def _apply_flat_database_url(self):
        """Let flat DATABASE_URL override only the default database URL."""
        env_url = os.getenv("DATABASE_URL")
        default_url = DatabaseConfig.model_fields["url"].default
        if env_url and self.database.url == default_url:
            self.database.url = env_url
        return self

    model_config = SettingsConfigDict(
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
