import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import respx

from local_paper_ai.ics import CalendarEntry, build_ics, entries_from_state
from local_paper_ai.models import Analysis, Deadline, Document, Evidence, Verdict
from local_paper_ai.notify import due_reminders, send, send_reminders
from local_paper_ai.state import State

NOW = datetime(2026, 10, 28, 9, 30, tzinfo=UTC)


def _deadline(due: date, label: str = "Einspruchsfrist") -> Deadline:
    return Deadline(
        due=due, label=label, evidence=Evidence(quote="innerhalb eines Monats", source="ai")
    )


def test_build_ics_structure_and_escaping() -> None:
    entry = CalendarEntry(1, "Steuerbescheid; 2025, Finanzamt", _deadline(date(2026, 4, 14)))
    text = build_ics([entry], NOW)
    assert text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n")
    assert "DTSTART;VALUE=DATE:20260414\r\n" in text
    assert "DTEND;VALUE=DATE:20260415\r\n" in text
    assert "DTSTAMP:20261028T093000Z\r\n" in text
    assert "Steuerbescheid\\; 2025\\, Finanzamt" in text.replace("\r\n ", "")
    assert "ORGANIZER" not in text and "@" not in text.split("UID:")[0]


def test_build_ics_folds_long_lines_and_uid_is_stable() -> None:
    entry = CalendarEntry(1, "Ä" * 120, _deadline(date(2026, 4, 14)))
    text = build_ics([entry], NOW)
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))
    assert "Ä" * 120 in text.replace("\r\n ", "")
    uid = [ln for ln in text.split("\r\n") if ln.startswith("UID:")]
    assert uid == [ln for ln in build_ics([entry], NOW).split("\r\n") if ln.startswith("UID:")]


def _state(tmp_path: Path, due: date) -> State:
    state = State(tmp_path / "s.db")
    doc = Document(id=1, title="Rechnung Stadtwerke", content="x", created=date(2026, 10, 1))
    analysis = Analysis(
        doc_id=1, verdict=Verdict(kind="may_shred"), deadlines=[_deadline(due, "Zahlung fällig")]
    )
    state.save_analysis(doc, analysis)
    return state


def test_entries_from_state(tmp_path: Path) -> None:
    entries = entries_from_state(_state(tmp_path, date(2026, 11, 2)))
    assert [(e.doc_id, e.doc_title, e.deadline.due) for e in entries] == [
        (1, "Rechnung Stadtwerke", date(2026, 11, 2))
    ]


def test_due_reminders_windows(tmp_path: Path) -> None:
    today = date(2026, 10, 28)
    assert [r.kind for r in due_reminders(_state(tmp_path / "a", date(2026, 11, 2)), today)] == [
        "d7"
    ]
    one = due_reminders(_state(tmp_path / "b", date(2026, 10, 29)), today)
    assert [(r.kind, r.title) for r in one] == [("d1", "Zahlung fällig morgen")]
    assert due_reminders(_state(tmp_path / "c", date(2026, 11, 30)), today) == []
    assert due_reminders(_state(tmp_path / "d", date(2026, 10, 1)), today) == []


def test_send_splits_topic(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(host="ntfy.sh").mock(return_value=httpx.Response(200))
    assert send(httpx.Client(), "https://ntfy.sh/geheim", "Titel", "Text") is True
    body = json.loads(route.calls.last.request.content)
    assert body["topic"] == "geheim" and body["title"] == "Titel" and body["message"] == "Text"


def test_send_without_url_or_on_error(respx_mock: respx.MockRouter) -> None:
    assert send(httpx.Client(), "", "t", "m") is False
    respx_mock.post(host="ntfy.sh").mock(return_value=httpx.Response(500))
    assert send(httpx.Client(), "https://ntfy.sh/x", "t", "m") is False


def test_send_reminders_marks_notified(tmp_path: Path, respx_mock: respx.MockRouter) -> None:
    respx_mock.post(host="ntfy.sh").mock(return_value=httpx.Response(200))
    state = _state(tmp_path, date(2026, 11, 2))
    today = date(2026, 10, 28)
    assert send_reminders(httpx.Client(), "https://ntfy.sh/x", state, today) == 1
    assert send_reminders(httpx.Client(), "https://ntfy.sh/x", state, today) == 0
    assert send_reminders(httpx.Client(), "", state, today) == 0
