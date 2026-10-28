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
        "id": 7,
        "title": "Bescheid",
        "content": "Text",
        "created": "2026-03-14",
        "correspondent": 3,
        "document_type": 5,
        "tags": [1],
        "custom_fields": [],
        "page_count": 2,
        **kw,
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
            json=_raw(
                created="2026-03-14T00:00:00+01:00",
                correspondent=None,
                document_type=None,
                tags=[],
                content=None,
            ),
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
