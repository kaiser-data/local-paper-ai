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
