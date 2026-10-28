### Task 3: SQLite state

**Files:**
- Create: `src/local_paper_ai/state.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Consumes: `Document`, `Analysis` (Task 2).
- Produces: `StoredDoc(document: Document, analysis: Analysis)` (frozen dataclass), and `State(path: Path)` with:
  `has(doc_id) -> bool`, `save_analysis(doc, analysis, now=None)`, `update_analysis(analysis, now=None)`,
  `get(doc_id) -> StoredDoc | None`, `all_docs() -> list[StoredDoc]`,
  `record_copy(doc_id, target, sha256, now=None)`, `copy_sha(doc_id, target) -> str | None`,
  `verified_copies(doc_id) -> int` (1 + the number of recorded targets),
  `mark_shredded(doc_id, pages, now=None)`, `is_shredded(doc_id) -> bool`, `shred_stats() -> tuple[int, int]` (docs, pages),
  `was_notified(doc_id, due: date, kind: str) -> bool`, `mark_notified(doc_id, due, kind)`, `close()`.

- [x] **Step 1: Write the failing test** `tests/test_state.py`

```python
from datetime import UTC, date, datetime
from pathlib import Path

from local_paper_ai.models import Analysis, Document, Verdict
from local_paper_ai.state import State

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


def _doc(doc_id: int = 1, pages: int = 2) -> Document:
    return Document(
        id=doc_id,
        title="Kontoauszug März",
        content="Kontoauszug",
        created=date(2026, 3, 14),
        correspondent="Sparkasse",
        page_count=pages,
    )


def _analysis(doc_id: int = 1, kind: str = "may_shred") -> Analysis:
    return Analysis(doc_id=doc_id, verdict=Verdict(kind=kind, category="finance"))  # type: ignore[arg-type]


def test_save_get_roundtrip(tmp_path: Path) -> None:
    s = State(tmp_path / "sub" / "s.db")
    s.save_analysis(_doc(), _analysis(), NOW)
    got = s.get(1)
    assert got is not None
    assert got.document.title == "Kontoauszug März"
    assert got.analysis.verdict.kind == "may_shred"
    assert s.has(1) and not s.has(2)
    assert [d.document.id for d in s.all_docs()] == [1]


def test_update_analysis_keeps_document(tmp_path: Path) -> None:
    s = State(tmp_path / "s.db")
    s.save_analysis(_doc(), _analysis(), NOW)
    s.update_analysis(_analysis(kind="keep_original"), NOW)
    got = s.get(1)
    assert got is not None
    assert got.analysis.verdict.kind == "keep_original"
    assert got.document.correspondent == "Sparkasse"


def test_verified_copies_counts_paperless_plus_targets(tmp_path: Path) -> None:
    s = State(tmp_path / "s.db")
    assert s.verified_copies(1) == 1
    s.record_copy(1, "nas", "abc", NOW)
    s.record_copy(1, "nas", "abc", NOW)
    s.record_copy(1, "cloud", "abc", NOW)
    assert s.verified_copies(1) == 3
    assert s.copy_sha(1, "nas") == "abc"
    assert s.copy_sha(1, "missing") is None


def test_shred_stats(tmp_path: Path) -> None:
    s = State(tmp_path / "s.db")
    assert s.shred_stats() == (0, 0)
    s.mark_shredded(1, 3, NOW)
    s.mark_shredded(2, 2, NOW)
    assert s.shred_stats() == (2, 5)
    assert s.is_shredded(1) and not s.is_shredded(3)


def test_notified(tmp_path: Path) -> None:
    s = State(tmp_path / "s.db")
    due = date(2026, 11, 1)
    assert not s.was_notified(1, due, "d7")
    s.mark_notified(1, due, "d7")
    s.mark_notified(1, due, "d7")
    assert s.was_notified(1, due, "d7")
    assert not s.was_notified(1, due, "d1")
```

- [x] **Step 2: Run it**

Run: `uv run pytest tests/test_state.py -v`
Expected: FAIL with `No module named 'local_paper_ai.state'`

- [x] **Step 3: Implement** `src/local_paper_ai/state.py`

```python
"""SQLite state: analyses, verified backup copies, shred log, sent reminders."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from local_paper_ai.models import Analysis, Document

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id INTEGER PRIMARY KEY,
    analyzed_at TEXT NOT NULL,
    document_json TEXT NOT NULL,
    analysis_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS copies (
    doc_id INTEGER NOT NULL,
    target TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    verified_at TEXT NOT NULL,
    PRIMARY KEY (doc_id, target)
);
CREATE TABLE IF NOT EXISTS shredded (
    doc_id INTEGER PRIMARY KEY,
    shredded_at TEXT NOT NULL,
    pages INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS notified (
    doc_id INTEGER NOT NULL,
    due TEXT NOT NULL,
    kind TEXT NOT NULL,
    PRIMARY KEY (doc_id, due, kind)
);
"""


@dataclass(frozen=True)
class StoredDoc:
    document: Document
    analysis: Analysis


def _ts(now: datetime | None) -> str:
    return (now or datetime.now(UTC)).isoformat()


def _stored(row: tuple[str, str]) -> StoredDoc:
    return StoredDoc(Document.model_validate_json(row[0]), Analysis.model_validate_json(row[1]))


class State:
    """Shared by the worker and the web process; WAL mode allows both."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    def _write(self, sql: str, params: tuple[object, ...]) -> None:
        with self._lock, self._db:
            self._db.execute(sql, params)

    def has(self, doc_id: int) -> bool:
        row = self._db.execute("SELECT 1 FROM documents WHERE doc_id = ?", (doc_id,)).fetchone()
        return row is not None

    def save_analysis(
        self, doc: Document, analysis: Analysis, now: datetime | None = None
    ) -> None:
        self._write(
            "INSERT OR REPLACE INTO documents VALUES (?, ?, ?, ?)",
            (doc.id, _ts(now), doc.model_dump_json(), analysis.model_dump_json()),
        )

    def update_analysis(self, analysis: Analysis, now: datetime | None = None) -> None:
        self._write(
            "UPDATE documents SET analysis_json = ?, analyzed_at = ? WHERE doc_id = ?",
            (analysis.model_dump_json(), _ts(now), analysis.doc_id),
        )

    def get(self, doc_id: int) -> StoredDoc | None:
        row = self._db.execute(
            "SELECT document_json, analysis_json FROM documents WHERE doc_id = ?", (doc_id,)
        ).fetchone()
        return _stored(row) if row else None

    def all_docs(self) -> list[StoredDoc]:
        rows = self._db.execute(
            "SELECT document_json, analysis_json FROM documents ORDER BY doc_id"
        ).fetchall()
        return [_stored(r) for r in rows]

    def record_copy(
        self, doc_id: int, target: str, sha256: str, now: datetime | None = None
    ) -> None:
        self._write(
            "INSERT OR REPLACE INTO copies VALUES (?, ?, ?, ?)", (doc_id, target, sha256, _ts(now))
        )

    def copy_sha(self, doc_id: int, target: str) -> str | None:
        row = self._db.execute(
            "SELECT sha256 FROM copies WHERE doc_id = ? AND target = ?", (doc_id, target)
        ).fetchone()
        return str(row[0]) if row else None

    def verified_copies(self, doc_id: int) -> int:
        """The Paperless archive itself counts as one copy."""
        (count,) = self._db.execute(
            "SELECT COUNT(*) FROM copies WHERE doc_id = ?", (doc_id,)
        ).fetchone()
        return 1 + int(count)

    def mark_shredded(self, doc_id: int, pages: int, now: datetime | None = None) -> None:
        self._write("INSERT OR REPLACE INTO shredded VALUES (?, ?, ?)", (doc_id, _ts(now), pages))

    def is_shredded(self, doc_id: int) -> bool:
        row = self._db.execute("SELECT 1 FROM shredded WHERE doc_id = ?", (doc_id,)).fetchone()
        return row is not None

    def shred_stats(self) -> tuple[int, int]:
        count, pages = self._db.execute(
            "SELECT COUNT(*), COALESCE(SUM(pages), 0) FROM shredded"
        ).fetchone()
        return int(count), int(pages)

    def was_notified(self, doc_id: int, due: date, kind: str) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM notified WHERE doc_id = ? AND due = ? AND kind = ?",
            (doc_id, due.isoformat(), kind),
        ).fetchone()
        return row is not None

    def mark_notified(self, doc_id: int, due: date, kind: str) -> None:
        self._write(
            "INSERT OR IGNORE INTO notified VALUES (?, ?, ?)", (doc_id, due.isoformat(), kind)
        )
```

- [x] **Step 4: Run tests**

Run: `uv run pytest tests/test_state.py -v`
Expected: `5 passed`

- [x] **Step 5: Commit and push** with the message `feat: sqlite state store` (plus the trailer).
