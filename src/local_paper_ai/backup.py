"""Export PDFs to every backup target and record verified copies."""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import httpx

from local_paper_ai.config import BackupCfg
from local_paper_ai.models import Document
from local_paper_ai.state import State

log = logging.getLogger(__name__)
_UNSAFE = re.compile(r"[^\w-]+")
Runner = Callable[[list[str]], int]


class BackupError(Exception):
    """A target refused a copy."""


class Downloader(Protocol):
    def download(self, doc_id: int) -> bytes: ...


class Target(Protocol):
    name: str

    def put(self, src: Path) -> None: ...
    def verify(self, src: Path, sha256: str) -> bool: ...


def _slug(text: str) -> str:
    return _UNSAFE.sub("-", text).strip("-")[:60] or "Unbenannt"


def export_name(doc: Document) -> str:
    correspondent = _slug(doc.correspondent or "Unbekannt")
    return f"{doc.created.isoformat()}_{correspondent}_{_slug(doc.title)}_{doc.id}.pdf"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(cmd: list[str]) -> int:
    return subprocess.run(cmd, check=False, capture_output=True).returncode


class LocalTarget:
    """A folder, e.g. one shared by Syncthing with the desktop and phone."""

    def __init__(self, name: str, path: Path) -> None:
        self.name = name
        self._dir = path

    def put(self, src: Path) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, self._dir / src.name)

    def verify(self, src: Path, sha256: str) -> bool:
        dest = self._dir / src.name
        return dest.exists() and sha256_file(dest) == sha256


class RcloneTarget:
    """Any rclone remote. With crypt=True the remote is a crypt remote (cryptcheck)."""

    def __init__(self, name: str, remote: str, crypt: bool = False, runner: Runner = _run) -> None:
        self.name = name
        self._remote = remote.rstrip("/")
        self._crypt = crypt
        self._run = runner

    def put(self, src: Path) -> None:
        if self._run(["rclone", "copyto", str(src), f"{self._remote}/{src.name}"]) != 0:
            raise BackupError(f"rclone copyto failed for {src.name}")

    def verify(self, src: Path, sha256: str) -> bool:
        command = "cryptcheck" if self._crypt else "check"
        cmd = ["rclone", command, str(src.parent), self._remote, "--one-way", "--include", src.name]
        return self._run(cmd) == 0


def build_targets(cfg: BackupCfg) -> list[Target]:
    targets: list[Target] = []
    for t in cfg.targets:
        if t.kind == "local":
            targets.append(LocalTarget(t.name, Path(t.path)))
        else:
            targets.append(RcloneTarget(t.name, t.path, t.crypt))
    return targets


@dataclass
class BackupReport:
    exported: int = 0
    copied: int = 0
    failed: list[str] = field(default_factory=list)


def sync(source: Downloader, state: State, targets: list[Target], export_dir: Path) -> BackupReport:
    report = BackupReport()
    export_dir.mkdir(parents=True, exist_ok=True)
    for stored in state.all_docs():
        doc = stored.document
        path = export_dir / export_name(doc)
        if not path.exists():
            try:
                partial = path.with_suffix(".part")
                partial.write_bytes(source.download(doc.id))
                partial.replace(path)
                report.exported += 1
            except httpx.HTTPError as exc:
                report.failed.append(f"{doc.id}: download failed ({exc})")
                continue
        digest = sha256_file(path)
        for target in targets:
            if state.copy_sha(doc.id, target.name) == digest:
                continue
            try:
                target.put(path)
                ok = target.verify(path, digest)
            except (OSError, BackupError) as exc:
                log.warning("backup %s -> %s failed: %s", doc.id, target.name, exc)
                ok = False
            if ok:
                state.record_copy(doc.id, target.name, digest)
                report.copied += 1
            else:
                report.failed.append(f"{doc.id} -> {target.name}")
    return report
