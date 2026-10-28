### Task 7: Paperless-ngx REST client

**Files:**
- Create: `src/local_paper_ai/paperless.py`
- Test: `tests/test_paperless.py`

**Interfaces:**
- Consumes: `Document` (Task 2).
- Produces: `PaperlessClient(url, token, timeout=30.0)` with:
  - `ping()`
  - `names(kind) -> dict[int, str]` (kind ∈ tags/correspondents/document_types/custom_fields; cached, reloaded once when an id is unknown)
  - `ensure_tag(name) -> int`, `ensure_custom_field(name, data_type) -> int`
  - `list_document_ids() -> list[int]`, `get_raw(doc_id) -> dict`, `get_document(doc_id) -> Document`
  - `patch_document(doc_id, data)`, `download(doc_id) -> bytes`, `close()`
- Every method raises `httpx.HTTPError` on failure. The auth header is `Authorization: Token <token>`.

- [ ] **Step 1: Write the failing test** `tests/test_paperless.py`

```python
from datetime import date
from typing import Any

import httpx
import respx

from local_paper_ai.paperless import PaperlessClient

BASE = "http://pl"


def _page(results: list[dict[str, Any]], next_url: str | None = None) -> httpx.Response:
    return httpx.Response(200, json={"count": len(results), "next": next_url, "results": results})


def _names(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(path="/api/correspondents/").mock(
        return_value=_page([{"id": 3, "name": "Finanzamt"}])
    )
    respx_mock.get(path="/api/document_types/").mock(
        return_value=_page([{"id": 5, "name": "Steuerbescheid"}])
    )
    respx_mock.get(path="/api/tags/").mock(return_value=_page([{"id": 1, "name": "inbox"}]))


def _raw(**kw: Any) -> dict[str, Any]:
    return {
        "id": 7, "title": "Bescheid", "content": "Text", "created": "2026-03-14",
        "correspondent": 3, "document_type": 5, "tags": [1], "custom_fields": [],
        "page_count": 2, **kw,
    }


def test_get_document_resolves_names(respx_mock: respx.MockRouter) -> None:
    _names(respx_mock)
    respx_mock.get(f"{BASE}/api/documents/7/").mock(return_value=httpx.Response(200, json=_raw()))
    doc = PaperlessClient(BASE, "tok").get_document(7)
    assert doc.correspondent == "Finanzamt"
    assert doc.document_type == "Steuerbescheid"
    assert doc.tags == ["inbox"]
    assert doc.created == date(2026, 3, 14)
    assert doc.page_count == 2
    assert respx_mock.calls[0].request.headers["Authorization"] == "Token tok"


def test_created_as_datetime_and_missing_names(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{BASE}/api/documents/7/").mock(
        return_value=httpx.Response(
            200,
            json=_raw(created="2026-03-14T00:00:00+01:00", correspondent=None,
                      document_type=None, tags=[], content=None),
        )
    )
    doc = PaperlessClient(BASE, "tok").get_document(7)
    assert doc.created == date(2026, 3, 14)
    assert doc.correspondent is None and doc.content == ""


def test_list_ids_follows_pagination(respx_mock: respx.MockRouter) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("page") == "2":
            return _page([{"id": 3}])
        return _page([{"id": 1}, {"id": 2}], next_url="http://elsewhere/api/documents/?page=2")

    respx_mock.get(path="/api/documents/").mock(side_effect=handler)
    assert PaperlessClient(BASE, "tok").list_document_ids() == [1, 2, 3]


def test_ensure_tag_creates_once(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(path="/api/tags/").mock(return_value=_page([{"id": 1, "name": "inbox"}]))
    create = respx_mock.post(f"{BASE}/api/tags/").mock(
        return_value=httpx.Response(201, json={"id": 9, "name": "lpa:darf-weg"})
    )
    client = PaperlessClient(BASE, "tok")
    assert client.ensure_tag("inbox") == 1
    assert client.ensure_tag("lpa:darf-weg") == 9
    assert client.ensure_tag("lpa:darf-weg") == 9
    assert create.call_count == 1


def test_ensure_custom_field_sends_type(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(path="/api/custom_fields/").mock(return_value=_page([]))
    create = respx_mock.post(f"{BASE}/api/custom_fields/").mock(
        return_value=httpx.Response(201, json={"id": 4, "name": "lpa_keep_until"})
    )
    assert PaperlessClient(BASE, "tok").ensure_custom_field("lpa_keep_until", "date") == 4
    assert b'"data_type":"date"' in create.calls.last.request.content.replace(b" ", b"")


def test_patch_and_download(respx_mock: respx.MockRouter) -> None:
    patch = respx_mock.patch(f"{BASE}/api/documents/7/").mock(return_value=httpx.Response(200))
    respx_mock.get(f"{BASE}/api/documents/7/download/").mock(
        return_value=httpx.Response(200, content=b"%PDF-1.4")
    )
    client = PaperlessClient(BASE, "tok")
    client.patch_document(7, {"tags": [1, 9]})
    assert patch.calls.last.request.content.replace(b" ", b"") == b'{"tags":[1,9]}'
    assert client.download(7) == b"%PDF-1.4"
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_paperless.py -v`
Expected: FAIL with `No module named 'local_paper_ai.paperless'`

- [ ] **Step 3: Implement** `src/local_paper_ai/paperless.py`

```python
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
```

Note: the `respx_mock` fixture fails a test when a mocked route is never called, so each test mocks only the endpoints it actually hits.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_paperless.py -v`
Expected: `6 passed`

- [ ] **Step 5: Commit and push** with the message `feat: paperless-ngx REST client` (plus the trailer).
