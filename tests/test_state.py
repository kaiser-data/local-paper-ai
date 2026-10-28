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
