### Task 13: Worker cycle and CLI

**Files:**
- Create: `src/local_paper_ai/worker.py`, `src/local_paper_ai/cli.py`
- Test: `tests/test_worker.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: everything from Tasks 2–12.
- Produces:
  - `worker`:
    - `Protocol PaperlessPort(DocumentStore, Downloader) { list_document_ids(); get_document(id) }`
    - `Context(cfg, state, paperless, router, rules, metrics, targets, http, feed=feed_one)`
    - `CycleReport(idle, fed, analyzed, reminders, backup)`
    - `build_context(cfg) -> Context`
    - `feed_while_idle(ctx, now) -> list[str]`, `analyze_pending(ctx, today) -> int`
    - `run_cycle(ctx, today, now) -> CycleReport`
    - `run_forever(ctx, sleep=time.sleep) -> None` (backs off exponentially on `httpx.HTTPError`, up to 6 h)
  - `cli.app` (Typer), with the commands `run`, `once`, `gate-status`, `analyze ID [--write]`, `backup`, `serve` and `doctor`. Each takes `--config/-c` (env `LPA_CONFIG`, default `config.yaml`).
- Cycle order: reminders are sent first, even when the box is busy. Only when it is idle: feed scans one at a time while it stays idle → analyze new documents (re-checking idle between documents) → back up.

- [ ] **Step 1: Write the failing tests**

`tests/test_worker.py`:
```python
import os
from datetime import date
from pathlib import Path

import httpx
import pytest

from local_paper_ai.ai.router import Router
from local_paper_ai.backup import LocalTarget
from local_paper_ai.config import Config
from local_paper_ai.metrics import Metrics
from local_paper_ai.models import Document
from local_paper_ai.rules import load_rules
from local_paper_ai.state import State
from local_paper_ai.worker import Context, run_cycle, run_forever
from tests.fakes import FakePaperless

ROOT = Path(__file__).resolve().parents[1]
NOW = 1_800_000_000.0
TODAY = date(2026, 10, 28)


class SeqMetrics:
    """Returns the given readings in order, then repeats the last one."""

    def __init__(self, *readings: Metrics) -> None:
        self.readings = list(readings)

    def read(self) -> Metrics:
        return self.readings.pop(0) if len(self.readings) > 1 else self.readings[0]

    def free_mb(self) -> int:
        return self.readings[0].ram_free_mb


IDLE = Metrics(5, 0, 6000)
BUSY = Metrics(95, 80, 6000)


def _ctx(tmp_path: Path, metrics: SeqMetrics, store: FakePaperless) -> Context:
    cfg = Config.model_validate({
        "paperless": {"url": "http://pl", "token": "t", "consume_dir": str(tmp_path / "consume")},
        "gate": {"inbox_dir": str(tmp_path / "inbox")},
        "export_dir": str(tmp_path / "export"),
    })
    (tmp_path / "inbox").mkdir()

    def feed(src: Path, consume: Path) -> bool:  # "Paperless" consumes instantly
        new_id = max(store.documents, default=0) + 1
        store.add(Document(id=new_id, title=src.stem, content="Ihr Kontoauszug Nr. 3",
                           created=date(2026, 10, 1), page_count=1))
        src.unlink()
        return True

    return Context(cfg=cfg, state=State(tmp_path / "s.db"), paperless=store,
                   router=Router([], []), rules=load_rules(ROOT / "rules.yaml"),
                   metrics=metrics, targets=[LocalTarget("nas", tmp_path / "nas")],
                   http=httpx.Client(), feed=feed)


def _scan(tmp_path: Path, name: str, age_s: float) -> None:
    path = tmp_path / "inbox" / name
    path.write_bytes(b"x")
    os.utime(path, (NOW - age_s, NOW - age_s))


def test_busy_box_does_nothing(tmp_path: Path) -> None:
    store = FakePaperless()
    ctx = _ctx(tmp_path, SeqMetrics(BUSY), store)
    _scan(tmp_path, "a.pdf", 600)
    report = run_cycle(ctx, TODAY, NOW)
    assert report.idle is False and report.fed == [] and store.documents == {}


def test_idle_cycle_feeds_analyzes_tags_and_backs_up(tmp_path: Path) -> None:
    store = FakePaperless()
    ctx = _ctx(tmp_path, SeqMetrics(IDLE), store)
    _scan(tmp_path, "a.pdf", 1200)
    _scan(tmp_path, "b.pdf", 600)
    report = run_cycle(ctx, TODAY, NOW)
    assert report.fed == ["a.pdf", "b.pdf"]
    assert report.analyzed == 2
    assert store.tag_names(1) == {"lpa:darf-weg"}
    assert report.backup is not None and report.backup.copied == 2
    assert ctx.state.verified_copies(1) == 2
    assert run_cycle(ctx, TODAY, NOW).analyzed == 0


def test_stops_feeding_when_load_rises(tmp_path: Path) -> None:
    store = FakePaperless()
    # read 1 (cycle gate) idle -> feed a.pdf; read 2 (re-check before b.pdf) busy -> stop
    ctx = _ctx(tmp_path, SeqMetrics(IDLE, BUSY), store)
    _scan(tmp_path, "a.pdf", 1200)
    _scan(tmp_path, "b.pdf", 600)
    assert run_cycle(ctx, TODAY, NOW).fed == ["a.pdf"]


def test_overdue_scan_ignores_cpu_limit(tmp_path: Path) -> None:
    store = FakePaperless()
    ctx = _ctx(tmp_path, SeqMetrics(BUSY), store)
    _scan(tmp_path, "old.pdf", 5 * 86_400)
    assert run_cycle(ctx, TODAY, NOW).fed == ["old.pdf"]


class Stop(Exception):
    pass


def test_run_forever_backs_off_on_api_errors(tmp_path: Path) -> None:
    store = FakePaperless()

    def broken() -> list[int]:
        raise httpx.ConnectError("down")

    store.list_document_ids = broken  # type: ignore[method-assign]
    ctx = _ctx(tmp_path, SeqMetrics(IDLE), store)
    delays: list[float] = []

    def sleep(seconds: float) -> None:
        delays.append(seconds)
        if len(delays) == 3:
            raise Stop

    with pytest.raises(Stop):
        run_forever(ctx, sleep=sleep)
    assert delays == [1800.0, 3600.0, 7200.0]
```

`tests/test_cli.py`:
```python
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
    respx_mock.get("http://pl/api/documents/5/").mock(return_value=httpx.Response(200, json={
        "id": 5, "title": "Auszug", "content": "Ihr Kontoauszug Nr. 3", "created": "2026-10-01",
        "correspondent": None, "document_type": None, "tags": [], "custom_fields": [],
    }))
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
```

- [ ] **Step 2: Run them**

Run: `uv run pytest tests/test_worker.py tests/test_cli.py -v`
Expected: FAIL with `No module named 'local_paper_ai.worker'`

- [ ] **Step 3: Implement** `src/local_paper_ai/worker.py`

```python
"""One worker cycle: reminders, then (only when idle) feed scans, analyze, back up."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Protocol

import httpx

from local_paper_ai.ai.router import Extractor, build_router
from local_paper_ai.analyzer import analyze
from local_paper_ai.backup import BackupReport, Downloader, Target, build_targets, sync
from local_paper_ai.config import Config
from local_paper_ai.idle_gate import feed_one, is_idle, is_overdue, next_file
from local_paper_ai.metrics import MetricsSource, make_source
from local_paper_ai.models import Document
from local_paper_ai.notify import send_reminders
from local_paper_ai.paperless import PaperlessClient
from local_paper_ai.rules import Rule, load_rules
from local_paper_ai.state import State
from local_paper_ai.writer import DocumentStore, apply

log = logging.getLogger(__name__)
MAX_BACKOFF_S = 6 * 3600.0


class PaperlessPort(DocumentStore, Downloader, Protocol):
    def list_document_ids(self) -> list[int]: ...
    def get_document(self, doc_id: int) -> Document: ...


@dataclass
class Context:
    cfg: Config
    state: State
    paperless: PaperlessPort
    router: Extractor
    rules: list[Rule]
    metrics: MetricsSource
    targets: list[Target]
    http: httpx.Client
    feed: Callable[[Path, Path], bool] = feed_one


@dataclass
class CycleReport:
    idle: bool
    fed: list[str] = field(default_factory=list)
    analyzed: int = 0
    reminders: int = 0
    backup: BackupReport | None = None


def build_context(cfg: Config) -> Context:
    http = httpx.Client()
    metrics = make_source(cfg.gate.metrics)
    return Context(
        cfg=cfg,
        state=State(cfg.state_path),
        paperless=PaperlessClient(cfg.paperless.url, cfg.paperless.token),
        router=build_router(cfg.ai, http, metrics.free_mb),
        rules=load_rules(cfg.rules_path),
        metrics=metrics,
        targets=build_targets(cfg.backup),
        http=http,
        feed=lambda src, dest: feed_one(src, dest, timeout_s=cfg.gate.feed_timeout_s),
    )


def _gate_open(ctx: Context, now: float) -> bool:
    nxt = next_file(ctx.cfg.gate.inbox_dir, now)
    overdue = nxt is not None and is_overdue(nxt, now, ctx.cfg.gate.max_wait_days)
    return is_idle(ctx.metrics.read(), ctx.cfg.gate, overdue)


def feed_while_idle(ctx: Context, now: float) -> list[str]:
    fed: list[str] = []
    while (nxt := next_file(ctx.cfg.gate.inbox_dir, now)) is not None:
        if fed and not _gate_open(ctx, now):
            break
        if not ctx.feed(nxt, ctx.cfg.paperless.consume_dir):
            log.warning("Paperless did not consume %s in time", nxt.name)
            break
        fed.append(nxt.name)
    return fed


def analyze_pending(ctx: Context, today: date) -> int:
    count = 0
    for doc_id in ctx.paperless.list_document_ids():
        if ctx.state.has(doc_id):
            continue
        if count and not is_idle(ctx.metrics.read(), ctx.cfg.gate):
            break
        doc = ctx.paperless.get_document(doc_id)
        analysis = analyze(doc, ctx.rules, ctx.router)
        apply(ctx.paperless, analysis, today)
        ctx.state.save_analysis(doc, analysis)
        count += 1
    return count


def run_cycle(ctx: Context, today: date, now: float) -> CycleReport:
    reminders = send_reminders(ctx.http, ctx.cfg.notify.ntfy_url, ctx.state, today)
    if not _gate_open(ctx, now):
        return CycleReport(idle=False, reminders=reminders)
    report = CycleReport(idle=True, reminders=reminders)
    report.fed = feed_while_idle(ctx, now)
    report.analyzed = analyze_pending(ctx, today)
    report.backup = sync(ctx.paperless, ctx.state, ctx.targets, ctx.cfg.export_dir)
    return report


def run_forever(ctx: Context, sleep: Callable[[float], None] = time.sleep) -> None:
    interval = float(ctx.cfg.gate.interval_s)
    delay = interval
    while True:
        try:
            report = run_cycle(ctx, date.today(), time.time())
            log.info("cycle: %s", report)
            delay = interval
        except httpx.HTTPError as exc:
            delay = min(delay * 2, MAX_BACKOFF_S)
            log.warning("API error, retrying in %.0f s: %s", delay, exc)
        sleep(delay)
```

Note: the first file and the first document after an open gate need no re-check. `analyze_pending` always analyzes one document once the gate has opened; that is intended.

`src/local_paper_ai/cli.py`:
```python
"""Command line: run the worker, serve the web UI, inspect and diagnose."""

from __future__ import annotations

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
    uvicorn.run(create_app(cfg, State(cfg.state_path), _client(cfg)), host=cfg.web.host,
                port=cfg.web.port)


def _writable(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    probe = path / ".lpa-write-test"
    probe.write_text("ok")
    probe.unlink()


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
        check(f"Ordner beschreibbar: {folder}", lambda folder=folder: _writable(folder))
    for p in cfg.ai.providers:
        if p.kind == "openai_compatible":
            url = f"{p.base_url.rstrip('/')}/models"
            check(f"KI {p.name}", lambda url=url: httpx.get(url, timeout=10).raise_for_status())
    if any(t.kind == "rclone" for t in cfg.backup.targets):
        check("rclone installiert", lambda: _require(shutil.which("rclone"), "not on PATH"))
    if problems:
        raise typer.Exit(1)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_worker.py tests/test_cli.py -v && uv run mypy`
Expected: `8 passed`, mypy success. If mypy reports that `lambda folder=folder: …` has an untyped default, replace those lambdas with small `def` closures that take a typed default parameter.

- [ ] **Step 5: Commit and push** with the message `feat: worker cycle and CLI` (plus the trailer).
