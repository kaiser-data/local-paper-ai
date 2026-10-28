from __future__ import annotations

from local_paper_ai.models import Document

MAX_CHARS = 12_000

RETENTION_SYSTEM = """\
Du prüfst ein eingescanntes Dokument aus einem deutschen Privathaushalt.
Entscheide, ob das Papier-Original nach dem Scannen weggeworfen werden darf.
- keep_original: das Original hat rechtlichen Wert (Urkunden, notarielle Verträge,
  Zeugnisse, Fahrzeugbrief, Kredit- und Bürgschaftsverträge, Rentennachweise).
- keep_until: bis zu einer Frist aufbewahren; gib keep_years an
  (z. B. Steuerbescheid 10, Handwerkerrechnung 2, Kaufbeleg mit Garantie 2).
- may_shred: die digitale Kopie reicht.
Im Zweifel keep_original.
"quote" MUSS ein wörtliches Zitat (mindestens 8 Zeichen) aus dem Dokument sein,
das die Entscheidung belegt. "reason" ist ein kurzer deutscher Satz.
Antworte nur mit JSON nach dem Schema."""

DEADLINES_SYSTEM = """\
Du suchst in einem eingescannten Brief nach Fristen und Zahlungszielen
(Einspruchsfrist, Widerspruchsfrist, Zahlungsziel, Kündigungsfrist).
Rechne relative Fristen ("innerhalb eines Monats nach Bekanntgabe") ab dem
Dokumentdatum aus. Gib je Frist "due" (YYYY-MM-DD), ein kurzes deutsches "label"
und ein wörtliches Zitat "quote" aus dem Dokument an.
Keine Frist gefunden: "items" ist eine leere Liste. Antworte nur mit JSON nach dem Schema."""


def document_text(doc: Document) -> str:
    return (
        f"Titel: {doc.title}\n"
        f"Dokumentdatum: {doc.created.isoformat()}\n"
        f"Absender: {doc.correspondent or 'unbekannt'}\n\n"
        f"{doc.content[:MAX_CHARS]}"
    )
