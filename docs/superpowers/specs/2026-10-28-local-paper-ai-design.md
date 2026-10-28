# local-paper-ai — Design

**Date:** 2026-10-28
**Status:** Approved (v1 scope: all six features)

## Goal

Get rid of paper. Scan household paperwork (letters, contracts, invoices,
certificates, tax documents) with any phone, turn it into searchable,
well-named PDFs stored in several verified places, and answer for every
document the question no other tool answers: **"May I throw the paper away?"**
— with a reason, and with any deadline hidden in the letter turned into a
reminder.

## Positioning

Paperless-ngx (47k★) already does OCR, search and storage. paperless-ai (6k★)
and paperless-gpt (2.8k★) already do LLM tagging. local-paper-ai does **not**
rebuild any of that. It is a **companion service for Paperless-ngx** that adds:

1. Retention verdicts with evidence ("may shred / keep original / keep until")
2. Deadline detection (appeal periods, payment due dates, notice periods)
3. A provider-agnostic AI adapter, local by default, with per-category privacy locks
4. An idle gate that only works when a shared box (e.g. Jetson) is free
5. Verified multi-location backup — nothing is shreddable until ≥2 verified copies exist
6. A "shred ritual" web page: tick off the papers you binned, watch the pile shrink

It talks to Paperless only via its REST API, so it is a separate program
(MIT) and works with any existing Paperless install.

## Constraints

- **Platform-agnostic.** Any phone (primary user: Samsung/Android), any
  desktop OS, any calendar app, any cloud. No iCloud/iOS-only features.
- **Local by default.** Document contents never leave the user's hardware
  unless the user explicitly enables a cloud provider for a category.
  Tax, health and identity documents can never be sent to the cloud.
- **Shared, small hardware.** Reference target: Jetson Orin Nano 8 GB
  (unified CPU/GPU memory; CUDA allocations fail instead of slowing down
  under memory pressure). Must not disturb the box's main workload.
- **Not real-time.** Processing may lag hours or days.
- **No real documents or personal config in the repository.**

## Non-goals (YAGNI)

- Own OCR, own DMS, own mobile app, own LLM tagger (use Paperless /
  paperless-gpt for titles and tags).
- Multi-user, sharing, permissions.
- Legal advice. Retention rules are editable defaults with a disclaimer.

## Architecture

```
Phone (any)            Box (Jetson / any Linux / Mac)                         Outputs
───────────            ─────────────────────────────                          ───────
Scan app ──Syncthing─▶ inbox/
                         │ idle_gate (one file at a time, only when idle)
                         ▼
                       Paperless-ngx consume/ ─▶ OCR, search, archive
                         │ (REST API)
                         ▼
                       local-paper-ai worker
                         ├─ analyzer: rules + AI adapter → Verdict, Deadlines (with evidence)
                         ├─ writer: tags + custom fields back to Paperless
                         ├─ backup: export → targets → sha256 verify → state.db
                         └─ notifier: ntfy push                                ─▶ phone push
                       local-paper-ai web (FastAPI)
                         ├─ /            shred ritual (tick off binned papers) ─▶ any browser
                         ├─ /review      documents needing a human decision
                         └─ /calendar.ics deadline feed (subscribe once)       ─▶ any calendar
```

## Components (package `local_paper_ai`)

Each unit has one job, a small interface, and is testable without the others.

| Module | Responsibility | Interface (sketch) | Depends on |
|---|---|---|---|
| `config` | Load + validate `config.yaml` (pydantic) | `load(path) -> Config` | — |
| `paperless` | Thin REST client: list/get docs, content, tags, custom fields, tasks | `PaperlessClient` | httpx |
| `metrics` | Read load: CPU, GPU, **free** RAM. Providers: `jetson` (tegrastats/sysfs), `psutil` (generic) | `read() -> Metrics` | psutil |
| `idle_gate` | Decide idle; feed one inbox file into consume/, wait for task end | `is_idle(m, t) -> bool`, `next_file(inbox, max_wait) -> Path?`, `run_once()` | metrics, paperless |
| `ai` | Provider-agnostic LLM adapter (see below) | `LLM.extract(text, schema) -> model` | httpx |
| `rules` | Load `rules.yaml`; deterministic matching on doc type, tags, correspondent, keywords | `match(doc, rules) -> RuleHit?` | — |
| `analyzer` | Combine rules + AI into `Verdict` and `Deadline[]`, each with evidence; grounding check | `analyze(doc, rules, llm) -> Analysis` | rules, ai |
| `writer` | Write analysis back as tags + custom fields; idempotent | `apply(client, doc_id, analysis)` | paperless |
| `backup` | Export PDFs to targets (local dir, rclone remote), verify sha256, record in state | `sync() -> Report`, `verified_copies(doc_id) -> int` | state |
| `state` | SQLite: processed docs, backup verifications, shred log | `State` | sqlite3 |
| `notify` | ntfy push (optional) | `send(title, body)` | httpx |
| `calendar` | Build RFC 5545 feed from deadlines (folded lines, stable UIDs, SEQUENCE) | `build_ics(deadlines) -> str` | — |
| `web` | FastAPI + Jinja + vendored htmx: ritual, review, calendar | `app` | all of the above |
| `cli` | `local-paper-ai run | gate | analyze ID | backup | serve | doctor` | Typer | all |

### AI adapter

- One protocol, `LLM.extract(text, schema)`, returning a pydantic model
  (JSON mode / structured output).
- Providers:
  - `openai_compatible`: covers Ollama (`/v1`), llama.cpp server, LiteLLM,
    vLLM, OpenRouter, OpenAI.
  - `anthropic`: optional.
  - `none`: rules only.
- **Privacy lock:** each request carries the document's category. Cloud
  providers are refused for categories listed in `privacy.local_only`
  (default: tax, health, identity, finance), and the call falls back to the
  local provider or to rules only. The default config has no cloud provider
  at all.
- **Small-box discipline:**
  - Ollama requests set `keep_alive: 0`, so the model is unloaded after each
    document.
  - Before a call, the adapter checks free RAM against `model_budget_mb` and
    skips (retrying later) instead of risking an out-of-memory failure.
  - Recommended models: `phi4-mini` (~18 tok/s on Orin Nano) for analysis,
    `qwen3.5:0.8b` as the low-memory fallback.
- **Grounding:** every AI claim must include a verbatim quote from the
  document. The analyzer accepts a claim only if the normalized quote is
  found in the OCR text. Ungrounded claims are dropped, and the document is
  marked `needs-review`.

### Retention verdict

Order: deterministic rule hit → AI suggestion (grounded) → fallback.

| Verdict | Examples (German household defaults in `rules.yaml`) |
|---|---|
| 📦 `keep_original` | Birth/marriage/death certificates, notarised deeds, wills, land register, Zulassungsbescheinigung Teil II, school/university certificates, pension records, loan/guarantee contracts |
| ⏳ `keep_until(date)` | Tax assessments (default 10 y), craftsman invoices for property (2 y, §14b UStG), receipts under warranty (warranty period), business records (configurable) |
| ✅ `may_shred` | Bank statements, utility bills, insurance letters, payslips, general correspondence — once backed up |

Safety rules:
- Unsure (no rule hit, no grounded AI answer) → `keep_original` + `needs-review`.
- `may_shred` is **displayed as shreddable only when `verified_copies ≥ min_copies` (default 2)**.
  Until then it shows "waiting for backup".

### Deadline detection

- Regex pre-pass for German date phrases ("zahlbar bis 14.03.2026",
  "innerhalb eines Monats nach Bekanntgabe", "Kündigungsfrist …"), then the
  AI resolves relative periods against the document date. Every deadline is
  grounded with a quote.
- Output:
  - Paperless custom field `next_deadline`
  - entry in `/calendar.ics` (subscribable from Google Calendar, Samsung
    Calendar, Thunderbird, Outlook)
  - ntfy push 7 days and 1 day before
- The calendar feed has no organiser or e-mail fields. It is served only on
  the LAN, or with a token in the URL.

### Idle gate

- Every `interval_s` (default 900 s) the gate reads metrics. It counts as
  idle when CPU < `cpu_max`, GPU < `gpu_max` and **free** RAM >
  `ram_free_min_mb`.
- When idle, it feeds one file, waits for the Paperless task to finish,
  analyzes that document, then checks again. When load rises, it stops
  after the current document.
- Files older than `max_wait_days` go first. Analysis of documents that
  were uploaded directly to Paperless, bypassing the inbox, is gated the
  same way.

### Backup verification

- Targets are configured as a list:
  - `local` (e.g. a Syncthing-shared folder → desktop + phone)
  - `rclone` (any remote; crypt recommended)
- Export filename:
  `{created:%Y-%m-%d}_{correspondent}_{title}.pdf`
  (sanitized, collision-safe).
- Verification:
  - `local`: recompute sha256.
  - `rclone`: `rclone hashsum`, or `rclone cryptcheck` for crypt remotes.
  - Results go into `state.db`.
- The archive inside Paperless counts as one copy.

### Shred ritual (web)

- `/` lists papers that are shreddable now (verified), grouped by verdict.
  The papers to keep are shown separately as "put into the originals folder".
- One click marks a paper as binned. This sets the tag `shredded`, logs the
  date, and updates a counter ("142 sheets gone, 3.1 kg less paper").
- `/review` lists the `needs-review` documents. You pick a verdict manually,
  and that choice can be saved as a new rule.
- The web UI binds to the LAN by default. Optional basic auth.

## Data flow & error handling

| Situation | Behaviour |
|---|---|
| Box busy/offline | Scans wait in Syncthing/inbox; nothing lost |
| OCR fails / empty text | `needs-review`, listed in `/review` |
| LLM unavailable / OOM risk | Skip AI, retry next idle window; rules-only verdict if a rule hits |
| Ungrounded AI claim | Dropped; `needs-review` |
| Backup target unreachable | Retry next run; doc stays "waiting for backup" |
| Duplicate scan | Paperless rejects (checksum); counted in report |
| Paperless API down | Worker backs off exponentially; `doctor` reports it |
| Re-run on same doc | Idempotent: same analysis → no writes |

## Testing

- **Unit (pytest):**
  - `rules.match` and `analyzer.analyze` against a table of synthetic
    documents (rules hit / AI grounded / AI hallucinating / empty text)
  - `idle_gate` with fake metrics
  - `calendar.build_ics` (RFC 5545 folding, UIDs)
  - `backup` with temp dirs
  - privacy lock: cloud refused for local-only categories
- **Contract:** `paperless` client against recorded API responses (respx).
- **Integration (optional, marked):** `docker compose` with real
  Paperless-ngx plus the synthetic PDFs in `tests/fixtures/`.
- Quality gate: `ruff`, `mypy --strict` on the package, coverage ≥ 85 %
  for the pure modules.

## Stack & repository layout

Python 3.12, uv, Typer, FastAPI, Jinja2, htmx (vendored), httpx, pydantic,
psutil, SQLite. Docker image multi-arch (amd64 + arm64).

```
local-paper-ai/
├── pyproject.toml
├── docker-compose.yml          # paperless-ngx, redis, syncthing, local-paper-ai; ollama profile
├── config.example.yaml
├── rules.yaml                  # retention defaults (DE)
├── src/local_paper_ai/
│   ├── config.py  paperless.py  metrics.py  idle_gate.py  ai/
│   ├── rules.py   analyzer.py   writer.py   backup.py     state.py
│   ├── notify.py  calendar.py   cli.py      web/
├── tests/  (unit/, contract/, integration/, fixtures/ — synthetic only)
└── docs/
```

## Open items (not blocking implementation)

- Deployment on the real Jetson: model, free RAM and disk must be checked
  (read-only, with the user's go-ahead) before the first install.
- Which cloud remote(s) the user wants for the encrypted copy.
