### Task 8: Writer (tags + custom fields, idempotent)

**Files:**
- Create: `src/local_paper_ai/writer.py`
- Modify: `tests/fakes.py` (append `FakePaperless`)
- Test: `tests/test_writer.py`

**Interfaces:**
- Consumes: `Analysis`, `VerdictKind` (Task 2). It structurally matches `PaperlessClient` (Task 7).
- Produces:
  - `Protocol DocumentStore { get_raw(doc_id) -> dict; ensure_tag(name) -> int; ensure_custom_field(name, data_type) -> int; patch_document(doc_id, data) -> None }`
  - constants `TAG_FOR`, `TAG_REVIEW = "lpa:pruefen"`, `TAG_SHREDDED = "lpa:entsorgt"`, `FIELD_TYPES`
  - `desired_fields(analysis, today) -> dict[str, str | None]`
  - `apply(store, analysis, today) -> bool` (True if it patched)
  - `mark_shredded(store, doc_id) -> None`
- Produces (tests): `tests.fakes.FakePaperless` with `add(doc, tags=None, custom_fields=None)`, `tag_names(doc_id) -> set[str]`, `.patches`, `.downloads`. It also implements `list_document_ids`, `get_document` and `download`.

- [ ] **Step 1: Append to `tests/fakes.py`.** Add `import copy` to the imports at the top, plus `from local_paper_ai.models import Document`.

```python
class FakePaperless:
    """In-memory Paperless: DocumentStore + Downloader + list/get."""

    def __init__(self) -> None:
        self.tags: dict[str, int] = {"inbox": 1}
        self.fields: dict[str, int] = {"fremd": 1}
        self.raw: dict[int, dict[str, Any]] = {}
        self.documents: dict[int, Document] = {}
        self.files: dict[int, bytes] = {}
        self.patches: list[tuple[int, dict[str, Any]]] = []
        self.downloads = 0

    def add(
        self,
        doc: Document,
        tags: list[int] | None = None,
        custom_fields: list[dict[str, Any]] | None = None,
    ) -> None:
        self.documents[doc.id] = doc
        self.raw[doc.id] = {"id": doc.id, "tags": tags or [], "custom_fields": custom_fields or []}
        self.files[doc.id] = f"%PDF-1.4 fake {doc.id}".encode()

    def ensure_tag(self, name: str) -> int:
        if name not in self.tags:
            self.tags[name] = max(self.tags.values()) + 1
        return self.tags[name]

    def ensure_custom_field(self, name: str, data_type: str) -> int:
        if name not in self.fields:
            self.fields[name] = max(self.fields.values()) + 1
        return self.fields[name]

    def get_raw(self, doc_id: int) -> dict[str, Any]:
        return copy.deepcopy(self.raw[doc_id])

    def patch_document(self, doc_id: int, data: dict[str, Any]) -> None:
        self.patches.append((doc_id, copy.deepcopy(data)))
        self.raw[doc_id].update(copy.deepcopy(data))

    def list_document_ids(self) -> list[int]:
        return sorted(self.documents)

    def get_document(self, doc_id: int) -> Document:
        return self.documents[doc_id]

    def download(self, doc_id: int) -> bytes:
        self.downloads += 1
        return self.files[doc_id]

    def tag_names(self, doc_id: int) -> set[str]:
        by_id = {v: k for k, v in self.tags.items()}
        return {by_id[t] for t in self.raw[doc_id]["tags"]}

    def field_values(self, doc_id: int) -> dict[str, Any]:
        by_id = {v: k for k, v in self.fields.items()}
        return {by_id[f["field"]]: f["value"] for f in self.raw[doc_id]["custom_fields"]}
```

- [ ] **Step 2: Write the failing test** `tests/test_writer.py`

```python
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
```

- [ ] **Step 3: Run it**

Run: `uv run pytest tests/test_writer.py -v`
Expected: FAIL with `No module named 'local_paper_ai.writer'`

- [ ] **Step 4: Implement** `src/local_paper_ai/writer.py`

```python
"""Write analyses back to Paperless as tags and custom fields (idempotent)."""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol

from local_paper_ai.models import Analysis, VerdictKind

TAG_FOR: dict[VerdictKind, str] = {
    "may_shred": "lpa:darf-weg",
    "keep_original": "lpa:original-aufheben",
    "keep_until": "lpa:aufbewahren-bis",
}
TAG_REVIEW = "lpa:pruefen"
TAG_SHREDDED = "lpa:entsorgt"
FIELD_TYPES = {"lpa_keep_until": "date", "lpa_next_deadline": "date", "lpa_reason": "string"}
_REASON_MAX = 128  # Paperless string custom fields hold at most 128 characters


class DocumentStore(Protocol):
    def get_raw(self, doc_id: int) -> dict[str, Any]: ...
    def ensure_tag(self, name: str) -> int: ...
    def ensure_custom_field(self, name: str, data_type: str) -> int: ...
    def patch_document(self, doc_id: int, data: dict[str, Any]) -> None: ...


def desired_fields(analysis: Analysis, today: date) -> dict[str, str | None]:
    verdict = analysis.verdict
    upcoming = [d.due for d in analysis.deadlines if d.due >= today]
    reason = "; ".join(
        f"{e.detail}: »{e.quote}«" if e.detail else f"»{e.quote}«" for e in verdict.evidence
    )
    return {
        "lpa_keep_until": verdict.keep_until.isoformat() if verdict.keep_until else None,
        "lpa_next_deadline": min(upcoming).isoformat() if upcoming else None,
        "lpa_reason": reason[:_REASON_MAX] or None,
    }


def apply(store: DocumentStore, analysis: Analysis, today: date) -> bool:
    raw = store.get_raw(analysis.doc_id)
    own = {store.ensure_tag(name) for name in [*TAG_FOR.values(), TAG_REVIEW]}
    want = {store.ensure_tag(TAG_FOR[analysis.verdict.kind])}
    if analysis.verdict.needs_review:
        want.add(store.ensure_tag(TAG_REVIEW))
    current_tags = {int(t) for t in raw.get("tags", [])}
    new_tags = (current_tags - own) | want

    current_fields = {int(f["field"]): f["value"] for f in raw.get("custom_fields", [])}
    new_fields = dict(current_fields)
    for name, value in desired_fields(analysis, today).items():
        field_id = store.ensure_custom_field(name, FIELD_TYPES[name])
        if value is None:
            new_fields.pop(field_id, None)
        else:
            new_fields[field_id] = value

    patch: dict[str, Any] = {}
    if new_tags != current_tags:
        patch["tags"] = sorted(new_tags)
    if new_fields != current_fields:
        patch["custom_fields"] = [{"field": k, "value": v} for k, v in sorted(new_fields.items())]
    if patch:
        store.patch_document(analysis.doc_id, patch)
    return bool(patch)


def mark_shredded(store: DocumentStore, doc_id: int) -> None:
    tags = {int(t) for t in store.get_raw(doc_id).get("tags", [])}
    tag = store.ensure_tag(TAG_SHREDDED)
    if tag not in tags:
        store.patch_document(doc_id, {"tags": sorted(tags | {tag})})
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_writer.py tests/test_analyzer.py -v`
Expected: all pass (`5 passed` for writer).

- [ ] **Step 6: Commit and push** with the message `feat: idempotent paperless writer` (plus the trailer).
