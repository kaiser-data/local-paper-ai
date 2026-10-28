"""End-to-end against a real Paperless-ngx. Run with: pytest -m integration."""

import os
import time
import uuid
from datetime import date
from pathlib import Path

import httpx
import pytest

from local_paper_ai.ai.router import Router
from local_paper_ai.analyzer import analyze
from local_paper_ai.paperless import PaperlessClient
from local_paper_ai.rules import load_rules
from local_paper_ai.writer import apply

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


def _pdf(text: str) -> bytes:
    """Smallest valid one-page PDF with a real text layer (no OCR needed)."""
    stream = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


def test_upload_analyze_and_tag() -> None:
    url, token = os.environ.get("LPA_IT_URL"), os.environ.get("LPA_IT_TOKEN")
    if not url or not token:
        pytest.skip("set LPA_IT_URL and LPA_IT_TOKEN")
    http = httpx.Client(base_url=url, headers={"Authorization": f"Token {token}"}, timeout=60)
    title = f"lpa-it-{uuid.uuid4().hex[:8]}"
    pdf = _pdf(f"Kontoauszug Nr. 7 Sparkasse {title}")
    resp = http.post(
        "/api/documents/post_document/",
        files={"document": ("it.pdf", pdf, "application/pdf")},
        data={"title": title},
    )
    resp.raise_for_status()
    hits: list[dict[str, object]] = []
    for _ in range(60):
        hits = http.get("/api/documents/", params={"title__iexact": title}).json()["results"]
        if hits:
            break
        time.sleep(5)
    assert hits, "Paperless did not consume the test PDF within 5 minutes"

    client = PaperlessClient(url, token)
    doc = client.get_document(int(str(hits[0]["id"])))
    assert "Kontoauszug" in doc.content
    analysis = analyze(doc, load_rules(ROOT / "rules.yaml"), Router([], []))
    assert analysis.verdict.kind == "may_shred"
    assert apply(client, analysis, date.today()) is True
    assert apply(client, analysis, date.today()) is False
    assert "lpa:darf-weg" in client.get_document(doc.id).tags
    assert client.download(doc.id).startswith(b"%PDF")
