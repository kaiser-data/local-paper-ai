"""Command line: run the worker, serve the web UI, inspect and diagnose."""

from __future__ import annotations

import functools
import logging
import shutil
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer
import uvicorn

from local_paper_ai.ai.router import build_router
from local_paper_ai.analyzer import analyze as analyze_document
from local_paper_ai.backup import build_targets, sync
from local_paper_ai.config import Config, load
from local_paper_ai.idle_gate import is_idle
from local_paper_ai.metrics import make_source
from local_paper_ai.paperless import PaperlessClient
from local_paper_ai.rules import load_rules
from local_paper_ai.state import State
from local_paper_ai.web.app import create_app
from local_paper_ai.worker import build_context, run_cycle, run_forever
from local_paper_ai.writer import apply

app = typer.Typer(help="local-paper-ai: darf das Papier weg?", no_args_is_help=True)
ConfigOpt = Annotated[
    Path, typer.Option("--config", "-c", envvar="LPA_CONFIG", help="Pfad zur config.yaml")
]
DEFAULT_CONFIG = Path("config.yaml")


@app.callback()
def main(verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _client(cfg: Config) -> PaperlessClient:
    return PaperlessClient(cfg.paperless.url, cfg.paperless.token)


@app.command()
def run(config: ConfigOpt = DEFAULT_CONFIG) -> None:
    """Worker dauerhaft laufen lassen."""
    run_forever(build_context(load(config)))


@app.command()
def once(config: ConfigOpt = DEFAULT_CONFIG) -> None:
    """Genau einen Durchlauf ausführen."""
    typer.echo(run_cycle(build_context(load(config)), date.today(), time.time()))


@app.command("gate-status")
def gate_status(config: ConfigOpt = DEFAULT_CONFIG) -> None:
    """Aktuelle Last anzeigen und ob gerade gearbeitet würde."""
    cfg = load(config)
    m = make_source(cfg.gate.metrics).read()
    typer.echo(
        f"cpu {m.cpu_pct:.0f}% · gpu {m.gpu_pct:.0f}% · frei {m.ram_free_mb} MB "
        f"→ idle: {is_idle(m, cfg.gate)}"
    )


@app.command()
def analyze(
    doc_id: int,
    config: ConfigOpt = DEFAULT_CONFIG,
    write: Annotated[bool, typer.Option("--write/--dry-run")] = False,
) -> None:
    """Ein Dokument analysieren und das Ergebnis als JSON zeigen."""
    cfg = load(config)
    client = _client(cfg)
    with httpx.Client() as http:
        metrics = make_source(cfg.gate.metrics)
        router = build_router(cfg.ai, http, metrics.free_mb)
        doc = client.get_document(doc_id)
        result = analyze_document(doc, load_rules(cfg.rules_path), router)
        if write:
            apply(client, result, date.today())
            State(cfg.state_path).save_analysis(doc, result)
    typer.echo(result.model_dump_json(indent=2))


@app.command()
def backup(config: ConfigOpt = DEFAULT_CONFIG) -> None:
    """Backup-Ziele jetzt synchronisieren."""
    cfg = load(config)
    report = sync(_client(cfg), State(cfg.state_path), build_targets(cfg.backup), cfg.export_dir)
    typer.echo(report)


@app.command()
def serve(config: ConfigOpt = DEFAULT_CONFIG) -> None:
    """Weboberfläche starten."""
    cfg = load(config)
    uvicorn.run(
        create_app(cfg, State(cfg.state_path), _client(cfg)), host=cfg.web.host, port=cfg.web.port
    )


def _writable(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    probe = path / ".lpa-write-test"
    probe.write_text("ok")
    probe.unlink()


def _get_ok(url: str) -> None:
    httpx.get(url, timeout=10).raise_for_status()


def _require(value: Any, message: str) -> None:
    if not value:
        raise RuntimeError(message)


@app.command()
def doctor(config: ConfigOpt = DEFAULT_CONFIG) -> None:
    """Prüft Verbindungen, Ordner und Werkzeuge."""
    cfg = load(config)
    problems: list[str] = []

    def check(label: str, fn: Callable[[], object]) -> None:
        try:
            fn()
        except Exception as exc:
            problems.append(label)
            typer.echo(f"✗ {label}: {exc}")
        else:
            typer.echo(f"✓ {label}")

    check("Paperless erreichbar", _client(cfg).ping)
    check("Regeln gültig", lambda: load_rules(cfg.rules_path))
    for folder in (cfg.gate.inbox_dir, cfg.paperless.consume_dir, cfg.export_dir):
        check(f"Ordner beschreibbar: {folder}", functools.partial(_writable, folder))
    for p in cfg.ai.providers:
        if p.kind == "openai_compatible":
            url = f"{p.base_url.rstrip('/')}/models"
            check(f"KI {p.name}", functools.partial(_get_ok, url))
    if any(t.kind == "rclone" for t in cfg.backup.targets):
        check("rclone installiert", lambda: _require(shutil.which("rclone"), "not on PATH"))
    if problems:
        raise typer.Exit(1)
