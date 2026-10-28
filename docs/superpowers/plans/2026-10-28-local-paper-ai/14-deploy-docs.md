### Task 14: Deployment, live integration test, README

**Files:**
- Create: `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `.env.example`, `tests/integration/__init__.py` (empty), `tests/integration/test_paperless_live.py`
- Modify: `.github/workflows/ci.yml` (add the `docker` and `integration` jobs), `README.md`

**Interfaces:**
- Consumes: CLI commands `run` and `serve` (Task 13); `PaperlessClient`, `analyze`, `apply`, `Router`, `load_rules`.
- Produces: an image that runs `local-paper-ai run`/`serve`; a compose stack; a CI job that proves the whole path against a **real** Paperless-ngx.

- [x] **Step 1: Live integration test** `tests/integration/test_paperless_live.py`

```python
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
```

Run: `uv run pytest -q`
Expected: the integration test is **deselected** by the default `-m 'not integration'`, so the unit tests pass as before.

- [x] **Step 2: Container** `Dockerfile`

```dockerfile
FROM python:3.12-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends rclone ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.7.13 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev
COPY rules.yaml config.example.yaml ./
ENV PATH="/app/.venv/bin:$PATH" LPA_CONFIG=/config/config.yaml
CMD ["local-paper-ai", "run"]
```

`.dockerignore`:
```
.git
.venv
data
config.yaml
.env
**/__pycache__
.pytest_cache
.mypy_cache
.ruff_cache
```

- [x] **Step 3: Compose stack** `docker-compose.yml`

```yaml
# local-paper-ai stack. Works on amd64 and arm64 (Jetson, Raspberry Pi, NAS, desktop).
#   cp config.example.yaml config.yaml && cp .env.example .env   # then edit both
#   docker compose up -d                  # without local AI
#   docker compose --profile ai up -d     # with Ollama
x-lpa: &lpa
  build: .
  restart: unless-stopped
  depends_on: [paperless]
  env_file: .env
  volumes:
    - ./config.yaml:/config/config.yaml:ro
    - ./rules.yaml:/app/rules.yaml            # writable: the review page appends rules
    - ./data/inbox:/data/inbox
    - ./data/consume:/data/consume
    - ./data/export-sync:/data/export-sync
    - ./data/lpa:/data/lpa
    - ${RCLONE_CONFIG_DIR:-./data/rclone}:/root/.config/rclone:ro

services:
  broker:
    image: docker.io/library/redis:7
    restart: unless-stopped
    volumes: [redisdata:/data]

  paperless:
    image: ghcr.io/paperless-ngx/paperless-ngx:${PAPERLESS_TAG:-latest}   # pin a version in .env
    restart: unless-stopped
    depends_on: [broker]
    ports: ["8000:8000"]
    env_file: .env
    environment:
      PAPERLESS_REDIS: redis://broker:6379
      PAPERLESS_OCR_LANGUAGE: deu+eng
      PAPERLESS_TIME_ZONE: Europe/Berlin
      PAPERLESS_FILENAME_FORMAT: "{{ created_year }}/{{ correspondent }}/{{ created }}_{{ title }}"
      PAPERLESS_WEBSERVER_WORKERS: 1      # small-box footprint
      PAPERLESS_TASK_WORKERS: 1
      PAPERLESS_THREADS_PER_WORKER: 1
    volumes:
      - paperless-data:/usr/src/paperless/data
      - paperless-media:/usr/src/paperless/media
      - ./data/consume:/usr/src/paperless/consume

  syncthing:
    image: docker.io/syncthing/syncthing:1
    restart: unless-stopped
    ports: ["8384:8384", "22000:22000/tcp", "22000:22000/udp", "21027:21027/udp"]
    volumes:
      - syncthing-config:/var/syncthing
      - ./data/inbox:/data/inbox            # phone -> box
      - ./data/export-sync:/data/export-sync  # box -> desktop + phone

  worker:
    <<: *lpa
    command: ["local-paper-ai", "run"]

  web:
    <<: *lpa
    command: ["local-paper-ai", "serve"]
    ports: ["8765:8765"]

  ollama:
    profiles: [ai]
    # On a Jetson use the jetson-containers image (dustynv/ollama) for CUDA instead.
    image: docker.io/ollama/ollama:latest
    restart: unless-stopped
    environment:
      OLLAMA_KEEP_ALIVE: "0"          # unload after every request
      OLLAMA_MAX_LOADED_MODELS: "1"
    volumes: [ollama:/root/.ollama]

volumes:
  redisdata:
  paperless-data:
  paperless-media:
  syncthing-config:
  ollama:
```

`.env.example`:
```
# Copy to .env and fill in. Never commit .env.
PAPERLESS_TAG=latest
PAPERLESS_SECRET_KEY=change-me-to-a-long-random-string
PAPERLESS_URL=http://localhost:8000
PAPERLESS_ADMIN_USER=admin
PAPERLESS_ADMIN_PASSWORD=change-me
# Create in Paperless: profile -> API token
PAPERLESS_TOKEN=
# Random string; the calendar URL becomes http://<box>:8765/calendar.ics?token=<this>
LPA_CALENDAR_TOKEN=
# Only if you enable the optional cloud provider in config.yaml
ANTHROPIC_API_KEY=
```

- [x] **Step 4: CI jobs.** Append them to `.github/workflows/ci.yml` under `jobs:`.

```yaml
  docker:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-qemu-action@v3
      - uses: docker/setup-buildx-action@v3
      - uses: docker/build-push-action@v6
        with:
          context: .
          platforms: linux/amd64,linux/arm64
          push: false

  integration:
    runs-on: ubuntu-latest
    services:
      redis:
        image: redis:7
      paperless:
        image: ghcr.io/paperless-ngx/paperless-ngx:latest
        env:
          PAPERLESS_REDIS: redis://redis:6379
          PAPERLESS_ADMIN_USER: it
          PAPERLESS_ADMIN_PASSWORD: it-password
          PAPERLESS_SECRET_KEY: ci-only-not-secret
        ports: ["8000:8000"]
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --frozen
      - name: Wait for Paperless
        run: |
          for i in $(seq 1 60); do
            code=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/api/ || true)
            echo "attempt $i: HTTP $code"
            if [ "$code" != "000" ]; then exit 0; fi
            sleep 5
          done
          exit 1
      - name: Run live test
        run: |
          token=$(curl -sf -X POST -d username=it -d password=it-password \
            http://localhost:8000/api/token/ | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')
          LPA_IT_URL=http://localhost:8000 LPA_IT_TOKEN="$token" uv run pytest -m integration -v
```

- [x] **Step 5: README.** Replace `README.md` with a user guide covering these sections, in this order:
  1. **Title and pitch.** Keep the current pitch and bullet list. Say that it is a companion for Paperless-ngx, and that tagging/titles can come from paperless-gpt or paperless-ai.
  2. **How it works.** Use the architecture diagram from the spec.
  3. **Quick start.**
     - Clone the repo.
     - Copy `config.example.yaml` to `config.yaml` and `.env.example` to `.env`.
     - Run `docker compose up -d`.
     - Create the Paperless API token and put it in `.env`.
     - Run `docker compose restart worker web`.
     - Run `docker compose exec worker local-paper-ai doctor`.
  4. **Scanning from your phone (any phone).** Install Syncthing on the phone and share a folder with the box's `data/inbox`. Scan with any app that saves PDFs into that folder: the Samsung camera document scan, the Google Drive scanner, or the Files app on iOS.
  5. **Getting your files everywhere.**
     - Syncthing shares `data/export-sync` with desktop and phone.
     - Optionally add an encrypted cloud copy: create an rclone crypt remote with `rclone config`, then add an `rclone` target.
  6. **Deadlines in your calendar.** Explain the subscribe URL with the token, and that ntfy push is optional.
  7. **Running on a Jetson / small box.**
     - Explain the idle gate and its thresholds.
     - Free RAM vs. available RAM.
     - Use `dustynv/ollama`.
     - Recommended models: `phi4-mini` for analysis, `qwen3.5:0.8b` as the fallback.
     - Set `ollama_unload: true`.
  8. **Privacy.**
     - The default config has no cloud provider.
     - `local_only` categories and how the "unknown" category stays local.
     - Grounding: every claim quotes the document.
  9. **Retention rules.** How `rules.yaml` works, saving rules from `/review`, and a bold **"not legal advice"** notice.
  10. **CLI reference.** One line per command.
  11. **Development.** The `uv` commands, plus `pytest -m integration` with `LPA_IT_URL`/`LPA_IT_TOKEN`.
  12. **License.** MIT.

- [x] **Step 6: Verify locally what can be verified here.** Run `uv run ruff format . && uv run ruff check . && uv run mypy && uv run pytest -q`. All must pass. Docker isn't installed on the dev Mac, so the CI `docker` and `integration` jobs are the proof for those parts.

- [x] **Step 7: Commit and push** with the message `feat: docker deployment, live integration test and user guide` (plus the trailer). Then run `gh run watch --exit-status` (or `gh run list --limit 1` until it finishes) and confirm all three jobs (`test`, `docker`, `integration`) are green. If `integration` fails, read the log (`gh run view --log-failed`) and fix the cause. Do not skip the test.
