"""Feed scans into Paperless one at a time, only while the box is idle."""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable
from pathlib import Path

from local_paper_ai.config import GateCfg
from local_paper_ai.metrics import Metrics

SCAN_SUFFIXES = frozenset({".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"})
SETTLE_S = 60.0


def is_idle(m: Metrics, cfg: GateCfg, overdue: bool = False) -> bool:
    """Free RAM is always required. Overdue scans ignore the CPU/GPU limits."""
    if m.ram_free_mb < cfg.ram_free_min_mb:
        return False
    return overdue or (m.cpu_pct <= cfg.cpu_max and m.gpu_pct <= cfg.gpu_max)


def _is_scan(path: Path) -> bool:
    return (
        path.is_file()
        and not path.name.startswith((".", "~"))
        and path.suffix.lower() in SCAN_SUFFIXES
    )


def pending_files(inbox: Path, now: float, settle_s: float = SETTLE_S) -> list[Path]:
    if not inbox.is_dir():
        return []
    files = [p for p in inbox.iterdir() if _is_scan(p) and p.stat().st_mtime <= now - settle_s]
    return sorted(files, key=lambda p: p.stat().st_mtime)


def next_file(inbox: Path, now: float) -> Path | None:
    files = pending_files(inbox, now)
    return files[0] if files else None


def is_overdue(path: Path, now: float, max_wait_days: int) -> bool:
    return now - path.stat().st_mtime > max_wait_days * 86_400


def feed_one(
    src: Path,
    consume_dir: Path,
    timeout_s: float = 1800.0,
    poll_s: float = 5.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """Move one scan into Paperless' consume folder; True once Paperless has taken it."""
    consume_dir.mkdir(parents=True, exist_ok=True)
    dest = consume_dir / src.name
    n = 1
    while dest.exists():
        dest = consume_dir / f"{src.stem}-{n}{src.suffix}"
        n += 1
    shutil.move(src, dest)
    deadline = clock() + timeout_s
    while dest.exists():
        if clock() >= deadline:
            return False
        sleep(poll_s)
    return True
