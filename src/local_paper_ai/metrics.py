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
