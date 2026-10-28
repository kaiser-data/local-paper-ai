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
