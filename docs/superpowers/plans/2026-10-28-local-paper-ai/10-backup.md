### Task 10: Backup (export, multi-target copy, sha256 verification)

**Files:**
- Create: `src/local_paper_ai/backup.py`
- Test: `tests/test_backup.py`

**Interfaces:**
- Consumes: `BackupCfg` (Task 2), `Document` (Task 2), `State` (Task 3).
- Produces:
  - `BackupError`, `Protocol Downloader { download(doc_id) -> bytes }`
  - `Protocol Target { name: str; put(src: Path) -> None; verify(src: Path, sha256: str) -> bool }`
  - `LocalTarget(name, path)`, `RcloneTarget(name, remote, crypt=False, runner=_run)`, where `Runner = Callable[[list[str]], int]` returns the exit code
  - `export_name(doc) -> str`, e.g. `2026-03-14_Finanzamt-München_Steuerbescheid-2025_42.pdf`
  - `sha256_file(path) -> str`
  - `build_targets(cfg) -> list[Target]`
  - `BackupReport(exported, copied, failed: list[str])`
  - `sync(source: Downloader, state, targets, export_dir) -> BackupReport`

- [ ] **Step 1: Write the failing test** `tests/test_backup.py`

```python
from datetime import date
from pathlib import Path

from local_paper_ai.backup import (
    LocalTarget,
    RcloneTarget,
    export_name,
    sha256_file,
    sync,
)
from local_paper_ai.models import Analysis, Document, Verdict
from local_paper_ai.state import State
from tests.fakes import FakePaperless

DOC = Document(
    id=42,
    title="Steuerbescheid 2025/Einkommen",
    content="x",
    created=date(2026, 3, 14),
    correspondent="Finanzamt München",
)


def test_export_name_is_safe_and_unique() -> None:
    assert export_name(DOC) == "2026-03-14_Finanzamt-München_Steuerbescheid-2025-Einkommen_42.pdf"
    anon = DOC.model_copy(update={"correspondent": None, "title": "///"})
    assert export_name(anon) == "2026-03-14_Unbekannt_Unbenannt_42.pdf"


def test_local_target_put_and_verify(tmp_path: Path) -> None:
    src = tmp_path / "a.pdf"
    src.write_bytes(b"%PDF")
    target = LocalTarget("nas", tmp_path / "nas")
    target.put(src)
    assert target.verify(src, sha256_file(src))
    (tmp_path / "nas" / "a.pdf").write_bytes(b"tampered")
    assert not target.verify(src, sha256_file(src))


class FakeRunner:
    def __init__(self, fail: str = "") -> None:
        self.calls: list[list[str]] = []
        self.fail = fail

    def __call__(self, cmd: list[str]) -> int:
        self.calls.append(cmd)
        return 1 if cmd[1] == self.fail else 0


def test_rclone_commands(tmp_path: Path) -> None:
    src = tmp_path / "a.pdf"
    src.write_bytes(b"%PDF")
    runner = FakeRunner()
    RcloneTarget("cloud", "crypt:lpa", crypt=True, runner=runner).put(src)
    assert RcloneTarget("cloud", "crypt:lpa", crypt=True, runner=runner).verify(src, "x")
    assert RcloneTarget("gd", "gd:lpa", runner=runner).verify(src, "x")
    assert runner.calls == [
        ["rclone", "copyto", str(src), "crypt:lpa/a.pdf"],
        ["rclone", "cryptcheck", str(tmp_path), "crypt:lpa", "--one-way", "--include", "a.pdf"],
        ["rclone", "check", str(tmp_path), "gd:lpa", "--one-way", "--include", "a.pdf"],
    ]


def _state_with_doc(tmp_path: Path, store: FakePaperless) -> State:
    state = State(tmp_path / "s.db")
    store.add(DOC)
    state.save_analysis(DOC, Analysis(doc_id=42, verdict=Verdict(kind="may_shred")))
    return state


def test_sync_copies_verifies_and_is_incremental(tmp_path: Path) -> None:
    store = FakePaperless()
    state = _state_with_doc(tmp_path, store)
    targets = [LocalTarget("nas", tmp_path / "nas")]
    report = sync(store, state, targets, tmp_path / "export")
    assert (report.exported, report.copied, report.failed) == (1, 1, [])
    assert state.verified_copies(42) == 2
    again = sync(store, state, targets, tmp_path / "export")
    assert (again.exported, again.copied) == (0, 0)
    assert store.downloads == 1


def test_sync_reports_failing_target(tmp_path: Path) -> None:
    store = FakePaperless()
    state = _state_with_doc(tmp_path, store)
    target = RcloneTarget("cloud", "crypt:lpa", runner=FakeRunner(fail="copyto"))
    report = sync(store, state, [target], tmp_path / "export")
    assert report.failed == ["42 -> cloud"]
    assert state.verified_copies(42) == 1
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_backup.py -v`
Expected: FAIL with `No module named 'local_paper_ai.backup'`

- [ ] **Step 3: Implement** `src/local_paper_ai/backup.py`

```python
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
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_backup.py -v`
Expected: `5 passed`

- [ ] **Step 5: Commit and push** with the message `feat: verified multi-target backup` (plus the trailer).
