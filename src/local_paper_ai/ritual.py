"""Group analysed documents for the shred ritual."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from local_paper_ai.models import Analysis, Document
from local_paper_ai.state import State

GRAMS_PER_SHEET = 5  # A4 at 80 g/m²


@dataclass(frozen=True)
class RitualItem:
    doc: Document
    analysis: Analysis
    copies: int


@dataclass
class RitualView:
    shred_now: list[RitualItem] = field(default_factory=list)
    waiting_backup: list[RitualItem] = field(default_factory=list)
    keep_original: list[RitualItem] = field(default_factory=list)
    keep_until: list[RitualItem] = field(default_factory=list)
    review: list[RitualItem] = field(default_factory=list)
    shredded_docs: int = 0
    shredded_pages: int = 0

    @property
    def kg(self) -> float:
        return self.shredded_pages * GRAMS_PER_SHEET / 1000


def ritual_view(state: State, min_copies: int, today: date) -> RitualView:
    view = RitualView()
    view.shredded_docs, view.shredded_pages = state.shred_stats()
    for stored in state.all_docs():
        doc, analysis = stored.document, stored.analysis
        if state.is_shredded(doc.id):
            continue
        item = RitualItem(doc, analysis, state.verified_copies(doc.id))
        verdict = analysis.verdict
        expired = (
            verdict.kind == "keep_until"
            and verdict.keep_until is not None
            and verdict.keep_until < today
        )
        if verdict.needs_review:
            view.review.append(item)
        elif verdict.kind == "may_shred" or expired:
            group = view.shred_now if item.copies >= min_copies else view.waiting_backup
            group.append(item)
        elif verdict.kind == "keep_original":
            view.keep_original.append(item)
        else:
            view.keep_until.append(item)
    return view
