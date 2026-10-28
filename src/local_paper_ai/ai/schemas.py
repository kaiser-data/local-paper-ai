from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from local_paper_ai.models import VerdictKind

Category = Literal[
    "tax",
    "health",
    "identity",
    "finance",
    "legal",
    "property",
    "vehicle",
    "education",
    "pension",
    "purchase",
    "household",
    "insurance",
    "other",
]


class AIRetention(BaseModel):
    category: Category
    verdict: VerdictKind
    keep_years: int | None = Field(default=None, ge=1, le=30)
    quote: str
    reason: str


class AIDeadline(BaseModel):
    due: date
    label: str
    quote: str


class AIDeadlines(BaseModel):
    items: list[AIDeadline] = Field(default_factory=list)
