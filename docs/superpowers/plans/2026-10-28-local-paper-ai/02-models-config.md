### Task 2: Domain models and config

**Files:**
- Create: `src/local_paper_ai/models.py`, `src/local_paper_ai/config.py`, `config.example.yaml`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces (`models`): `VerdictKind = Literal["may_shred","keep_original","keep_until"]`, `EvidenceSource = Literal["rule","ai","regex","manual"]`, pydantic models `Document(id, title, content, created: date, correspondent: str|None, document_type: str|None, tags: list[str], page_count: int|None)`, `Evidence(quote, source, detail="")`, `Verdict(kind, keep_until: date|None, category="unknown", evidence: list[Evidence], needs_review=False)`, `Deadline(due: date, label, evidence: Evidence)`, `Analysis(doc_id, verdict, deadlines: list[Deadline])`.
- Produces (`config`): `Config` with sections `paperless: PaperlessCfg`, `gate: GateCfg`, `ai: AICfg`, `backup: BackupCfg`, `notify: NotifyCfg`, `web: WebCfg`, and the paths `rules_path`, `state_path`, `export_dir`; `ProviderCfg`; `TargetCfg`; `load(path: Path) -> Config`.

- [ ] **Step 1: Write the failing test** `tests/test_config.py`

```python
from pathlib import Path

import pytest
from pydantic import ValidationError

from local_paper_ai.config import load

ROOT = Path(__file__).resolve().parents[1]


def test_example_config_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERLESS_TOKEN", "abc")
    cfg = load(ROOT / "config.example.yaml")
    assert cfg.paperless.token == "abc"
    assert cfg.gate.interval_s == 900
    assert cfg.ai.providers[0].kind == "openai_compatible"
    assert all(p.local for p in cfg.ai.providers)
    assert "unknown" in cfg.ai.local_only
    assert cfg.backup.min_copies == 2


def test_missing_env_becomes_empty_and_defaults_apply(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("LPA_NOPE", raising=False)
    path = tmp_path / "c.yaml"
    path.write_text(
        "paperless: {url: 'http://x', token: '${LPA_NOPE}', consume_dir: /c}\n"
        "gate: {inbox_dir: /i}\n"
    )
    cfg = load(path)
    assert cfg.paperless.token == ""
    assert cfg.web.port == 8765
    assert cfg.ai.providers == []
    assert cfg.ai.local_only == ["tax", "health", "identity", "finance", "unknown"]


def test_invalid_provider_kind_rejected(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_text(
        "paperless: {url: 'http://x', token: t, consume_dir: /c}\n"
        "gate: {inbox_dir: /i}\n"
        "ai: {providers: [{name: x, kind: magic}]}\n"
    )
    with pytest.raises(ValidationError):
        load(path)
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'local_paper_ai.config'`

- [ ] **Step 3: Implement** `src/local_paper_ai/models.py`

```python
"""Domain types shared by all modules."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

VerdictKind = Literal["may_shred", "keep_original", "keep_until"]
EvidenceSource = Literal["rule", "ai", "regex", "manual"]


class Document(BaseModel):
    id: int
    title: str
    content: str
    created: date
    correspondent: str | None = None
    document_type: str | None = None
    tags: list[str] = Field(default_factory=list)
    page_count: int | None = None


class Evidence(BaseModel):
    quote: str
    source: EvidenceSource
    detail: str = ""


class Verdict(BaseModel):
    kind: VerdictKind
    keep_until: date | None = None
    category: str = "unknown"
    evidence: list[Evidence] = Field(default_factory=list)
    needs_review: bool = False


class Deadline(BaseModel):
    due: date
    label: str
    evidence: Evidence


class Analysis(BaseModel):
    doc_id: int
    verdict: Verdict
    deadlines: list[Deadline] = Field(default_factory=list)
```

`src/local_paper_ai/config.py`:
```python
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
```

`config.example.yaml`:
```yaml
# local-paper-ai configuration. Copy to config.yaml and adjust.
# ${VAR} values are read from the environment (see .env.example).
paperless:
  url: http://paperless:8000
  token: ${PAPERLESS_TOKEN}
  consume_dir: /data/consume

gate:                      # only work when the box is idle
  inbox_dir: /data/inbox   # Syncthing drops phone scans here
  interval_s: 900
  cpu_max: 30              # percent
  gpu_max: 20              # percent
  ram_free_min_mb: 3000    # FREE (not available) RAM on Jetson
  max_wait_days: 3         # older scans ignore CPU/GPU limits (RAM still required)
  metrics: auto            # auto | jetson | psutil

ai:
  local_only: [tax, health, identity, finance, unknown]   # never sent to cloud providers
  providers:
    - name: ollama
      kind: openai_compatible
      base_url: http://ollama:11434/v1
      model: phi4-mini
      local: true
      model_budget_mb: 3500
      ollama_unload: true
    # - name: claude            # optional cloud provider, off by default
    #   kind: anthropic
    #   model: claude-sonnet-5-5
    #   api_key: ${ANTHROPIC_API_KEY}
    #   local: false

backup:
  min_copies: 2            # the Paperless archive counts as one
  targets:
    - name: syncthing
      kind: local
      path: /data/export-sync
    # - name: cloud
    #   kind: rclone
    #   path: "mycrypt:local-paper-ai"
    #   crypt: true

notify:
  ntfy_url: ""             # e.g. https://ntfy.sh/your-secret-topic

web:
  host: 0.0.0.0
  port: 8765
  calendar_token: ${LPA_CALENDAR_TOKEN}
  basic_auth_user: ""
  basic_auth_password: ""

rules_path: /app/rules.yaml
state_path: /data/lpa/state.db
export_dir: /data/lpa/export
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_config.py -v`
Expected: `3 passed`

- [ ] **Step 5: Commit and push**

```bash
git add -A
git commit -m "feat: domain models and config loader

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push
```
