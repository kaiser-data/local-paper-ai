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
