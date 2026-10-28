### Task 11: Deadline calendar feed and ntfy reminders

**Files:**
- Create: `src/local_paper_ai/ics.py` (not `calendar.py`, which would shadow the stdlib), `src/local_paper_ai/notify.py`
- Test: `tests/test_ics_notify.py`

**Interfaces:**
- Consumes: `Deadline`, `Evidence` (Task 2), `State` (Task 3).
- Produces:
  - `ics`: `CalendarEntry(doc_id, doc_title, deadline)` (frozen), `build_ics(entries, now: datetime) -> str`, `entries_from_state(state) -> list[CalendarEntry]`
  - `notify`: `send(http, ntfy_url, title, message) -> bool`, `Reminder(doc_id, due, kind, title, message)`, `due_reminders(state, today) -> list[Reminder]`, `send_reminders(http, ntfy_url, state, today) -> int`
- Calendar privacy: no ORGANIZER/ATTENDEE fields, no e-mail addresses.

- [x] **Step 1: Write the failing test** `tests/test_ics_notify.py`

```python
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
    return Deadline(due=due, label=label, evidence=Evidence(quote="innerhalb eines Monats", source="ai"))


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
    analysis = Analysis(doc_id=1, verdict=Verdict(kind="may_shred"), deadlines=[_deadline(due, "Zahlung fällig")])
    state.save_analysis(doc, analysis)
    return state


def test_entries_from_state(tmp_path: Path) -> None:
    entries = entries_from_state(_state(tmp_path, date(2026, 11, 2)))
    assert [(e.doc_id, e.doc_title, e.deadline.due) for e in entries] == [
        (1, "Rechnung Stadtwerke", date(2026, 11, 2))
    ]


def test_due_reminders_windows(tmp_path: Path) -> None:
    today = date(2026, 10, 28)
    assert [r.kind for r in due_reminders(_state(tmp_path / "a", date(2026, 11, 2)), today)] == ["d7"]
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
```

- [x] **Step 2: Run it**

Run: `uv run pytest tests/test_ics_notify.py -v`
Expected: FAIL with `No module named 'local_paper_ai.ics'`

- [x] **Step 3: Implement** `src/local_paper_ai/ics.py`

```python
"""RFC 5545 deadline feed. Subscribe once from any calendar app."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from local_paper_ai.models import Deadline
from local_paper_ai.state import State


@dataclass(frozen=True)
class CalendarEntry:
    doc_id: int
    doc_title: str
    deadline: Deadline


def _escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
    )


def _fold(line: str) -> str:
    """Fold at 75 octets; continuation lines start with one space."""
    parts: list[str] = []
    current = b""
    for ch in line:
        encoded = ch.encode()
        limit = 75 if not parts else 74
        if len(current) + len(encoded) > limit:
            parts.append(current.decode())
            current = encoded
        else:
            current += encoded
    parts.append(current.decode())
    return "\r\n ".join(parts)


def build_ics(entries: Iterable[CalendarEntry], now: datetime) -> str:
    stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//local-paper-ai//DE",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Papier-Fristen",
    ]
    for entry in sorted(entries, key=lambda e: (e.deadline.due, e.doc_id)):
        d = entry.deadline
        label_hash = hashlib.sha256(d.label.encode()).hexdigest()[:8]
        lines += [
            "BEGIN:VEVENT",
            f"UID:lpa-{entry.doc_id}-{d.due:%Y%m%d}-{label_hash}@local-paper-ai",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{d.due:%Y%m%d}",
            f"DTEND;VALUE=DATE:{d.due + timedelta(days=1):%Y%m%d}",
            "SEQUENCE:0",
            "TRANSP:TRANSPARENT",
            f"SUMMARY:{_escape(f'{d.label}: {entry.doc_title}')}",
            f"DESCRIPTION:{_escape(d.evidence.quote)}",
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{_escape(d.label)}",
            "TRIGGER:-P7D",
            "END:VALARM",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


def entries_from_state(state: State) -> list[CalendarEntry]:
    return [
        CalendarEntry(s.document.id, s.document.title, d)
        for s in state.all_docs()
        for d in s.analysis.deadlines
    ]
```

`src/local_paper_ai/notify.py`:
```python
"""Optional push reminders via ntfy (works with any phone; self-hostable)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import httpx

from local_paper_ai.state import State


@dataclass(frozen=True)
class Reminder:
    doc_id: int
    due: date
    kind: str
    title: str
    message: str


def send(http: httpx.Client, ntfy_url: str, title: str, message: str) -> bool:
    if not ntfy_url:
        return False
    server, _, topic = ntfy_url.rstrip("/").rpartition("/")
    payload = {"topic": topic, "title": title, "message": message, "tags": ["page_facing_up"]}
    try:
        resp = http.post(server, json=payload, timeout=10)
        resp.raise_for_status()
    except httpx.HTTPError:
        return False
    return True


def due_reminders(state: State, today: date) -> list[Reminder]:
    """One reminder 7 days before and one 1 day before; never for past deadlines."""
    reminders: list[Reminder] = []
    for stored in state.all_docs():
        for deadline in stored.analysis.deadlines:
            days_left = (deadline.due - today).days
            if not 0 <= days_left <= 7:
                continue
            kind = "d1" if days_left <= 1 else "d7"
            if state.was_notified(stored.document.id, deadline.due, kind):
                continue
            when = {0: "heute", 1: "morgen"}.get(days_left, f"in {days_left} Tagen")
            reminders.append(
                Reminder(
                    doc_id=stored.document.id,
                    due=deadline.due,
                    kind=kind,
                    title=f"{deadline.label} {when}",
                    message=f"{stored.document.title}: fällig am {deadline.due:%d.%m.%Y}",
                )
            )
    return reminders


def send_reminders(http: httpx.Client, ntfy_url: str, state: State, today: date) -> int:
    if not ntfy_url:
        return 0
    sent = 0
    for reminder in due_reminders(state, today):
        if send(http, ntfy_url, reminder.title, reminder.message):
            state.mark_notified(reminder.doc_id, reminder.due, reminder.kind)
            sent += 1
    return sent
```

- [x] **Step 4: Run tests**

Run: `uv run pytest tests/test_ics_notify.py -v`
Expected: `8 passed`

- [x] **Step 5: Commit and push** with the message `feat: deadline calendar feed and ntfy reminders` (plus the trailer).
