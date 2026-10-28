### Task 12: Web UI (shred ritual, review, calendar)

**Files:**
- Create: `src/local_paper_ai/ritual.py`, `src/local_paper_ai/web/__init__.py`, `src/local_paper_ai/web/app.py`, `src/local_paper_ai/web/templates/base.html`, `index.html`, `review.html`
- Test: `tests/test_ritual.py`, `tests/test_web.py`

**Interfaces:**
- Consumes:
  - `Config` (Task 2), `State`, `StoredDoc` (Task 3)
  - `Rule`, `RuleMatch`, `add_years`, `append_rule` (Task 4)
  - `DocumentStore`, `apply`, `mark_shredded` (Task 8)
  - `build_ics`, `entries_from_state` (Task 11)
- Produces:
  - `ritual`:
    - `RitualItem(doc, analysis, copies)`
    - `RitualView(shred_now, waiting_backup, keep_original, keep_until, review, shredded_docs, shredded_pages)` with the property `kg`
    - `ritual_view(state, min_copies, today) -> RitualView`
  - `web.app.create_app(cfg, state, store: DocumentStore, today=date.today) -> FastAPI`, with these routes:
    - `GET /`, `POST /shred/{id}` (303 → `/`)
    - `GET /review`, `POST /review/{id}` (303 → `/review`)
    - `GET /calendar.ics?token=`
- Behaviour:
  - Basic auth applies to every route except `/calendar.ics`, and only when `web.basic_auth_user` is set.
  - `/calendar.ics` returns 404 when `web.calendar_token` is set and the token doesn't match.
  - No JavaScript: plain forms, POST and then a redirect.

- [x] **Step 1: Write the failing tests**

`tests/test_ritual.py`:
```python
from datetime import date
from pathlib import Path

from local_paper_ai.models import Analysis, Document, Verdict
from local_paper_ai.ritual import ritual_view
from local_paper_ai.state import State

TODAY = date(2026, 10, 28)


def _add(state: State, doc_id: int, copies: int = 1, **verdict: object) -> None:
    doc = Document(id=doc_id, title=f"D{doc_id}", content="x", created=date(2026, 1, 1), page_count=3)
    state.save_analysis(doc, Analysis(doc_id=doc_id, verdict=Verdict(**verdict)))  # type: ignore[arg-type]
    for i in range(copies - 1):
        state.record_copy(doc_id, f"t{i}", "sha")


def test_groups(tmp_path: Path) -> None:
    s = State(tmp_path / "s.db")
    _add(s, 1, copies=2, kind="may_shred")
    _add(s, 2, copies=1, kind="may_shred")
    _add(s, 3, copies=2, kind="keep_until", keep_until=date(2026, 1, 1))
    _add(s, 4, copies=2, kind="keep_until", keep_until=date(2030, 1, 1))
    _add(s, 5, copies=2, kind="keep_original")
    _add(s, 6, copies=2, kind="keep_original", needs_review=True)
    _add(s, 7, copies=2, kind="may_shred")
    s.mark_shredded(7, 3)
    view = ritual_view(s, min_copies=2, today=TODAY)
    ids = lambda items: [i.doc.id for i in items]  # noqa: E731
    assert ids(view.shred_now) == [1, 3]
    assert ids(view.waiting_backup) == [2]
    assert ids(view.keep_until) == [4]
    assert ids(view.keep_original) == [5]
    assert ids(view.review) == [6]
    assert (view.shredded_docs, view.shredded_pages) == (1, 3)
    assert view.kg == 0.015
```

`tests/test_web.py`:
```python
import shutil
from datetime import date
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from local_paper_ai.config import Config
from local_paper_ai.models import Analysis, Deadline, Document, Evidence, Verdict
from local_paper_ai.rules import load_rules
from local_paper_ai.state import State
from local_paper_ai.web.app import create_app
from tests.fakes import FakePaperless

ROOT = Path(__file__).resolve().parents[1]


def _setup(tmp_path: Path, **web: Any) -> tuple[Config, State, FakePaperless, TestClient]:
    rules = tmp_path / "rules.yaml"
    shutil.copy(ROOT / "rules.yaml", rules)
    cfg = Config.model_validate({
        "paperless": {"url": "http://pl", "token": "t", "consume_dir": str(tmp_path / "c")},
        "gate": {"inbox_dir": str(tmp_path / "i")},
        "rules_path": str(rules),
        "web": web,
    })
    state = State(tmp_path / "s.db")
    store = FakePaperless()
    app = create_app(cfg, state, store, today=lambda: date(2026, 10, 28))
    return cfg, state, store, TestClient(app)


def _save(state: State, store: FakePaperless, doc_id: int, copies: int = 1,
          pages: int = 2, deadlines: list[Deadline] | None = None, **verdict: Any) -> None:
    doc = Document(id=doc_id, title=f"Dokument {doc_id}", content="Inhalt des Dokuments",
                   created=date(2026, 3, 14), page_count=pages)
    store.add(doc)
    v = Verdict(category="unknown",
                evidence=[Evidence(quote="Inhalt", source="rule", detail="Regel test")], **verdict)
    state.save_analysis(doc, Analysis(doc_id=doc_id, verdict=v, deadlines=deadlines or []))
    for i in range(copies - 1):
        state.record_copy(doc_id, f"t{i}", "sha")


def test_index_groups_by_backup_state(tmp_path: Path) -> None:
    _, state, store, client = _setup(tmp_path)
    _save(state, store, 1, copies=2, kind="may_shred")
    _save(state, store, 2, copies=1, kind="may_shred")
    text = client.get("/").text
    assert "Darf weg (1)" in text and "Wartet auf Backup (1)" in text


def test_shred_marks_state_and_tags(tmp_path: Path) -> None:
    _, state, store, client = _setup(tmp_path)
    _save(state, store, 1, copies=2, pages=3, kind="may_shred")
    r = client.post("/shred/1", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert state.is_shredded(1)
    assert "lpa:entsorgt" in store.tag_names(1)
    assert "3 Blatt Papier weg" in client.get("/").text
    assert client.post("/shred/99", follow_redirects=False).status_code == 404


def test_review_decision_updates_and_saves_rule(tmp_path: Path) -> None:
    cfg, state, store, client = _setup(tmp_path)
    _save(state, store, 1, kind="keep_original", needs_review=True)
    assert "Dokument 1" in client.get("/review").text
    r = client.post("/review/1", follow_redirects=False, data={
        "verdict": "keep_until", "keep_years": "3", "save_rule": "on",
        "keyword": "Hausratversicherung",
    })
    assert r.status_code == 303
    stored = state.get(1)
    assert stored is not None
    v = stored.analysis.verdict
    assert (v.kind, v.keep_until, v.needs_review) == ("keep_until", date(2029, 3, 14), False)
    assert "lpa:aufbewahren-bis" in store.tag_names(1)
    last = load_rules(cfg.rules_path)[-1]
    assert (last.match.keywords, last.years, last.category) == (["Hausratversicherung"], 3, "unknown")
    assert "Dokument 1" not in client.get("/review").text


def test_review_keep_until_needs_years(tmp_path: Path) -> None:
    _, state, store, client = _setup(tmp_path)
    _save(state, store, 1, kind="keep_original", needs_review=True)
    r = client.post("/review/1", data={"verdict": "keep_until", "keep_years": ""})
    assert r.status_code == 422


def test_calendar_token(tmp_path: Path) -> None:
    _, state, store, client = _setup(tmp_path, calendar_token="geheim")
    dl = Deadline(due=date(2026, 11, 2), label="Zahlung fällig",
                  evidence=Evidence(quote="zahlbar bis 02.11.2026", source="regex"))
    _save(state, store, 1, kind="may_shred", deadlines=[dl])
    assert client.get("/calendar.ics").status_code == 404
    r = client.get("/calendar.ics", params={"token": "geheim"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/calendar")
    assert "DTSTART;VALUE=DATE:20261102" in r.text


def test_basic_auth(tmp_path: Path) -> None:
    _, _, _, client = _setup(tmp_path, basic_auth_user="me", basic_auth_password="pw")
    assert client.get("/").status_code == 401
    assert client.get("/", auth=("me", "pw")).status_code == 200
    assert client.get("/review", auth=("me", "wrong")).status_code == 401
    assert client.get("/calendar.ics").status_code == 200
```

- [x] **Step 2: Run them**

Run: `uv run pytest tests/test_ritual.py tests/test_web.py -v`
Expected: FAIL with `No module named 'local_paper_ai.ritual'`

- [x] **Step 3: Implement** `src/local_paper_ai/ritual.py`

```python
"""Group analysed documents for the shred ritual."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from local_paper_ai.models import Analysis, Document
from local_paper_ai.state import State

GRAMS_PER_SHEET = 5  # A4 at 80 g/m²


@dataclass(frozen=True)
class RitualItem:
    doc: Document
    analysis: Analysis
    copies: int


@dataclass
class RitualView:
    shred_now: list[RitualItem] = field(default_factory=list)
    waiting_backup: list[RitualItem] = field(default_factory=list)
    keep_original: list[RitualItem] = field(default_factory=list)
    keep_until: list[RitualItem] = field(default_factory=list)
    review: list[RitualItem] = field(default_factory=list)
    shredded_docs: int = 0
    shredded_pages: int = 0

    @property
    def kg(self) -> float:
        return self.shredded_pages * GRAMS_PER_SHEET / 1000


def ritual_view(state: State, min_copies: int, today: date) -> RitualView:
    view = RitualView()
    view.shredded_docs, view.shredded_pages = state.shred_stats()
    for stored in state.all_docs():
        doc, analysis = stored.document, stored.analysis
        if state.is_shredded(doc.id):
            continue
        item = RitualItem(doc, analysis, state.verified_copies(doc.id))
        verdict = analysis.verdict
        expired = (
            verdict.kind == "keep_until"
            and verdict.keep_until is not None
            and verdict.keep_until < today
        )
        if verdict.needs_review:
            view.review.append(item)
        elif verdict.kind == "may_shred" or expired:
            group = view.shred_now if item.copies >= min_copies else view.waiting_backup
            group.append(item)
        elif verdict.kind == "keep_original":
            view.keep_original.append(item)
        else:
            view.keep_until.append(item)
    return view
```

`src/local_paper_ai/web/__init__.py`:
```python
"""Web UI."""
```

`src/local_paper_ai/web/app.py`:
```python
"""Web UI: shred ritual, review queue, deadline calendar feed.

No `from __future__ import annotations` here: FastAPI must resolve the
Annotated[..., Depends(...)] hints of the nested route functions at definition time.
"""

import secrets
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Any

import httpx
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from local_paper_ai.config import Config
from local_paper_ai.ics import build_ics, entries_from_state
from local_paper_ai.models import Evidence, Verdict, VerdictKind
from local_paper_ai.ritual import ritual_view
from local_paper_ai.rules import Rule, RuleMatch, add_years, append_rule
from local_paper_ai.state import State, StoredDoc
from local_paper_ai.writer import DocumentStore, apply, mark_shredded

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def create_app(
    cfg: Config,
    state: State,
    store: DocumentStore,
    today: Callable[[], date] = date.today,
) -> FastAPI:
    app = FastAPI(title="local-paper-ai", docs_url=None, redoc_url=None, openapi_url=None)
    basic = HTTPBasic(auto_error=False)

    def require_auth(creds: Annotated[HTTPBasicCredentials | None, Depends(basic)]) -> None:
        user, password = cfg.web.basic_auth_user, cfg.web.basic_auth_password
        if not user:
            return
        ok = (
            creds is not None
            and secrets.compare_digest(creds.username, user)
            and secrets.compare_digest(creds.password, password)
        )
        if not ok:
            raise HTTPException(status_code=401, headers={"WWW-Authenticate": "Basic"})

    def stored_or_404(doc_id: int) -> StoredDoc:
        stored = state.get(doc_id)
        if stored is None:
            raise HTTPException(status_code=404)
        return stored

    auth: list[Any] = [Depends(require_auth)]

    @app.get("/", response_class=HTMLResponse, dependencies=auth)
    def index(request: Request) -> HTMLResponse:
        view = ritual_view(state, cfg.backup.min_copies, today())
        return _TEMPLATES.TemplateResponse(request, "index.html", {"view": view})

    @app.post("/shred/{doc_id}", dependencies=auth)
    def shred(doc_id: int) -> RedirectResponse:
        stored = stored_or_404(doc_id)
        state.mark_shredded(doc_id, stored.document.page_count or 1)
        with suppress(httpx.HTTPError):
            mark_shredded(store, doc_id)
        return RedirectResponse("/", status_code=303)

    @app.get("/review", response_class=HTMLResponse, dependencies=auth)
    def review(request: Request) -> HTMLResponse:
        docs = [
            s
            for s in state.all_docs()
            if s.analysis.verdict.needs_review and not state.is_shredded(s.document.id)
        ]
        return _TEMPLATES.TemplateResponse(request, "review.html", {"docs": docs})

    @app.post("/review/{doc_id}", dependencies=auth)
    def decide(
        doc_id: int,
        verdict: Annotated[VerdictKind, Form()],
        keep_years: Annotated[str, Form()] = "",
        save_rule: Annotated[bool, Form()] = False,
        keyword: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        stored = stored_or_404(doc_id)
        years = int(keep_years) if keep_years.strip().isdigit() else None
        if verdict == "keep_until" and not years:
            raise HTTPException(status_code=422, detail="Aufbewahren bis … braucht Jahre")
        category = stored.analysis.verdict.category
        keep_until = None
        if years and verdict == "keep_until":
            keep_until = add_years(stored.document.created, years)
        new_verdict = Verdict(
            kind=verdict,
            keep_until=keep_until,
            category=category,
            evidence=[Evidence(quote="Entscheidung im Review", source="manual")],
        )
        analysis = stored.analysis.model_copy(update={"verdict": new_verdict})
        state.update_analysis(analysis)
        with suppress(httpx.HTTPError):
            apply(store, analysis, today())
        if save_rule and keyword.strip():
            rule = Rule(
                name=f"manuell-{doc_id}",
                category=category,
                verdict=verdict,
                years=years if verdict == "keep_until" else None,
                match=RuleMatch(keywords=[keyword.strip()]),
            )
            append_rule(cfg.rules_path, rule)
        return RedirectResponse("/review", status_code=303)

    @app.get("/calendar.ics")
    def calendar(token: str = "") -> Response:
        expected = cfg.web.calendar_token
        if expected and not secrets.compare_digest(token, expected):
            raise HTTPException(status_code=404)
        body = build_ics(entries_from_state(state), datetime.now(UTC))
        return Response(body, media_type="text/calendar; charset=utf-8")

    return app
```

`src/local_paper_ai/web/templates/base.html`:
```html
<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{% block title %}local-paper-ai{% endblock %}</title>
<style>
:root { --bg:#f7f5f0; --fg:#1d1b18; --muted:#6b665d; --card:#fff; --line:#e3ded3;
        --accent:#2f6f4f; --on-accent:#fff; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#151412; --fg:#ece8e1; --muted:#a39d92; --card:#1f1d1a; --line:#34312c;
          --accent:#6fbf93; --on-accent:#10130f; }
}
body { margin:0; background:var(--bg); color:var(--fg); font:16px/1.5 system-ui, sans-serif; }
main { max-width:760px; margin:0 auto; padding:16px; }
nav a { margin-right:16px; color:var(--accent); font-weight:600; }
section { margin:24px 0; }
h2 { font-size:1.05rem; margin:0 0 8px; }
.item { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:12px;
        margin:8px 0; display:flex; gap:12px; align-items:center; justify-content:space-between;
        flex-wrap:wrap; }
.meta, .why, .empty { color:var(--muted); font-size:.875rem; }
.why { font-style:italic; }
.stats { font-size:1.2rem; font-weight:600; }
button { background:var(--accent); color:var(--on-accent); border:0; border-radius:6px;
         padding:8px 14px; font:inherit; cursor:pointer; }
input, select { font:inherit; padding:6px; border:1px solid var(--line); border-radius:6px;
                background:var(--card); color:var(--fg); }
form.inline { display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
</style>
</head>
<body><main>
<nav><a href="/">Entsorgen</a><a href="/review">Prüfen</a></nav>
{% block content %}{% endblock %}
</main></body>
</html>
```

`src/local_paper_ai/web/templates/index.html`:
```html
{% extends "base.html" %}
{% block content %}
{% macro row(item, can_shred) %}
<div class="item">
  <div>
    <strong>{{ item.doc.title }}</strong>
    <div class="meta">{{ item.doc.correspondent or "Unbekannt" }} · {{ item.doc.created.strftime("%d.%m.%Y") }} · {{ item.copies }} Kopien</div>
    {% if item.analysis.verdict.keep_until %}<div class="meta">Aufbewahren bis {{ item.analysis.verdict.keep_until.strftime("%d.%m.%Y") }}</div>{% endif %}
    {% for e in item.analysis.verdict.evidence %}<div class="why">{{ e.detail }} »{{ e.quote }}«</div>{% endfor %}
  </div>
  {% if can_shred %}<form method="post" action="/shred/{{ item.doc.id }}"><button>Weggeworfen ✓</button></form>{% endif %}
</div>
{% endmacro %}
<p class="stats">{{ view.shredded_pages }} Blatt Papier weg · {{ "%.1f"|format(view.kg) }} kg · {{ view.shredded_docs }} Dokumente</p>
{% if view.review %}<p><a href="/review">{{ view.review|length }} Dokumente brauchen deine Entscheidung →</a></p>{% endif %}
<section><h2>✅ Darf weg ({{ view.shred_now|length }})</h2>
{% for item in view.shred_now %}{{ row(item, True) }}{% else %}<p class="empty">Gerade nichts zu entsorgen.</p>{% endfor %}
</section>
<section><h2>⏳ Wartet auf Backup ({{ view.waiting_backup|length }})</h2>
{% for item in view.waiting_backup %}{{ row(item, False) }}{% else %}<p class="empty">Alles gesichert.</p>{% endfor %}
</section>
<section><h2>📦 Original aufheben ({{ view.keep_original|length }})</h2>
{% for item in view.keep_original %}{{ row(item, False) }}{% else %}<p class="empty">Keine.</p>{% endfor %}
</section>
<section><h2>🗓️ Aufbewahren bis … ({{ view.keep_until|length }})</h2>
{% for item in view.keep_until %}{{ row(item, False) }}{% else %}<p class="empty">Keine.</p>{% endfor %}
</section>
{% endblock %}
```

`src/local_paper_ai/web/templates/review.html`:
```html
{% extends "base.html" %}
{% block title %}Prüfen · local-paper-ai{% endblock %}
{% block content %}
<h1>Prüfen</h1>
{% for s in docs %}
<div class="item">
  <div>
    <strong>{{ s.document.title }}</strong>
    <div class="meta">{{ s.document.correspondent or "Unbekannt" }} · {{ s.document.created.strftime("%d.%m.%Y") }}</div>
    <div class="why">{{ s.document.content[:300] }}</div>
  </div>
  <form class="inline" method="post" action="/review/{{ s.document.id }}">
    <select name="verdict" aria-label="Entscheidung">
      <option value="keep_original">Original aufheben</option>
      <option value="keep_until">Aufbewahren bis …</option>
      <option value="may_shred">Darf weg</option>
    </select>
    <input name="keep_years" type="number" min="1" max="30" placeholder="Jahre" aria-label="Jahre">
    <label><input type="checkbox" name="save_rule"> als Regel merken:</label>
    <input name="keyword" placeholder="Stichwort, z. B. Hausrat" aria-label="Stichwort">
    <button>Speichern</button>
  </form>
</div>
{% else %}
<p class="empty">Nichts zu prüfen. 🎉</p>
{% endfor %}
{% endblock %}
```

- [x] **Step 4: Run tests**

Run: `uv run pytest tests/test_ritual.py tests/test_web.py -v`
Expected: `7 passed`

- [x] **Step 5: Check by eye.** Deferred to Task 15, Step 3, where `serve` exists: open `/` at 375 px width and confirm that nothing scrolls horizontally.

- [x] **Step 6: Commit and push** with the message `feat: web shred ritual, review queue and calendar feed` (plus the trailer).
