import json
from pathlib import Path

import httpx
import respx
from typer.testing import CliRunner

from local_paper_ai.cli import app

ROOT = Path(__file__).resolve().parents[1]


def _config(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(
        f'paperless: {{url: "http://pl", token: t, consume_dir: "{tmp_path / "consume"}"}}\n'
        f'gate: {{inbox_dir: "{tmp_path / "inbox"}", metrics: psutil}}\n'
        f'rules_path: "{ROOT / "rules.yaml"}"\n'
        f'state_path: "{tmp_path / "state.db"}"\n'
        f'export_dir: "{tmp_path / "export"}"\n'
    )
    return path


def test_gate_status(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["gate-status", "-c", str(_config(tmp_path))])
    assert result.exit_code == 0, result.output
    assert "idle:" in result.output


def test_analyze_dry_run_does_not_write(tmp_path: Path, respx_mock: respx.MockRouter) -> None:
    respx_mock.get("http://pl/api/documents/5/").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 5,
                "title": "Auszug",
                "content": "Ihr Kontoauszug Nr. 3",
                "created": "2026-10-01",
                "correspondent": None,
                "document_type": None,
                "tags": [],
                "custom_fields": [],
            },
        )
    )
    result = CliRunner().invoke(app, ["analyze", "5", "-c", str(_config(tmp_path))])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["verdict"]["kind"] == "may_shred"
    assert not any(call.request.method == "PATCH" for call in respx_mock.calls)


def test_doctor_reports_unreachable_paperless(tmp_path: Path, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(path="/api/documents/").mock(side_effect=httpx.ConnectError("down"))
    result = CliRunner().invoke(app, ["doctor", "-c", str(_config(tmp_path))])
    assert result.exit_code == 1
    assert "✗ Paperless erreichbar" in result.output
    assert "✓ Regeln gültig" in result.output
