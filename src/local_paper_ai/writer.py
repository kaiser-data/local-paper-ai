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
