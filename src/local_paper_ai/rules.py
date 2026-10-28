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
