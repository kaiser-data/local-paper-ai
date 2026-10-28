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
