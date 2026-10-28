# lesspaper — Design

**Date:** 2026-10-28
**Status:** Draft, awaiting review

## Goal

Get rid of paper. Scan household paperwork (letters, contracts, invoices,
certificates, tax documents) with a phone, turn it into searchable, well-named
PDFs stored in several places, and tell the user for every document whether the
paper original may be thrown away.

## Constraints

- **Platform-agnostic.** Works with any phone (primary user: Samsung/Android),
  any desktop OS, any cloud. No iCloud/iOS-only features.
- **Local processing.** Document contents never leave the user's own hardware.
  Cloud copies are encrypted client-side.
- **Runs on a shared Jetson.** The Jetson has another main job. lesspaper
  must stay out of its way: no real-time processing, work only when the device
  is idle, small resident footprint.
- **Not real-time.** A scan may take hours or days to be processed. That is fine.
- **No real documents or personal config in the repository.** Ever.

## Non-goals (YAGNI)

- Own mobile app or own web UI — Paperless-ngx and existing Android apps cover it.
- Own OCR or ML classifier — reuse ocrmypdf/Paperless.
- Multi-user / sharing.
- Legal advice. Retention rules are defaults the user reviews and owns.

## Architecture

```
Phone                     Jetson (only when idle)                    Storage (several)
─────                     ───────────────────────                    ─────────────────
Scan app          ──▶  inbox/ (staging)                       ──▶  1. Paperless archive (Jetson)
(Paperless mobile      │                                           2. Syncthing → desktop + phone
 app, Google Drive     ├─ idle-gate: feeds ONE file at a time      3. rclone crypt → any cloud
 scan, Samsung         │   into Paperless consume/ when idle
 camera scan)          ├─ Paperless-ngx: OCR (deu+eng),
   │                   │   classify (learned matching,
   └─ Syncthing ────▶  │   optional local LLM via Ollama)
      (or upload)      ├─ filename: 2026-03-14_Finanzamt_Steuerbescheid.pdf
                       ├─ retention: ✅ may shred · 📦 keep original · ⏳ keep until YYYY
                       └─ daily report: "papers you can throw away"
```

## Components

Reused (configured, not written):

| Component | Role |
|---|---|
| **Paperless-ngx** (Docker, arm64) | OCR via ocrmypdf, full-text search, web UI, tags, document types, correspondents, learned auto-matching, REST API. SQLite backend to save RAM. |
| **Syncthing** | Phone → Jetson inbox; Jetson export → desktop and phone. Works offline, catches up later. |
| **rclone** (crypt remote) | Encrypted copy of the export to any cloud the user picks. |
| **Ollama** (optional, off by default) | Local LLM for better titles/correspondents. Loaded only during a batch, unloaded afterwards (`keep_alive: 0`). Driven by an existing Paperless LLM add-on (e.g. paperless-gpt) rather than custom code. |
| **ntfy** (optional) | Push notification "N documents processed, M may be shredded". |

Written by us (small Python package `lesspaper`, each unit independently testable):

1. **`idle_gate`** — Watches `inbox/`. Every N minutes reads CPU load, GPU load
   (Jetson `tegrastats`/sysfs) and free RAM. If all are under the configured
   thresholds, moves exactly one file into Paperless' `consume/`, waits until
   Paperless has finished it, then re-checks. Stops after the current document
   as soon as load rises. Files older than `max_wait_days` (default 3) are
   prioritised at the next idle moment. Interface: `is_idle(metrics, thresholds) -> bool`,
   `next_file(inbox) -> Path | None`. Metrics source is pluggable so it also runs
   on non-Jetson Linux/macOS.
2. **`retention`** — Rules engine. Reads `rules.yaml`, matches on Paperless
   document type, tags, correspondent and keywords, returns a verdict:
   `may_shred`, `keep_original`, or `keep_until(date)`. Pure function, no I/O:
   `evaluate(document, rules) -> Verdict`. Ships with German household defaults
   (see below).
3. **`tagger`** — Runs as a Paperless post-consume step / periodic job. Calls
   `retention.evaluate` for every new document and writes the verdict back via
   the Paperless API (tag + custom field `keep_until`). Unclassified documents
   get `needs-review` instead of a guess.
4. **`report`** — Builds the daily shred list (HTML + Markdown) into the synced
   export folder, grouped by verdict, and optionally pushes to ntfy.
5. **`export`** — Uses Paperless' own exporter / filename format
   (`{created_year}-{created_month}-{created_day}_{correspondent}_{title}`) into
   `export/`, then triggers rclone. Syncthing picks up `export/` by itself.

Deployment: one `docker-compose.yml` (Paperless, Redis, Syncthing, the
lesspaper worker; Ollama behind a profile), plus `config.example.yaml`.

## Retention defaults (German households)

Configurable in `rules.yaml`; shipped with a "not legal advice" notice.

| Verdict | Examples |
|---|---|
| 📦 **Keep original** | Birth/marriage/death certificates, notarised contracts, wills, land register documents, vehicle title (Zulassungsbescheinigung Teil II), school/university certificates and diplomas, pension records (Rentenunterlagen), loan and guarantee contracts |
| ⏳ **Keep until** | Tax assessments: until final + buffer (default 10 years); craftsman/household-service invoices for property: 2 years (§14b UStG); warranty-relevant purchase receipts: warranty period; self-employed business records: configurable (default 10 years) |
| ✅ **May shred** | Everything else once scanned and verified: bank statements, utility bills, insurance letters, general correspondence, payslips (digital copy kept) |

Rule of thumb built into the engine: if unsure → `keep_original` + `needs-review`.
Nothing is ever marked shreddable before its PDF exists in at least two
storage locations.

## Data flow & error handling

- **Scan arrives while Jetson busy/offline:** stays in phone/inbox via Syncthing; no loss.
- **OCR or classification fails:** Paperless keeps the file; tagger marks
  `needs-review`; report lists it under "check manually".
- **Load rises mid-document:** current document finishes; gate pauses.
- **Duplicate scan:** Paperless duplicate detection (checksum) rejects it; report mentions it.
- **Backup target unreachable:** rclone retries next run; report shows last
  successful backup per target. "May shred" is withheld while fewer than two
  copies are confirmed.

## Testing

- Unit tests (pytest): `retention.evaluate` against a table of example documents;
  `idle_gate.is_idle` / `next_file` with fake metrics and temp dirs; report rendering.
- Integration: docker-compose up with Paperless, feed sample PDFs (synthetic,
  committed under `tests/fixtures/`), assert tags/custom fields via API.
- No real documents in fixtures.

## Repository layout

```
lesspaper/
├── docker-compose.yml
├── config.example.yaml
├── rules.yaml                 # retention defaults
├── src/lesspaper/
│   ├── idle_gate.py
│   ├── retention.py
│   ├── tagger.py
│   ├── report.py
│   └── export.py
├── tests/
└── docs/
```

## Open questions

- Jetson model, free RAM and disk — to verify before deployment (read-only check, with the user's go-ahead).
- Which cloud(s) for the encrypted copy.
