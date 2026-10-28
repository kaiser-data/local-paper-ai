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

    def save_analysis(self, doc: Document, analysis: Analysis, now: datetime | None = None) -> None:
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
