"""Striktes Schema für Klassifikationsergebnisse.

Jedes Ergebnis MUSS: einen Dokumenttyp aus einer festen, bekannten Menge
haben (kein Freitext, der versehentlich Unsinn enthalten könnte), eine
Konfidenz zwischen 0 und 1, und eine nicht-leere Begründung. Das erzwingt
die Konzeptvorgabe "strikt validiertes JSON-Schema" bereits auf
Code-Ebene, nicht erst durch Konvention.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

# Bewusst eine feste, überschaubare Menge - erweiterbar, aber nicht
# beliebiger Freitext. "Sonstiges" und "Unbekannt" fangen alles andere auf,
# ohne dass der Klassifikator sich einen Typ ausdenken kann.
ALLOWED_DOCUMENT_TYPES = frozenset(
    {
        "Rechnung",
        "Vollmacht",
        "Kündigungsschreiben",
        "Mahnung",
        "Klage/Schriftsatz",
        "Gerichtliches Schreiben",
        "Vertrag",
        # Steuerrechtliche Dokumenttypen (14.09.): ECHTER FUND beim
        # Durchspielen der synthetischen Kanzlei-Datenbasis gegen den
        # PRODUKTIVEN DocumentProcessingService - eine Datei namens
        # "steuerbescheid_2025_*.pdf" mit dem Wort "Steuerbescheid" im Text
        # wurde als "Unbekannt" (Konfidenz 0.1) eingestuft, weil die
        # Typliste KEINEN EINZIGEN steuerrechtlichen Dokumenttyp enthielt.
        # Fuer die Pilotkanzlei (Steuerfachanwaltskanzlei) sind das die mit
        # Abstand haeufigsten Dokumente ueberhaupt.
        "Steuerbescheid",
        "Einspruch",
        "Prüfungsanordnung",
        "Steuererklärung",
        "Sonstiges",
        "Unbekannt",
    }
)


class ClassificationResult(BaseModel):
    document_type: str
    # Im Text gefundenes moegliches Aktenzeichen - KEINE Zuordnung, nur ein
    # Hinweis fuer den Matter-Matching-Schritt (Prompt 09).
    possible_matter_reference: str | None = None
    # Im Text gefundene moegliche Namen/Beteiligte - bewusst als Hinweise,
    # nicht als bestaetigte Zuordnung zu Party-Datensaetzen.
    possible_parties: list[str] = Field(default_factory=list)
    topic: str | None = None
    action_required: bool = False
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str

    @field_validator("document_type")
    @classmethod
    def document_type_must_be_known(cls, value: str) -> str:
        if value not in ALLOWED_DOCUMENT_TYPES:
            raise ValueError(
                f"document_type muss einer von {sorted(ALLOWED_DOCUMENT_TYPES)} sein, "
                f"nicht {value!r}"
            )
        return value

    @field_validator("reasoning")
    @classmethod
    def reasoning_must_not_be_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("reasoning darf nicht leer sein")
        return value

    def requires_manual_review(self, threshold: float) -> bool:
        """True, wenn die Konfidenz unter der konfigurierten Schwelle
        liegt - in diesem Fall darf (ab Prompt 09) keine automatische
        Aktenzuordnung erfolgen."""
        return self.confidence < threshold
