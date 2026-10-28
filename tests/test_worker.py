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
    cfg = Config.model_validate(
        {
            "paperless": {
                "url": "http://pl",
                "token": "t",
                "consume_dir": str(tmp_path / "consume"),
            },
            "gate": {"inbox_dir": str(tmp_path / "inbox")},
            "export_dir": str(tmp_path / "export"),
        }
    )
    (tmp_path / "inbox").mkdir()

    def feed(src: Path, consume: Path) -> bool:  # "Paperless" consumes instantly
        new_id = max(store.documents, default=0) + 1
        store.add(
            Document(
                id=new_id,
                title=src.stem,
                content="Ihr Kontoauszug Nr. 3",
                created=date(2026, 10, 1),
                page_count=1,
            )
        )
        src.unlink()
        return True

    return Context(
        cfg=cfg,
        state=State(tmp_path / "s.db"),
        paperless=store,
        router=Router([], []),
        rules=load_rules(ROOT / "rules.yaml"),
        metrics=metrics,
        targets=[LocalTarget("nas", tmp_path / "nas")],
        http=httpx.Client(),
        feed=feed,
    )


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
