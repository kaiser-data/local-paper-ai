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
    cfg = Config.model_validate(
        {
            "paperless": {"url": "http://pl", "token": "t", "consume_dir": str(tmp_path / "c")},
            "gate": {"inbox_dir": str(tmp_path / "i")},
            "rules_path": str(rules),
            "web": web,
        }
    )
    state = State(tmp_path / "s.db")
    store = FakePaperless()
    app = create_app(cfg, state, store, today=lambda: date(2026, 10, 28))
    return cfg, state, store, TestClient(app)


def _save(
    state: State,
    store: FakePaperless,
    doc_id: int,
    copies: int = 1,
    pages: int = 2,
    deadlines: list[Deadline] | None = None,
    **verdict: Any,
) -> None:
    doc = Document(
        id=doc_id,
        title=f"Dokument {doc_id}",
        content="Inhalt des Dokuments",
        created=date(2026, 3, 14),
        page_count=pages,
    )
    store.add(doc)
    v = Verdict(
        category="unknown",
        evidence=[Evidence(quote="Inhalt", source="rule", detail="Regel test")],
        **verdict,
    )
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
    r = client.post(
        "/review/1",
        follow_redirects=False,
        data={
            "verdict": "keep_until",
            "keep_years": "3",
            "save_rule": "on",
            "keyword": "Hausratversicherung",
        },
    )
    assert r.status_code == 303
    stored = state.get(1)
    assert stored is not None
    v = stored.analysis.verdict
    assert (v.kind, v.keep_until, v.needs_review) == ("keep_until", date(2029, 3, 14), False)
    assert "lpa:aufbewahren-bis" in store.tag_names(1)
    last = load_rules(cfg.rules_path)[-1]
    assert (last.match.keywords, last.years, last.category) == (
        ["Hausratversicherung"],
        3,
        "unknown",
    )
    assert "Dokument 1" not in client.get("/review").text


def test_review_keep_until_needs_years(tmp_path: Path) -> None:
    _, state, store, client = _setup(tmp_path)
    _save(state, store, 1, kind="keep_original", needs_review=True)
    r = client.post("/review/1", data={"verdict": "keep_until", "keep_years": ""})
    assert r.status_code == 422


def test_calendar_token(tmp_path: Path) -> None:
    _, state, store, client = _setup(tmp_path, calendar_token="geheim")
    dl = Deadline(
        due=date(2026, 11, 2),
        label="Zahlung fällig",
        evidence=Evidence(quote="zahlbar bis 02.11.2026", source="regex"),
    )
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
