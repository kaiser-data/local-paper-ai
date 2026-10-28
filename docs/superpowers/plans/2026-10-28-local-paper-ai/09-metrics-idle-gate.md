### Task 9: Metrics and idle gate

**Files:**
- Create: `src/local_paper_ai/metrics.py`, `src/local_paper_ai/idle_gate.py`
- Test: `tests/test_idle_gate.py`

**Interfaces:**
- Consumes: `GateCfg` (Task 2).
- Produces:
  - `metrics`:
    - `Metrics(cpu_pct, gpu_pct, ram_free_mb)` (frozen)
    - `Protocol MetricsSource { read() -> Metrics; free_mb() -> int }`
    - `PsutilMetrics(cpu=…)`, `JetsonMetrics(gpu_load_path, meminfo=Path("/proc/meminfo"), cpu=…)`
    - `GPU_LOAD_PATHS`, `find_gpu_load(paths) -> Path | None`
    - `make_source(kind, gpu_paths=GPU_LOAD_PATHS) -> MetricsSource`
  - `idle_gate`:
    - `SCAN_SUFFIXES`
    - `is_idle(m, cfg, overdue=False) -> bool`
    - `pending_files(inbox, now: float, settle_s=60.0) -> list[Path]` (oldest first)
    - `next_file(inbox, now) -> Path | None`, `is_overdue(path, now, max_wait_days) -> bool`
    - `feed_one(src, consume_dir, timeout_s=1800.0, poll_s=5.0, sleep=time.sleep, clock=time.monotonic) -> bool`

- [ ] **Step 1: Write the failing test** `tests/test_idle_gate.py`

```python
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
    assert feed_one(src, consume, timeout_s=1500, sleep=lambda _: None,
                    clock=lambda: float(next(ticks))) is False
    assert (consume / "scan-1.pdf").exists()
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_idle_gate.py -v`
Expected: FAIL with `No module named 'local_paper_ai.idle_gate'`

- [ ] **Step 3: Implement** `src/local_paper_ai/metrics.py`

```python
"""Machine load. On a Jetson, FREE RAM is what counts: CUDA allocations fail instead of
waiting for the page cache to be reclaimed."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import psutil

GPU_LOAD_PATHS: tuple[Path, ...] = (
    Path("/sys/devices/platform/gpu.0/load"),
    Path("/sys/devices/gpu.0/load"),
    Path("/sys/devices/platform/17000000.ga10b/load"),
    Path("/sys/devices/platform/17000000.gpu/load"),
)
_MIB = 1024 * 1024


@dataclass(frozen=True)
class Metrics:
    cpu_pct: float
    gpu_pct: float
    ram_free_mb: int


class MetricsSource(Protocol):
    def read(self) -> Metrics: ...
    def free_mb(self) -> int: ...


def _cpu() -> float:
    return float(psutil.cpu_percent(interval=1.0))


class PsutilMetrics:
    """Generic Linux/macOS/Windows. No GPU reading; uses available RAM."""

    def __init__(self, cpu: Callable[[], float] = _cpu) -> None:
        self._cpu = cpu

    def free_mb(self) -> int:
        return int(psutil.virtual_memory().available // _MIB)

    def read(self) -> Metrics:
        return Metrics(cpu_pct=self._cpu(), gpu_pct=0.0, ram_free_mb=self.free_mb())


class JetsonMetrics:
    def __init__(
        self,
        gpu_load_path: Path,
        meminfo: Path = Path("/proc/meminfo"),
        cpu: Callable[[], float] = _cpu,
    ) -> None:
        self._gpu = gpu_load_path
        self._meminfo = meminfo
        self._cpu = cpu

    def free_mb(self) -> int:
        for line in self._meminfo.read_text().splitlines():
            if line.startswith("MemFree:"):
                return int(line.split()[1]) // 1024
        return 0

    def read(self) -> Metrics:
        load = int(self._gpu.read_text().strip() or 0)  # 0..1000
        return Metrics(cpu_pct=self._cpu(), gpu_pct=load / 10.0, ram_free_mb=self.free_mb())


def find_gpu_load(paths: Sequence[Path] = GPU_LOAD_PATHS) -> Path | None:
    return next((p for p in paths if p.exists()), None)


def make_source(kind: str, gpu_paths: Sequence[Path] = GPU_LOAD_PATHS) -> MetricsSource:
    gpu = find_gpu_load(gpu_paths)
    if kind == "psutil" or (kind == "auto" and gpu is None):
        return PsutilMetrics()
    if gpu is None:
        raise RuntimeError("metrics: jetson, but no GPU load file was found")
    return JetsonMetrics(gpu)
```

`src/local_paper_ai/idle_gate.py`:
```python
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
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_idle_gate.py -v`
Expected: `7 passed`

- [ ] **Step 5: Commit and push** with the message `feat: jetson-aware metrics and idle gate` (plus the trailer).
