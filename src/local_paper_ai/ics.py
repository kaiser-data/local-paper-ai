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
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


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
