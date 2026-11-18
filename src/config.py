from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class JobConfig(BaseModel):
    """Configuration for a single scheduled notification job."""

    name: str
    type: str
    schedule: str
    enabled: bool = True
    target_chat_id: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class AppConfig(BaseModel):
    """Top-level configuration loaded from jobs.yaml."""

    jobs: list[JobConfig] = Field(default_factory=list)


def load_config(path: str | Path = "jobs.yaml") -> AppConfig:
    """Load and validate job definitions from a YAML file."""
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return AppConfig(**raw)


def save_config(config: AppConfig, path: str | Path = "jobs.yaml") -> None:
    """Persist job definitions back to a YAML file."""
    data = {"jobs": [job.model_dump(exclude_none=True) for job in config.jobs]}
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)

