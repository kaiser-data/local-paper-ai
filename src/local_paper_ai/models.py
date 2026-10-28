"""Domain types shared by all modules."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

VerdictKind = Literal["may_shred", "keep_original", "keep_until"]
EvidenceSource = Literal["rule", "ai", "regex", "manual"]


class Document(BaseModel):
    id: int
    title: str
    content: str
    created: date
    correspondent: str | None = None
    document_type: str | None = None
    tags: list[str] = Field(default_factory=list)
    page_count: int | None = None


class Evidence(BaseModel):
    quote: str
    source: EvidenceSource
    detail: str = ""


class Verdict(BaseModel):
    kind: VerdictKind
    keep_until: date | None = None
    category: str = "unknown"
    evidence: list[Evidence] = Field(default_factory=list)
    needs_review: bool = False


class Deadline(BaseModel):
    due: date
    label: str
    evidence: Evidence


class Analysis(BaseModel):
    doc_id: int
    verdict: Verdict
    deadlines: list[Deadline] = Field(default_factory=list)
