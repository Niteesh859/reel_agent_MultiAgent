"""Load config/settings.yaml + .env into one validated Settings object."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, field_validator

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SETTINGS_PATH = REPO_ROOT / "config" / "settings.yaml"
OUTPUT_DIR = REPO_ROOT / "output"


class PipelineCfg(BaseModel):
    target_length_sec: int = 45
    tone: str = "casual, energetic explainer — like a friend hyping you up"
    platform: str = "Instagram Reel, vertical 9:16, 30-60 seconds"


class LoopCfg(BaseModel):
    min_rounds: int = Field(default=3, ge=2)
    max_rounds: int = Field(default=5, ge=2)


class ModelsCfg(BaseModel):
    researcher: str
    strategist: str
    writer: str


class LLMCfg(BaseModel):
    base_url: str = "https://openrouter.ai/api/v1"
    max_output_tokens: int = 8000
    request_timeout_sec: float = Field(default=240.0, gt=0)
    json_retries: int = 2
    force_json_mode: bool = True
    require_json_capable_provider: bool = True
    temperatures: dict[str, float] = Field(
        default_factory=lambda: {"researcher": 0.3, "strategist": 0.7, "writer": 0.8}
    )
    # Per-agent thinking budget (OpenRouter unified `reasoning.effort`). Agents
    # absent from the dict use the model's default reasoning behavior.
    reasoning_efforts: dict[str, str] = Field(default_factory=dict)

    @field_validator("reasoning_efforts")
    @classmethod
    def _valid_efforts(cls, v: dict[str, str]) -> dict[str, str]:
        allowed = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
        for agent, effort in v.items():
            if effort not in allowed:
                raise ValueError(
                    f"reasoning_efforts.{agent}: '{effort}' not in {sorted(allowed)}"
                )
        return v


class ResearchCfg(BaseModel):
    max_facts: int = 10
    max_search_queries: int = 6
    tavily_max_results: int = 5
    search_depth: str = "basic"
    chunks_per_source: int = Field(default=3, ge=1, le=3)  # advanced depth only
    source_content_chars: int = Field(default=1500, gt=0)

    @field_validator("search_depth")
    @classmethod
    def _valid_depth(cls, v: str) -> str:
        if v not in {"basic", "advanced"}:
            raise ValueError("search_depth must be 'basic' or 'advanced'")
        return v


class IOCfg(BaseModel):
    telegram_poll_timeout_sec: int = 25


class Settings(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    pipeline: PipelineCfg = PipelineCfg()
    loop: LoopCfg = LoopCfg()
    models: ModelsCfg
    llm: LLMCfg = LLMCfg()
    research: ResearchCfg = ResearchCfg()
    io_cfg: IOCfg = Field(default=IOCfg(), alias="io")


def load_settings(path: Path | None = None) -> Settings:
    load_dotenv(REPO_ROOT / ".env")
    raw = yaml.safe_load((path or DEFAULT_SETTINGS_PATH).read_text())
    return Settings.model_validate(raw)


def env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def missing_required_keys() -> list[str]:
    return [k for k in ("OPENROUTER_API_KEY", "TAVILY_API_KEY") if env(k) is None]


def telegram_configured() -> bool:
    return env("TELEGRAM_BOT_TOKEN") is not None and env("TELEGRAM_CHAT_ID") is not None


def langsmith_enabled() -> bool:
    return (env("LANGSMITH_TRACING") or "").lower() in {"1", "true", "yes"}
