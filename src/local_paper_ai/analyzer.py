"""Combine rules and AI into a verdict and deadlines, each backed by evidence."""

from __future__ import annotations

import re
from datetime import date

from local_paper_ai.ai.prompts import DEADLINES_SYSTEM, RETENTION_SYSTEM, document_text
from local_paper_ai.ai.router import Extractor
from local_paper_ai.ai.schemas import AIDeadlines, AIRetention
from local_paper_ai.models import Analysis, Deadline, Document, Evidence, Verdict
from local_paper_ai.rules import Rule, add_years, match, verdict_from_rule

_WS = re.compile(r"\s+")
_DEADLINE = re.compile(
    r"(?P<kw>zahlbar bis(?: zum)?|fällig (?:am|bis)|spätestens (?:bis )?(?:zum |am )?"
    r"|frist(?:ende)?(?: bis)?:?|bis (?:spätestens )?zum)\s*"
    r"(?P<d>\d{1,2})\.\s?(?P<m>\d{1,2})\.\s?(?P<y>\d{4})",
    re.IGNORECASE,
)
_DEADLINE_HINTS = (
    "frist",
    "bis zum",
    "innerhalb",
    "spätestens",
    "kündig",
    "widerspruch",
    "einspruch",
    "zahlbar",
    "fällig",
)


def _norm(text: str) -> str:
    return _WS.sub(" ", text).strip().casefold()


def grounded(quote: str, text: str) -> bool:
    """True if the quote is long enough and appears verbatim (ignoring whitespace and case)."""
    q = _norm(quote)
    return len(q) >= 8 and q in _norm(text)


def regex_deadlines(text: str) -> list[Deadline]:
    found: list[Deadline] = []
    for m in _DEADLINE.finditer(text):
        try:
            due = date(int(m["y"]), int(m["m"]), int(m["d"]))
        except ValueError:
            continue
        is_payment = m["kw"].casefold().startswith(("zahlbar", "fällig"))
        label = "Zahlung fällig" if is_payment else "Frist"
        found.append(Deadline(due=due, label=label, evidence=Evidence(quote=m[0], source="regex")))
    return found


def analyze(doc: Document, rules: list[Rule], router: Extractor) -> Analysis:
    if not doc.content.strip():
        return Analysis(doc_id=doc.id, verdict=_unsure("unknown"))
    hit = match(doc, rules)
    verdict = verdict_from_rule(hit, doc) if hit else _ai_verdict(doc, router)
    return Analysis(
        doc_id=doc.id, verdict=verdict, deadlines=_deadlines(doc, router, verdict.category)
    )


def _unsure(category: str) -> Verdict:
    return Verdict(kind="keep_original", category=category, needs_review=True)


def _ai_verdict(doc: Document, router: Extractor) -> Verdict:
    # Category is not known yet, so "unknown" keeps the call on local providers.
    ai = router.extract(RETENTION_SYSTEM, document_text(doc), AIRetention, "unknown")
    if ai is None or not grounded(ai.quote, doc.content):
        return _unsure("unknown")
    evidence = Evidence(quote=ai.quote, source="ai", detail=ai.reason)
    if ai.verdict == "keep_until":
        if not ai.keep_years:
            return _unsure(ai.category)
        return Verdict(
            kind="keep_until",
            keep_until=add_years(doc.created, ai.keep_years),
            category=ai.category,
            evidence=[evidence],
        )
    return Verdict(kind=ai.verdict, category=ai.category, evidence=[evidence])


def _deadlines(doc: Document, router: Extractor, category: str) -> list[Deadline]:
    found = regex_deadlines(doc.content)
    lowered = doc.content.casefold()
    if any(hint in lowered for hint in _DEADLINE_HINTS):
        ai = router.extract(DEADLINES_SYSTEM, document_text(doc), AIDeadlines, category)
        for item in ai.items if ai else []:
            if grounded(item.quote, doc.content):
                evidence = Evidence(quote=item.quote, source="ai")
                found.append(Deadline(due=item.due, label=item.label, evidence=evidence))
    unique: dict[date, Deadline] = {}
    for deadline in found:
        if deadline.due >= doc.created:
            unique.setdefault(deadline.due, deadline)
    return sorted(unique.values(), key=lambda d: d.due)
