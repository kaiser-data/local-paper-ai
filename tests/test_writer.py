from datetime import date

from local_paper_ai.models import Analysis, Deadline, Document, Evidence, Verdict
from local_paper_ai.writer import apply, mark_shredded
from tests.fakes import FakePaperless

TODAY = date(2026, 10, 28)


def _store() -> FakePaperless:
    store = FakePaperless()
    store.add(
        Document(id=1, title="t", content="c", created=date(2026, 3, 14)),
        tags=[1],
        custom_fields=[{"field": 1, "value": "bleibt"}],
    )
    return store


def _analysis(kind: str = "may_shred", **kw: object) -> Analysis:
    verdict = Verdict(
        kind=kind,  # type: ignore[arg-type]
        category="finance",
        evidence=[Evidence(quote="Kontoauszug", source="rule", detail="Regel kontoauszug")],
        **kw,  # type: ignore[arg-type]
    )
    return Analysis(doc_id=1, verdict=verdict)


def test_apply_sets_verdict_tag_and_keeps_foreign_tags() -> None:
    store = _store()
    assert apply(store, _analysis(), TODAY) is True
    assert store.tag_names(1) == {"inbox", "lpa:darf-weg"}
    assert store.field_values(1)["fremd"] == "bleibt"
    assert store.field_values(1)["lpa_reason"] == "Regel kontoauszug: »Kontoauszug«"


def test_apply_is_idempotent() -> None:
    store = _store()
    apply(store, _analysis(), TODAY)
    assert apply(store, _analysis(), TODAY) is False
    assert len(store.patches) == 1


def test_verdict_change_replaces_own_tag_and_review_tag() -> None:
    store = _store()
    apply(store, _analysis(), TODAY)
    apply(store, _analysis("keep_original", needs_review=True), TODAY)
    assert store.tag_names(1) == {"inbox", "lpa:original-aufheben", "lpa:pruefen"}


def test_fields_keep_until_and_next_future_deadline() -> None:
    store = _store()
    a = _analysis("keep_until", keep_until=date(2036, 3, 14))
    ev = Evidence(quote="zahlbar bis", source="regex")
    a.deadlines = [
        Deadline(due=date(2026, 4, 1), label="alt", evidence=ev),
        Deadline(due=date(2026, 11, 30), label="neu", evidence=ev),
    ]
    apply(store, a, TODAY)
    fields = store.field_values(1)
    assert fields["lpa_keep_until"] == "2036-03-14"
    assert fields["lpa_next_deadline"] == "2026-11-30"


def test_mark_shredded_adds_tag_once() -> None:
    store = _store()
    mark_shredded(store, 1)
    mark_shredded(store, 1)
    assert store.tag_names(1) == {"inbox", "lpa:entsorgt"}
    assert len(store.patches) == 1
