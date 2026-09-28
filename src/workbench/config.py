from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from ruamel.yaml import YAML


class CommitMode(str, Enum):
    IMMEDIATE = "immediate"
    DEBOUNCE = "debounce"
    MANUAL = "manual"


class GitConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    commit_mode: CommitMode = CommitMode.DEBOUNCE
    debounce_seconds: float = Field(default=30.0, ge=0.05, le=3600)
    author_name: str = "Workbench"
    author_email: str = "workbench@localhost"
    push_after_commit: bool = False
    remote: str = "origin"


class WorkbenchConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    git: GitConfig = Field(default_factory=GitConfig)


def load_config(root: Path) -> WorkbenchConfig:
    path = root / ".workbench" / "config.yml"
    data: dict[str, Any] = {}
    if path.exists():
        loaded = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data = loaded

    git = dict(data.get("git") or {})
    environment_overrides = {
        "commit_mode": os.getenv("WORKBENCH_COMMIT_MODE"),
        "debounce_seconds": os.getenv("WORKBENCH_DEBOUNCE_SECONDS"),
        "author_name": os.getenv("WORKBENCH_GIT_AUTHOR_NAME"),
        "author_email": os.getenv("WORKBENCH_GIT_AUTHOR_EMAIL"),
        "push_after_commit": os.getenv("WORKBENCH_GIT_PUSH_AFTER_COMMIT"),
        "remote": os.getenv("WORKBENCH_GIT_REMOTE"),
    }
    git.update(
        {
            key: value
            for key, value in environment_overrides.items()
            if value is not None
        }
    )
    data["git"] = git
    return WorkbenchConfig.model_validate(data)
