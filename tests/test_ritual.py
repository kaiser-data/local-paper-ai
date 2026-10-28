from datetime import date
from pathlib import Path

from local_paper_ai.models import Analysis, Document, Verdict
from local_paper_ai.ritual import ritual_view
from local_paper_ai.state import State

TODAY = date(2026, 10, 28)


def _add(state: State, doc_id: int, copies: int = 1, **verdict: object) -> None:
    doc = Document(
        id=doc_id, title=f"D{doc_id}", content="x", created=date(2026, 1, 1), page_count=3
    )
    state.save_analysis(doc, Analysis(doc_id=doc_id, verdict=Verdict(**verdict)))  # type: ignore[arg-type]
    for i in range(copies - 1):
        state.record_copy(doc_id, f"t{i}", "sha")


def test_groups(tmp_path: Path) -> None:
    s = State(tmp_path / "s.db")
    _add(s, 1, copies=2, kind="may_shred")
    _add(s, 2, copies=1, kind="may_shred")
    _add(s, 3, copies=2, kind="keep_until", keep_until=date(2026, 1, 1))
    _add(s, 4, copies=2, kind="keep_until", keep_until=date(2030, 1, 1))
    _add(s, 5, copies=2, kind="keep_original")
    _add(s, 6, copies=2, kind="keep_original", needs_review=True)
    _add(s, 7, copies=2, kind="may_shred")
    s.mark_shredded(7, 3)
    view = ritual_view(s, min_copies=2, today=TODAY)
    ids = lambda items: [i.doc.id for i in items]  # noqa: E731
    assert ids(view.shred_now) == [1, 3]
    assert ids(view.waiting_backup) == [2]
    assert ids(view.keep_until) == [4]
    assert ids(view.keep_original) == [5]
    assert ids(view.review) == [6]
    assert (view.shredded_docs, view.shredded_pages) == (1, 3)
    assert view.kg == 0.015
