"""In-memory fakes shared by the tests."""

from __future__ import annotations

import copy
from typing import Any

from local_paper_ai.ai.schemas import AIDeadlines, AIRetention
from local_paper_ai.models import Document


class FakeRouter:
    def __init__(
        self, retention: AIRetention | None = None, deadlines: AIDeadlines | None = None
    ) -> None:
        self.retention = retention
        self.deadlines = deadlines
        self.calls: list[tuple[str, str]] = []

    def extract(self, system: str, text: str, schema: type[Any], category: str) -> Any:
        self.calls.append((schema.__name__, category))
        if schema is AIRetention:
            return self.retention
        if schema is AIDeadlines:
            return self.deadlines
        return None


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
