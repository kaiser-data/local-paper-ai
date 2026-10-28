### Task 6: Analyzer (verdict + deadlines, grounded)

**Files:**
- Create: `src/local_paper_ai/analyzer.py`, `tests/fakes.py`, `tests/__init__.py` (empty)
- Test: `tests/test_analyzer.py`

**Interfaces:**
- Consumes:
  - `Extractor`, `AIRetention`, `AIDeadlines`, `AIDeadline`, `RETENTION_SYSTEM`, `DEADLINES_SYSTEM`, `document_text` (Task 5)
  - `Rule`, `match`, `verdict_from_rule`, `add_years`, `load_rules` (Task 4)
- Produces: `grounded(quote, text) -> bool`, `regex_deadlines(text) -> list[Deadline]`, `analyze(doc, rules, router: Extractor) -> Analysis`. Also `tests/fakes.py::FakeRouter(retention=None, deadlines=None)`, which records `.calls: list[tuple[str, str]]` as (schema name, category).

- [ ] **Step 1: Shared fake** `tests/fakes.py` (Task 8 appends `FakePaperless` to this file)

```python
"""In-memory fakes shared by the tests."""

from __future__ import annotations

from typing import Any

from local_paper_ai.ai.schemas import AIDeadlines, AIRetention


class FakeRouter:
    def __init__(
        self, retention: AIRetention | None = None, deadlines: AIDeadlines | None = None
    ) -> None:
        self.retention = retention
        self.deadlines = deadlines
        self.calls: list[tuple[str, str]] = []

    def extract(self, system: str, text: str, schema: type[Any], category: str) -> Any:
        self.calls.append((schema.__name__, category))
        if schema is AIRetention:
            return self.retention
        if schema is AIDeadlines:
            return self.deadlines
        return None
```

`tests/__init__.py`: an empty file (so `from tests.fakes import …` works).

- [ ] **Step 2: Write the failing test** `tests/test_analyzer.py`

```python
from datetime import date
from pathlib import Path

from local_paper_ai.ai.schemas import AIDeadline, AIDeadlines, AIRetention
from local_paper_ai.analyzer import analyze, grounded, regex_deadlines
from local_paper_ai.models import Document
from local_paper_ai.rules import load_rules
from tests.fakes import FakeRouter

ROOT = Path(__file__).resolve().parents[1]
RULES = load_rules(ROOT / "rules.yaml")
POLICY = "Sehr geehrte Frau Muster, anbei Ihre Versicherungspolice Nr. 123 für den Hausrat."


def _doc(content: str) -> Document:
    return Document(id=7, title="Brief", content=content, created=date(2026, 3, 14))


def test_grounded() -> None:
    assert grounded("innerhalb  eines\nMonats", "… innerhalb eines Monats nach …")
    assert not grounded("ab", "ab cd")
    assert not grounded("erfundenes Zitat hier", "ganz anderer Text")


def test_empty_content_needs_review_without_ai() -> None:
    router = FakeRouter()
    a = analyze(_doc("   "), RULES, router)
    assert a.verdict.kind == "keep_original" and a.verdict.needs_review
    assert router.calls == []


def test_rule_hit_skips_retention_ai() -> None:
    router = FakeRouter()
    a = analyze(_doc("Ihr Kontoauszug Nr. 3"), RULES, router)
    assert a.verdict.kind == "may_shred"
    assert a.verdict.evidence[0].source == "rule"
    assert all(name != "AIRetention" for name, _ in router.calls)


def test_grounded_ai_verdict_uses_unknown_category_for_privacy() -> None:
    router = FakeRouter(
        retention=AIRetention(
            category="insurance",
            verdict="may_shred",
            quote="Versicherungspolice Nr. 123",
            reason="Police, digitale Kopie reicht",
        )
    )
    a = analyze(_doc(POLICY), RULES, router)
    assert a.verdict.kind == "may_shred"
    assert a.verdict.category == "insurance"
    assert a.verdict.evidence[0].source == "ai"
    assert ("AIRetention", "unknown") in router.calls


def test_hallucinated_quote_needs_review() -> None:
    router = FakeRouter(
        retention=AIRetention(
            category="other", verdict="may_shred", quote="steht so nicht im Text", reason="x"
        )
    )
    a = analyze(_doc(POLICY), RULES, router)
    assert a.verdict.kind == "keep_original" and a.verdict.needs_review


def test_ai_keep_until_without_years_needs_review() -> None:
    router = FakeRouter(
        retention=AIRetention(
            category="insurance", verdict="keep_until", quote="Versicherungspolice", reason="x"
        )
    )
    a = analyze(_doc(POLICY), RULES, router)
    assert a.verdict.needs_review and a.verdict.category == "insurance"


def test_ai_keep_until_with_years() -> None:
    router = FakeRouter(
        retention=AIRetention(
            category="insurance",
            verdict="keep_until",
            keep_years=3,
            quote="Versicherungspolice",
            reason="x",
        )
    )
    a = analyze(_doc(POLICY), RULES, router)
    assert a.verdict.kind == "keep_until" and a.verdict.keep_until == date(2029, 3, 14)


def test_regex_deadlines() -> None:
    found = regex_deadlines("Bitte zahlbar bis 14.04.2026. Frist: 30.04.2026, sonst 31.02.2026")
    assert [(d.due, d.label) for d in found] == [
        (date(2026, 4, 14), "Zahlung fällig"),
        (date(2026, 4, 30), "Frist"),
    ]
    assert found[0].evidence.quote == "zahlbar bis 14.04.2026"


def test_ai_relative_deadline_grounded_and_category_passed() -> None:
    text = (
        "Einkommensteuerbescheid 2025. Gegen diesen Bescheid kann innerhalb eines Monats "
        "nach Bekanntgabe Einspruch eingelegt werden."
    )
    router = FakeRouter(
        deadlines=AIDeadlines(
            items=[
                AIDeadline(
                    due=date(2026, 4, 14),
                    label="Einspruchsfrist",
                    quote="innerhalb eines Monats nach Bekanntgabe",
                ),
                AIDeadline(due=date(2026, 5, 1), label="erfunden", quote="nicht im Dokument!"),
                AIDeadline(due=date(2020, 1, 1), label="alt", quote="Einspruch eingelegt"),
            ]
        )
    )
    a = analyze(_doc(text), RULES, router)
    assert a.verdict.category == "tax"
    assert [(d.due, d.label) for d in a.deadlines] == [(date(2026, 4, 14), "Einspruchsfrist")]
    assert ("AIDeadlines", "tax") in router.calls


def test_no_deadline_words_skips_deadline_ai() -> None:
    router = FakeRouter()
    analyze(_doc("Ihr Kontoauszug Nr. 3"), RULES, router)
    assert router.calls == []
```

- [ ] **Step 3: Run it**

Run: `uv run pytest tests/test_analyzer.py -v`
Expected: FAIL with `No module named 'local_paper_ai.analyzer'`

- [ ] **Step 4: Implement** `src/local_paper_ai/analyzer.py`

```python
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
    "frist", "bis zum", "innerhalb", "spätestens", "kündig",
    "widerspruch", "einspruch", "zahlbar", "fällig",
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
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_analyzer.py -v`
Expected: `10 passed`. Note: `test_no_deadline_words_skips_deadline_ai` relies on "Kontoauszug Nr. 3" containing none of the hint words. Keep it that way.

- [ ] **Step 6: Commit and push** with the message `feat: analyzer with grounded verdicts and deadline detection` (plus the trailer).
