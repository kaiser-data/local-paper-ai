### Task 4: Retention rules engine

**Files:**
- Create: `src/local_paper_ai/rules.py`, `rules.yaml`
- Test: `tests/test_rules.py`

**Interfaces:**
- Consumes: `Document`, `Evidence`, `Verdict`, `VerdictKind` (Task 2).
- Produces:
  - `RuleMatch(keywords, document_types, correspondents, tags)`
  - `Rule(name, category, verdict: VerdictKind, years: int|None, match: RuleMatch)` (`keep_until` requires `years`)
  - `RuleHit(rule, evidence)`
  - `load_rules(path) -> list[Rule]` (the file is a **top-level YAML list**)
  - `append_rule(path, rule)`
  - `match(doc, rules) -> RuleHit | None` (the first matching rule wins)
  - `add_years(d, years) -> date`
  - `verdict_from_rule(hit, doc) -> Verdict`

- [x] **Step 1: Write the failing test** `tests/test_rules.py`

```python
import shutil
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from local_paper_ai.models import Document
from local_paper_ai.rules import (
    Rule,
    RuleMatch,
    add_years,
    append_rule,
    load_rules,
    match,
    verdict_from_rule,
)

ROOT = Path(__file__).resolve().parents[1]
RULES = load_rules(ROOT / "rules.yaml")


def _doc(content: str, **kw: object) -> Document:
    return Document(id=1, title="Brief", content=content, created=date(2026, 3, 14), **kw)  # type: ignore[arg-type]


def test_default_rules_valid_and_originals_first() -> None:
    assert len(RULES) >= 10
    assert RULES[0].verdict == "keep_original"


def test_keyword_match_is_case_insensitive_and_quotes_original_text() -> None:
    hit = match(_doc("Anbei Ihre GEBURTSURKUNDE zurück."), RULES)
    assert hit is not None
    assert hit.rule.name == "personenstandsurkunde"
    assert hit.evidence.quote == "GEBURTSURKUNDE"
    assert hit.evidence.source == "rule"


def test_document_type_match() -> None:
    hit = match(_doc("irgendein Text", document_type="Steuerbescheid"), RULES)
    assert hit is not None and hit.rule.name == "steuerbescheid"


def test_first_rule_wins() -> None:
    hit = match(_doc("Kontoauszug Nr. 4 - Rate aus Ihrem Darlehensvertrag"), RULES)
    assert hit is not None and hit.rule.name == "kredit-buergschaft"


def test_no_match() -> None:
    assert match(_doc("Liebe Grüße aus dem Urlaub"), RULES) is None


def test_verdict_from_rule_keep_until() -> None:
    doc = _doc("Einkommensteuerbescheid 2025")
    hit = match(doc, RULES)
    assert hit is not None
    v = verdict_from_rule(hit, doc)
    assert v.kind == "keep_until"
    assert v.keep_until == date(2036, 3, 14)
    assert v.category == "tax"
    assert v.evidence[0].quote == "Einkommensteuerbescheid"


def test_add_years_leap_day() -> None:
    assert add_years(date(2024, 2, 29), 1) == date(2025, 2, 28)


def test_keep_until_requires_years() -> None:
    with pytest.raises(ValidationError):
        Rule(name="x", category="c", verdict="keep_until", match=RuleMatch())


def test_append_rule_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "rules.yaml"
    shutil.copy(ROOT / "rules.yaml", path)
    rule = Rule(
        name="manuell-1",
        category="insurance",
        verdict="keep_until",
        years=3,
        match=RuleMatch(keywords=["Hausratversicherung"]),
    )
    append_rule(path, rule)
    loaded = load_rules(path)
    assert loaded[-1] == rule
    assert len(loaded) == len(RULES) + 1
```

- [x] **Step 2: Run it**

Run: `uv run pytest tests/test_rules.py -v`
Expected: FAIL with `No module named 'local_paper_ai.rules'`

- [x] **Step 3: Implement** `src/local_paper_ai/rules.py`

```python
"""Deterministic retention rules loaded from rules.yaml (a top-level YAML list)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, TypeAdapter, model_validator

from local_paper_ai.models import Document, Evidence, Verdict, VerdictKind


class RuleMatch(BaseModel):
    keywords: list[str] = Field(default_factory=list)
    document_types: list[str] = Field(default_factory=list)
    correspondents: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class Rule(BaseModel):
    name: str
    category: str
    verdict: VerdictKind
    years: int | None = None
    match: RuleMatch

    @model_validator(mode="after")
    def _years_for_keep_until(self) -> Rule:
        if self.verdict == "keep_until" and not self.years:
            raise ValueError(f"rule {self.name}: keep_until needs years")
        return self


@dataclass(frozen=True)
class RuleHit:
    rule: Rule
    evidence: Evidence


_RULES = TypeAdapter(list[Rule])


def load_rules(path: Path) -> list[Rule]:
    return _RULES.validate_python(yaml.safe_load(path.read_text(encoding="utf-8")) or [])


def append_rule(path: Path, rule: Rule) -> None:
    """Append without rewriting the file, so comments in rules.yaml survive."""
    block = yaml.safe_dump(
        [rule.model_dump(mode="json", exclude_none=True)], allow_unicode=True, sort_keys=False
    )
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\n" + block)


def _evidence(rule: Rule, quote: str, what: str) -> Evidence:
    return Evidence(quote=quote, source="rule", detail=f"Regel {rule.name} ({what})")


def match(doc: Document, rules: list[Rule]) -> RuleHit | None:
    """First matching rule wins, so keep_original rules belong at the top."""
    haystack = f"{doc.title}\n{doc.content}"
    for rule in rules:
        m = rule.match
        if doc.document_type and doc.document_type in m.document_types:
            return RuleHit(rule, _evidence(rule, doc.document_type, "Dokumenttyp"))
        if doc.correspondent and doc.correspondent in m.correspondents:
            return RuleHit(rule, _evidence(rule, doc.correspondent, "Absender"))
        for tag in doc.tags:
            if tag in m.tags:
                return RuleHit(rule, _evidence(rule, tag, "Tag"))
        for keyword in m.keywords:
            found = re.search(re.escape(keyword), haystack, re.IGNORECASE)
            if found:
                return RuleHit(rule, _evidence(rule, found.group(0), "Stichwort"))
    return None


def add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # 29 February
        return d.replace(year=d.year + years, day=28)


def verdict_from_rule(hit: RuleHit, doc: Document) -> Verdict:
    rule = hit.rule
    keep_until = (
        add_years(doc.created, rule.years) if rule.verdict == "keep_until" and rule.years else None
    )
    return Verdict(
        kind=rule.verdict, keep_until=keep_until, category=rule.category, evidence=[hit.evidence]
    )
```

`rules.yaml`:
```yaml
# local-paper-ai retention rules: defaults for German households.
# NOT legal advice. Check and adjust them for your situation.
# The first matching rule wins, so keep "keep_original" rules at the top.
# verdict:  keep_original | keep_until (needs years) | may_shred
# category: used by the privacy lock (tax, health, identity, finance are local-only by default)
# Rules saved from the review page are appended at the end.

- name: personenstandsurkunde
  category: identity
  verdict: keep_original
  match:
    keywords: [Geburtsurkunde, Heiratsurkunde, Eheurkunde, Sterbeurkunde, Abstammungsurkunde, Lebenspartnerschaftsurkunde]
- name: notariell
  category: legal
  verdict: keep_original
  match:
    keywords: [notariell beurkundet, Urkundenrolle, Erbschein, Testament, Notar]
- name: grundbuch
  category: property
  verdict: keep_original
  match:
    keywords: [Grundbuchauszug, Grundbuch, Grundschuld]
- name: fahrzeugbrief
  category: vehicle
  verdict: keep_original
  match:
    keywords: [Zulassungsbescheinigung Teil II, Fahrzeugbrief]
- name: zeugnis
  category: education
  verdict: keep_original
  match:
    keywords: [Abiturzeugnis, Zeugnis der allgemeinen Hochschulreife, Bachelorurkunde, Masterurkunde, Diplomurkunde, Promotionsurkunde, Arbeitszeugnis, Gesellenbrief, Meisterbrief]
- name: rente
  category: pension
  verdict: keep_original
  match:
    keywords: [Deutsche Rentenversicherung, Versicherungsverlauf, Rentenbescheid, Renteninformation]
- name: kredit-buergschaft
  category: finance
  verdict: keep_original
  match:
    keywords: [Darlehensvertrag, Kreditvertrag, Bürgschaft]
- name: steuerbescheid
  category: tax
  verdict: keep_until
  years: 10
  match:
    document_types: [Steuerbescheid]
    keywords: [Einkommensteuerbescheid, Steuerbescheid, Bescheid über Einkommensteuer]
- name: handwerkerrechnung
  category: property
  verdict: keep_until
  years: 2
  match:
    keywords: [Handwerkerleistung, Arbeitskosten, Lohnanteil, "§14b UStG", "§ 14b UStG"]
- name: kaufbeleg-garantie
  category: purchase
  verdict: keep_until
  years: 2
  match:
    keywords: [Garantie, Gewährleistung, Kassenbon]
- name: kontoauszug
  category: finance
  verdict: may_shred
  match:
    keywords: [Kontoauszug]
- name: gehaltsabrechnung
  category: finance
  verdict: may_shred
  match:
    keywords: [Entgeltabrechnung, Gehaltsabrechnung, Lohnabrechnung, Verdienstabrechnung]
- name: nebenkosten
  category: household
  verdict: may_shred
  match:
    keywords: [Stromrechnung, Abschlagszahlung, Nebenkostenabrechnung, Jahresabrechnung]
- name: arzt
  category: health
  verdict: may_shred
  match:
    keywords: [Arztrechnung, Befund, Krankenkasse, Diagnose]
```

- [x] **Step 4: Run tests**

Run: `uv run pytest tests/test_rules.py -v`
Expected: `9 passed`

- [x] **Step 5: Commit and push** with the message `feat: retention rules engine with German defaults` (plus the trailer).
