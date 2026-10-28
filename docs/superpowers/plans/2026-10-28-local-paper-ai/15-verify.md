### Task 15: Final verification and release

**Files:**
- Modify: `docs/superpowers/specs/2026-10-28-local-paper-ai-design.md` (only where the implementation differs from it), `README.md` (status line)

**Interfaces:** none new. This task proves that the deliverable works.

- [ ] **Step 1: Full local quality gate**

Run: `uv run ruff format --check . && uv run ruff check . && uv run mypy && uv run pytest --cov=local_paper_ai --cov-report=term-missing`
Expected: everything passes. Coverage for `rules`, `analyzer`, `writer`, `idle_gate`, `backup`, `ics`, `notify`, `ritual` and `state` is ≥ 85 % each. Where a module is below that, add the missing test cases for its uncovered branches (shown in the `Missing` column) before continuing.

- [ ] **Step 2: Mock audit.** Run `grep -rn "Fake\|mock\|stub" src/`.
Expected: no matches. Fakes live only in `tests/`. The real path is proven by the CI `integration` job (real Paperless-ngx: upload, analyze, tag, download).

- [ ] **Step 3: Smoke-test the web UI locally (no Paperless needed)**

```bash
mkdir -p /tmp/lpa-smoke && cat > /tmp/lpa-smoke/config.yaml <<'EOF'
paperless: {url: "http://127.0.0.1:9", token: x, consume_dir: /tmp/lpa-smoke/consume}
gate: {inbox_dir: /tmp/lpa-smoke/inbox, metrics: psutil}
rules_path: rules.yaml
state_path: /tmp/lpa-smoke/state.db
export_dir: /tmp/lpa-smoke/export
web: {host: 127.0.0.1, port: 8765}
EOF
uv run local-paper-ai serve -c /tmp/lpa-smoke/config.yaml &
sleep 3
curl -sf http://127.0.0.1:8765/ | grep -q "Darf weg" && echo "index OK"
curl -sf http://127.0.0.1:8765/review | grep -q "Nichts zu prüfen" && echo "review OK"
curl -sf http://127.0.0.1:8765/calendar.ics | head -1
kill %1
```
Expected: `index OK`, `review OK`, `BEGIN:VCALENDAR`. If a browser tool is available, also open `/` at 375 px width and confirm that nothing scrolls horizontally.

- [ ] **Step 4: CI green.** Push, then run `gh run watch --exit-status`.
Expected: the `test`, `docker` (amd64 + arm64) and `integration` jobs all succeed. Paste the run URL into the final report.

- [ ] **Step 5: Spec sync.** Update the design doc wherever the implementation deliberately differs:
  - **web:** plain HTML forms instead of vendored htmx
  - **idle gate:** overdue scans ignore the CPU/GPU limits but still need free RAM
  - **export filename:** now ends with `_<doc id>`
  - **calendar module:** named `ics.py`

  Set `Status: Implemented (v0.1.0)`. In the README, change the status line to `Status: v0.1.0, first release`.

- [ ] **Step 6: Release**

```bash
git add -A
git commit -m "docs: sync spec with implementation, v0.1.0

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push
git tag -a v0.1.0 -m "local-paper-ai v0.1.0"
git push origin v0.1.0
gh release create v0.1.0 --title "local-paper-ai v0.1.0" --generate-notes
```

- [ ] **Step 7: Final report to the user.** List the following:
  - what was built, the CI run URL and the release URL
  - **what is not yet verified on real hardware:** deployment on the user's Jetson (model, free RAM and disk still unchecked, which needs the user's go-ahead), the real phone → Syncthing → inbox path, and Ollama on the Jetson
  - the exact first-run steps for the user: README "Quick start"
