"""Load and validate config.yaml. Values like `${VAR}` are read from the environment."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

_ENV = re.compile(r"\$\{([A-Za-z0-9_]+)\}")


class PaperlessCfg(BaseModel):
    url: str
    token: str
    consume_dir: Path


class GateCfg(BaseModel):
    inbox_dir: Path
    interval_s: int = 900
    cpu_max: float = 30.0
    gpu_max: float = 20.0
    ram_free_min_mb: int = 3000
    max_wait_days: int = 3
    feed_timeout_s: int = 1800
    metrics: Literal["auto", "jetson", "psutil"] = "auto"


class ProviderCfg(BaseModel):
    name: str
    kind: Literal["openai_compatible", "anthropic", "none"]
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    local: bool = True
    model_budget_mb: int = 0
    ollama_unload: bool = False
    timeout_s: float = 600.0


class AICfg(BaseModel):
    providers: list[ProviderCfg] = Field(default_factory=list)
    local_only: list[str] = Field(
        default_factory=lambda: ["tax", "health", "identity", "finance", "unknown"]
    )


class TargetCfg(BaseModel):
    name: str
    kind: Literal["local", "rclone"]
    path: str
    crypt: bool = False


class BackupCfg(BaseModel):
    min_copies: int = 2
    targets: list[TargetCfg] = Field(default_factory=list)


class NotifyCfg(BaseModel):
    ntfy_url: str = ""


class WebCfg(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8765
    calendar_token: str = ""
    basic_auth_user: str = ""
    basic_auth_password: str = ""


class Config(BaseModel):
    paperless: PaperlessCfg
    gate: GateCfg
    ai: AICfg = Field(default_factory=AICfg)
    backup: BackupCfg = Field(default_factory=BackupCfg)
    notify: NotifyCfg = Field(default_factory=NotifyCfg)
    web: WebCfg = Field(default_factory=WebCfg)
    rules_path: Path = Path("rules.yaml")
    state_path: Path = Path("data/state.db")
    export_dir: Path = Path("data/export")


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def load(path: Path) -> Config:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Config.model_validate(_expand_env(raw))
