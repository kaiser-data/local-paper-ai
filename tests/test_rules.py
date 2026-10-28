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
