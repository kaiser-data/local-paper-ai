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
