import os
from pathlib import Path

from local_paper_ai.config import GateCfg
from local_paper_ai.idle_gate import feed_one, is_idle, is_overdue, next_file, pending_files
from local_paper_ai.metrics import JetsonMetrics, Metrics, PsutilMetrics, make_source

CFG = GateCfg(inbox_dir=Path("/x"), cpu_max=30, gpu_max=20, ram_free_min_mb=3000)
NOW = 1_800_000_000.0


def test_jetson_metrics_reads_free_not_available(tmp_path: Path) -> None:
    gpu = tmp_path / "load"
    gpu.write_text("450\n")
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 7800000 kB\nMemFree: 2048000 kB\nMemAvailable: 5000000 kB\n")
    m = JetsonMetrics(gpu, meminfo, cpu=lambda: 12.0).read()
    assert m == Metrics(cpu_pct=12.0, gpu_pct=45.0, ram_free_mb=2000)


def test_make_source(tmp_path: Path) -> None:
    gpu = tmp_path / "load"
    gpu.write_text("0")
    assert isinstance(make_source("auto", [tmp_path / "missing"]), PsutilMetrics)
    assert isinstance(make_source("auto", [gpu]), JetsonMetrics)
    assert isinstance(make_source("psutil", [gpu]), PsutilMetrics)


def test_is_idle() -> None:
    assert is_idle(Metrics(10, 5, 4000), CFG)
    assert not is_idle(Metrics(50, 5, 4000), CFG)
    assert not is_idle(Metrics(10, 50, 4000), CFG)
    assert not is_idle(Metrics(10, 5, 1000), CFG)
    assert is_idle(Metrics(90, 90, 4000), CFG, overdue=True)
    assert not is_idle(Metrics(90, 90, 1000), CFG, overdue=True)


def _scan(inbox: Path, name: str, age_s: float) -> Path:
    path = inbox / name
    path.write_bytes(b"x")
    os.utime(path, (NOW - age_s, NOW - age_s))
    return path


def test_pending_files_filters_and_sorts(tmp_path: Path) -> None:
    newer = _scan(tmp_path, "b.pdf", 600)
    older = _scan(tmp_path, "a.JPG", 7200)
    _scan(tmp_path, ".syncthing.c.pdf.tmp", 7200)
    _scan(tmp_path, "~lock.pdf", 7200)
    _scan(tmp_path, "notes.txt", 7200)
    _scan(tmp_path, "fresh.pdf", 5)
    assert pending_files(tmp_path, NOW) == [older, newer]
    assert next_file(tmp_path, NOW) == older
    assert next_file(tmp_path / "missing", NOW) is None


def test_is_overdue(tmp_path: Path) -> None:
    assert is_overdue(_scan(tmp_path, "old.pdf", 4 * 86400), NOW, 3)
    assert not is_overdue(_scan(tmp_path, "new.pdf", 86400), NOW, 3)


def test_feed_one_waits_until_consumed(tmp_path: Path) -> None:
    src = _scan(tmp_path, "scan.pdf", 600)
    consume = tmp_path / "consume"

    def sleep(_: float) -> None:  # Paperless picks the file up
        for f in consume.iterdir():
            f.unlink()

    assert feed_one(src, consume, sleep=sleep) is True
    assert not src.exists()


def test_feed_one_times_out_and_avoids_name_collision(tmp_path: Path) -> None:
    consume = tmp_path / "consume"
    consume.mkdir()
    (consume / "scan.pdf").write_bytes(b"other")
    src = _scan(tmp_path, "scan.pdf", 600)
    ticks = iter(range(0, 10_000, 1000))
    assert (
        feed_one(
            src, consume, timeout_s=1500, sleep=lambda _: None, clock=lambda: float(next(ticks))
        )
        is False
    )
    assert (consume / "scan-1.pdf").exists()
