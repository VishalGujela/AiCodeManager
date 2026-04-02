"""Application configuration via pydantic-settings."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central settings; override with env vars or `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_version: str = Field(default="0.1.0", validation_alias="APP_VERSION")
    environment: str = Field(default="development", validation_alias="ENVIRONMENT")
    debug: bool = Field(default=True, validation_alias="DEBUG")
    port: int = Field(default=8000, validation_alias="PORT")
    secret_key: str = Field(default="change_me", validation_alias="SECRET_KEY")

    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    log_format: Literal["json", "text"] = Field(default="json", validation_alias="LOG_FORMAT")

    sandbox_dir: Path = Field(default=Path("sandbox"), validation_alias="SANDBOX_DIR")
    execution_timeout_seconds: int = Field(default=30, validation_alias="EXECUTION_TIMEOUT_SECONDS")
    pylint_score_threshold: float = Field(default=7.0, validation_alias="PYLINT_SCORE_THRESHOLD")
    mypy_strict: bool = Field(default=False, validation_alias="MYPY_STRICT")

    git_author_name: str = Field(default="AI Code Manager", validation_alias="GIT_AUTHOR_NAME")
    git_author_email: str = Field(
        default="ai@codemanager.local",
        validation_alias="GIT_AUTHOR_EMAIL",
    )

    # Docker sandbox (code execution, pytest, pylint/mypy)
    docker_enabled: bool = Field(default=False, validation_alias="DOCKER_ENABLED")
    docker_image: str = Field(
        default="ai_code_executor:latest",
        validation_alias="DOCKER_IMAGE",
    )
    docker_bootstrap_image: str = Field(
        default="python:3.10-slim",
        validation_alias="DOCKER_BOOTSTRAP_IMAGE",
    )
    docker_node_image: str = Field(
        default="node:20-alpine",
        validation_alias="DOCKER_NODE_IMAGE",
    )
    docker_memory: str = Field(default="256m", validation_alias="DOCKER_MEMORY")
    docker_cpus: str = Field(default="0.5", validation_alias="DOCKER_CPUS")
    docker_network: str = Field(default="", validation_alias="DOCKER_NETWORK")
    docker_code_timeout_seconds: int = Field(
        default=10,
        ge=1,
        le=600,
        validation_alias="DOCKER_CODE_TIMEOUT_SECONDS",
    )
    docker_pytest_timeout_seconds: int = Field(
        default=300,
        ge=10,
        le=3600,
        validation_alias="DOCKER_PYTEST_TIMEOUT_SECONDS",
    )
    docker_analyze_timeout_seconds: int = Field(
        default=90,
        ge=5,
        le=600,
        validation_alias="DOCKER_ANALYZE_TIMEOUT_SECONDS",
    )

    # AI fix suggestions (OpenAI-compatible Chat Completions HTTP API)
    ai_enabled: bool = Field(default=False, validation_alias="AI_ENABLED")
    openai_api_key: str = Field(default="", validation_alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", validation_alias="OPENAI_MODEL")
    openai_api_base: str = Field(
        default="https://api.openai.com/v1",
        validation_alias="OPENAI_API_BASE",
    )
    ai_timeout_seconds: float = Field(
        default=10.0,
        ge=1.0,
        le=120.0,
        validation_alias="AI_TIMEOUT_SECONDS",
    )
    openai_json_mode: bool = Field(default=True, validation_alias="OPENAI_JSON_MODE")


@lru_cache
def get_settings() -> Settings:
    return Settings()
