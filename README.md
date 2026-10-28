# local-paper-ai

**Scan it, file it, shred it.** Turn household paperwork into searchable PDFs,
and know which originals you can finally throw away.

- 📱 Scan with any phone (Android, iOS). No special app required.
- 🔒 Local by default: OCR and AI run on your own box. The cloud copy is encrypted.
- ✅ Per-document verdict: *may shred* · *keep original* · *keep until YYYY*, always with a quote from the document as evidence
- ⏰ Finds deadlines (appeal periods, payment dates) and puts them in your calendar
- 🔁 Verified copies in several places. Nothing counts as shreddable before at least 2 verified copies exist.
- 💤 Made for a shared small box (e.g. a Jetson): it only works while the box is idle
- 🗑️ A "shred ritual" page: tick off what you binned and watch the pile shrink

local-paper-ai is a **companion for [Paperless-ngx](https://github.com/paperless-ngx/paperless-ngx)**.
Paperless does the OCR, search and archive; local-paper-ai adds the
"may I throw this away?" answer, deadlines, verified backups and idle-aware scheduling.
For AI titles and tags, combine it with
[paperless-gpt](https://github.com/icereed/paperless-gpt) or
[paperless-ai](https://github.com/clusterzx/paperless-ai).
It only talks to Paperless over its REST API.

> Status: v0.1.0, first release. See the [design](docs/superpowers/specs/2026-10-28-local-paper-ai-design.md).

## How it works

```
Phone (any)            Box (Jetson / any Linux / Mac)                         Outputs
───────────            ─────────────────────────────                          ───────
Scan app ──Syncthing─▶ inbox/
                         │ idle gate (one file at a time, only when idle)
                         ▼
                       Paperless-ngx consume/ ─▶ OCR, search, archive
                         │ (REST API)
                         ▼
                       local-paper-ai worker
                         ├─ analyzer: rules + AI adapter → verdict, deadlines (with evidence)
                         ├─ writer: tags + custom fields back to Paperless
                         ├─ backup: export → targets → sha256 verify
                         └─ notifier: ntfy push                                ─▶ phone push
                       local-paper-ai web
                         ├─ /             shred ritual                          ─▶ any browser
                         ├─ /review       documents needing your decision
                         └─ /calendar.ics deadline feed                         ─▶ any calendar
```

In Paperless every document gets one of the tags `lpa:darf-weg`,
`lpa:original-aufheben` or `lpa:aufbewahren-bis`, and also `lpa:pruefen`
when it needs your decision. It also gets the custom fields `lpa_keep_until`,
`lpa_next_deadline` and `lpa_reason`.

## Quick start

```bash
git clone https://github.com/kaiser-data/local-paper-ai.git && cd local-paper-ai
cp config.example.yaml config.yaml
cp .env.example .env          # set PAPERLESS_SECRET_KEY, admin user/password, LPA_CALENDAR_TOKEN
docker compose up -d          # add "--profile ai" to also start Ollama
```

1. Open Paperless at `http://<box>:8000` and log in. Then go to **Profile → API token**, create a token and put it into `.env` as `PAPERLESS_TOKEN`.
2. Run `docker compose restart worker web`.
3. Run `docker compose exec worker local-paper-ai doctor`. Every line should show ✓.
4. Open the shred ritual at `http://<box>:8765`.

## Scanning from your phone

1. Install Syncthing on the phone, then share a folder with the box's `data/inbox`. The box runs Syncthing at `http://<box>:8384`.
2. Scan with any app that saves a PDF into that folder: the Samsung camera document scan, the Google Drive scanner, or the iOS Files app.
3. Done. The box picks scans up whenever it is idle, which may be minutes or hours later. Nothing gets lost in between.

## Getting your files everywhere

- **Desktop and phone:** Syncthing shares `data/export-sync`. That folder holds every PDF with a readable name such as `2026-03-14_Finanzamt-München_Steuerbescheid_42.pdf`.
- **Encrypted cloud copy (optional):**
  1. Create an rclone *crypt* remote with `rclone config`. Any backend works: Google Drive, OneDrive, Dropbox, S3, WebDAV…
  2. Add it as a target in `config.yaml`:
     ```yaml
     - name: cloud
       kind: rclone
       path: "mycrypt:local-paper-ai"
       crypt: true
     ```
  3. Mount your rclone config via `RCLONE_CONFIG_DIR`.
- The Paperless archive counts as one copy. Each target is verified by sha256 locally, or by `rclone check`/`cryptcheck`. A paper is shown as shreddable only after `min_copies` (default 2) verified copies exist.

## Deadlines in your calendar

Subscribe once in Google Calendar, Samsung Calendar, Thunderbird or Outlook:
`http://<box>:8765/calendar.ics?token=<LPA_CALENDAR_TOKEN>`

For push reminders 7 days and 1 day before a deadline, set `notify.ntfy_url`
to `https://ntfy.sh/<secret-topic>` or to your own ntfy server.

## Running on a Jetson or another small box

- **Idle gate:** the box only works while CPU ≤ `cpu_max`, GPU ≤ `gpu_max` and free RAM ≥ `ram_free_min_mb`. Load is checked again between documents. Scans older than `max_wait_days` ignore the CPU/GPU limits, but still need free RAM.
- **Free vs. available RAM:** on Jetson, CUDA allocations fail outright when memory runs short; they don't wait for the page cache to be reclaimed. That's why the gate reads `MemFree`. Check it with `local-paper-ai gate-status`.
- **AI:** for CUDA on Jetson, use the `dustynv/ollama` image from jetson-containers.
  - Recommended models: `phi4-mini` (≈18 tok/s on an Orin Nano 8 GB) for analysis, `qwen3.5:0.8b` as the low-memory fallback.
  - Keep `ollama_unload: true`, so the model leaves memory after every document.
  - `model_budget_mb` skips the AI instead of risking an out-of-memory failure.
- Paperless runs with one worker and one thread to keep its footprint small.

## Privacy

- The default config contains **no cloud provider**.
- Categories in `ai.local_only` are never sent to a cloud provider. The defaults are tax, health, identity, finance and **unknown**.
- A document whose category isn't known yet counts as `unknown`, so its first analysis always stays local.
- **Grounding:** every AI claim must quote the document word for word. Claims without a quote that appears in the text are dropped, and the document goes to `/review`.
- If nothing is certain, the verdict is **keep the original**.

## Retention rules

`rules.yaml` is an ordered list, and the first matching rule wins. A rule can
match on keywords, Paperless document types, correspondents or tags, and gives
one of `keep_original`, `keep_until` (with `years`) or `may_shred`. When you
decide a document on `/review`, you can save that decision as a new rule;
it is appended to the file.

**The shipped rules are defaults for German households. They are not legal
advice. Check them for your situation.**

## CLI

| Command | What it does |
|---|---|
| `local-paper-ai run` | Run the worker loop (reminders → idle gate → feed → analyze → back up) |
| `local-paper-ai once` | Run one cycle and print its report |
| `local-paper-ai gate-status` | Show the current load and whether the box counts as idle |
| `local-paper-ai analyze ID [--write]` | Analyze one document and print JSON (dry run by default) |
| `local-paper-ai backup` | Sync all backup targets now |
| `local-paper-ai serve` | Start the web UI |
| `local-paper-ai doctor` | Check Paperless, rules, folders, AI endpoints and rclone |

Every command takes `-c config.yaml` (or reads the env var `LPA_CONFIG`).

## Development

```bash
uv sync
uv run ruff format . && uv run ruff check . && uv run mypy && uv run pytest
# end-to-end against a real Paperless-ngx:
LPA_IT_URL=http://localhost:8000 LPA_IT_TOKEN=<token> uv run pytest -m integration
```

CI runs lint, types and tests, builds the image for amd64 and arm64, and runs
the end-to-end test against a real Paperless-ngx container.

## License

MIT
