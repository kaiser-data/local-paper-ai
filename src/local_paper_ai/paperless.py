"""Thin client for the Paperless-ngx REST API."""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from local_paper_ai.models import Document


class PaperlessClient:
    def __init__(self, url: str, token: str, timeout: float = 30.0) -> None:
        self._http = httpx.Client(
            base_url=url.rstrip("/"),
            headers={"Authorization": f"Token {token}", "Accept": "application/json"},
            timeout=timeout,
        )
        self._names: dict[str, dict[int, str]] = {}

    def close(self) -> None:
        self._http.close()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        resp = self._http.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    def _all(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Follow pagination. Only next's query is reused: its host may be Paperless' public URL."""
        query: dict[str, Any] = {"page_size": 100, **(params or {})}
        results: list[dict[str, Any]] = []
        while True:
            page = self._get(path, query)
            results.extend(page["results"])
            if not page.get("next"):
                return results
            query = dict(httpx.URL(page["next"]).params)

    def ping(self) -> None:
        self._get("/api/documents/", {"page_size": 1})

    def names(self, kind: str) -> dict[int, str]:
        if kind not in self._names:
            self._names[kind] = {int(r["id"]): str(r["name"]) for r in self._all(f"/api/{kind}/")}
        return self._names[kind]

    def _lookup(self, kind: str, item_id: int) -> str | None:
        if item_id not in self.names(kind):
            self._names.pop(kind, None)  # created elsewhere since we cached
        return self.names(kind).get(item_id)

    def _ensure(self, kind: str, name: str, extra: dict[str, Any]) -> int:
        for item_id, item_name in self.names(kind).items():
            if item_name == name:
                return item_id
        resp = self._http.post(f"/api/{kind}/", json={"name": name, **extra})
        resp.raise_for_status()
        item_id = int(resp.json()["id"])
        self._names[kind][item_id] = name
        return item_id

    def ensure_tag(self, name: str) -> int:
        return self._ensure("tags", name, {})

    def ensure_custom_field(self, name: str, data_type: str) -> int:
        return self._ensure("custom_fields", name, {"data_type": data_type})

    def list_document_ids(self) -> list[int]:
        rows = self._all("/api/documents/", {"fields": "id", "ordering": "id"})
        return [int(r["id"]) for r in rows]

    def get_raw(self, doc_id: int) -> dict[str, Any]:
        data: dict[str, Any] = self._get(f"/api/documents/{doc_id}/")
        return data

    def get_document(self, doc_id: int) -> Document:
        raw = self.get_raw(doc_id)
        tags = [self._lookup("tags", int(t)) for t in raw.get("tags") or []]
        return Document(
            id=int(raw["id"]),
            title=str(raw["title"]),
            content=str(raw.get("content") or ""),
            created=date.fromisoformat(str(raw["created"])[:10]),
            correspondent=self._optional("correspondents", raw.get("correspondent")),
            document_type=self._optional("document_types", raw.get("document_type")),
            tags=[t for t in tags if t is not None],
            page_count=raw.get("page_count"),
        )

    def _optional(self, kind: str, item_id: Any) -> str | None:
        return None if item_id is None else self._lookup(kind, int(item_id))

    def patch_document(self, doc_id: int, data: dict[str, Any]) -> None:
        resp = self._http.patch(f"/api/documents/{doc_id}/", json=data)
        resp.raise_for_status()

    def download(self, doc_id: int) -> bytes:
        resp = self._http.get(f"/api/documents/{doc_id}/download/")
        resp.raise_for_status()
        return resp.content
