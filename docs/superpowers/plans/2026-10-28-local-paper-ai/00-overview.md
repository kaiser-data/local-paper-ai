# local-paper-ai Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Each task lives in its own file in this folder; do them in numeric order and tick the boxes in the task file as you go.

**Goal:** Build local-paper-ai, a companion service for Paperless-ngx. For every scanned document it says whether the paper original may be shredded (with evidence), detects deadlines, verifies backups in several places, works only when a shared box is idle, and offers a small web "shred ritual".

**Architecture:** A Python package with small single-purpose modules (rules, analyzer, AI adapter, Paperless client, writer, idle gate, backup, calendar, notify, web, worker, CLI). Paperless-ngx does OCR and storage and is reached only over its REST API. One worker loop and one FastAPI web app share a SQLite state file.

**Tech Stack:** Python 3.12, uv, pydantic v2, httpx, Typer, FastAPI + Jinja2 (plain HTML forms, no JS), psutil, PyYAML, SQLite. Tests use pytest + respx; quality checks are ruff + mypy --strict. Docker Compose bundles paperless-ngx, redis, syncthing and optionally ollama.

**Spec:** `docs/superpowers/specs/2026-10-28-local-paper-ai-design.md`

## Global Constraints

- Python `>=3.12`, pinned to 3.12 via `.python-version`. Use `uv` for every command (`uv run …`).
- Runtime dependencies are exactly those in Task 1's `pyproject.toml`. Do not add others.
- Talk to Paperless-ngx only through its REST API and never import Paperless code. The license is MIT.
- Never commit real documents or personal config. Test fixtures are synthetic.
- The default config has **no cloud AI provider**. `ai.local_only` defaults to `["tax", "health", "identity", "finance", "unknown"]`.
- A document counts as shreddable only when `verified_copies >= backup.min_copies` (default 2). The Paperless archive counts as one copy.
- Unsure (no rule hit and no grounded AI answer) → `keep_original` + `needs_review`.
- Every AI claim carries a verbatim quote. It is accepted only if, after normalizing whitespace and case, the quote is at least 8 characters long and appears in the OCR text.
- UI text, prompts and Paperless tag names are in German. Code, comments and docs are in English.
- Paperless tag names: `lpa:darf-weg`, `lpa:original-aufheben`, `lpa:aufbewahren-bis`, `lpa:pruefen`, `lpa:entsorgt`. Custom fields: `lpa_keep_until` (date), `lpa_next_deadline` (date), `lpa_reason` (string).
- Before **every** commit: `uv run ruff format . && uv run ruff check . && uv run mypy && uv run pytest -q`. All must pass.
- Commit messages are conventional (`feat:`, `test:`, `chore:`, `docs:`) and end with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Push to `origin main` after each task.

## Tasks

| # | File | Deliverable |
|---|---|---|
| 1 | `01-scaffold.md` | Package skeleton, tooling, CI lint/test job |
| 2 | `02-models-config.md` | Domain types, config loader, `config.example.yaml` |
| 3 | `03-state.md` | SQLite state store |
| 4 | `04-rules.md` | Retention rules engine + German defaults `rules.yaml` |
| 5 | `05-ai-adapter.md` | Provider-agnostic AI adapter with privacy lock |
| 6 | `06-analyzer.md` | Verdict + deadlines with grounding; shared test fakes |
| 7 | `07-paperless-client.md` | Paperless REST client |
| 8 | `08-writer.md` | Idempotent tag/custom-field writer |
| 9 | `09-metrics-idle-gate.md` | Load metrics (Jetson/psutil) + idle gate |
| 10 | `10-backup.md` | Export, multi-target copy, sha256 verification |
| 11 | `11-ics-notify.md` | Calendar feed + ntfy reminders |
| 12 | `12-web.md` | Shred ritual, review, calendar endpoint |
| 13 | `13-worker-cli.md` | Worker cycle/loop + CLI |
| 14 | `14-deploy-docs.md` | Dockerfile, compose, CI docker + live integration test, README |
| 15 | `15-verify.md` | Final verification and release tag |

## Shared file map

```
pyproject.toml  .python-version  .github/workflows/ci.yml
config.example.yaml  rules.yaml  Dockerfile  docker-compose.yml  .env.example
src/local_paper_ai/
  __init__.py  models.py  config.py  state.py  rules.py  analyzer.py
  paperless.py  writer.py  metrics.py  idle_gate.py  backup.py
  ics.py  notify.py  ritual.py  worker.py  cli.py
  ai/__init__.py  ai/base.py  ai/schemas.py  ai/prompts.py
  ai/openai_compat.py  ai/anthropic.py  ai/router.py
  web/__init__.py  web/app.py  web/templates/{base,index,review}.html
tests/
  fakes.py  test_*.py (one per module)  integration/test_paperless_live.py
```
