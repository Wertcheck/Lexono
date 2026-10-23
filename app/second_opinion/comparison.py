"""Vergleich lokale Ergebnisse ↔ Cloud-Zweitmeinung und Uebernahme eines Hinweises (§43, Stufe 1) - rein lokal.

Kernregel (Owner-Entscheidung 15.10.2026): Die Cloud darf nur ERGAENZEN und MARKIEREN - nie lokale Fristen oder
Klassifikationen entfernen, ueberschreiben oder bestaetigen.

- Lokale Ergebnisse gehen nur als unveraenderliche Schnappschuesse ein (`LocalSnapshot`, Tupel, frozen); die
  Funktionen lesen sie und erzeugen ausschliesslich NEUE Objekte.
- Cloud-Hinweise sind eigene Objekte (`CloudHint`) mit Quelle "cloud" und Pruefstatus "ungeprueft".
- Nur ein Ergebnis mit Status `ok` und einer gueltig gepruefte Zweitmeinung ergibt Hinweise. Datenschutzblock,
  Rueckfrage, Timeout, Fehler, Budget, Laengengrenze oder ungueltige Antwort ergeben KEINE Hinweise.
- Uebernahme nur durch ausdrueckliche Nutzerhandlung und nur fuer "nur Cloud"-Daten; Ergebnis ist ein NEUER
  Fristvorschlag mit `review_status="unreviewed"` - er muss wie jeder lokale Vorschlag bestaetigt werden.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from app.second_opinion.validation import DATE_KINDS, ValidatedOpinion


class OpinionStatus(str, Enum):
    OK = "ok"
    DISABLED = "deaktiviert"
    BLOCKED_PRIVACY = "datenschutz_blockiert"
    REVIEW_REQUIRED = "rueckfrage_erforderlich"
    TOO_LONG = "zu_lang"
    BUDGET_EXCEEDED = "budget_erschoepft"
    TIMEOUT = "zeitueberschreitung"
    ERROR = "fehler"
    INVALID = "ungueltig"


class Agreement(str, Enum):
    MATCH = "uebereinstimmend"
    ONLY_LOCAL = "nur_lokal"
    ONLY_CLOUD = "nur_cloud"
    DIFFERENT_KIND = "andere_art"


UNREVIEWED = "ungeprueft"


@dataclass(frozen=True)
class LocalDeadline:
    due_date: date
    kind: str  # frist | termin | fristbeginn


@dataclass(frozen=True)
class LocalSnapshot:
    """Lesekopie der lokalen Ergebnisse eines Dokuments (Typ und datierte Vorschlaege)."""

    document_type: str
    deadlines: tuple[LocalDeadline, ...]


@dataclass(frozen=True)
class OpinionOutcome:
    """Ergebnis einer Anforderung: Status plus - nur bei `ok` - die gepruefte Zweitmeinung."""

    status: OpinionStatus
    opinion: ValidatedOpinion | None = None
    detail: str | None = None


@dataclass(frozen=True)
class CloudHint:
    due_date: date
    agreement: Agreement
    cloud_kind: str | None
    local_kind: str | None
    source: str = "cloud"
    review_state: str = UNREVIEWED


@dataclass(frozen=True)
class Comparison:
    status: OpinionStatus
    hints: tuple[CloudHint, ...]
    type_agreement: bool | None  # None = keine Zweitmeinung
    cloud_type: str | None
    detail: str | None = None


@dataclass(frozen=True)
class DeadlineProposal:
    """Neuer Fristvorschlag aus einem uebernommenen Cloud-Hinweis - NIE bestaetigt, ersetzt nichts Lokales."""

    due_date: date
    kind: str
    review_status: str
    origin: str
    adopted_by: str


def kind_from_reasoning(reasoning: str | None) -> str | None:
    """Art eines lokalen Vorschlags aus der Begruendung des Extraktors ("Art: Termin" usw.); Dauern haben kein
    Datum und werden nicht verglichen."""
    text = reasoning or ""
    if "Art: Fristdauer" in text:
        return None
    if "Art: Termin" in text or "Art: Beendigungstermin" in text:
        # §74: das Cloud-Schema kennt nur frist/termin/fristbeginn - ein Beendigungstermin ist ein Termin ohne
        # Fristablauf (vorher "frist"); das Schema bleibt unveraendert
        return "termin"
    if "Art: Fristbeginn" in text:
        return "fristbeginn"
    return "frist"


def snapshot_from(document_type: str | None, deadlines: object) -> LocalSnapshot:
    """Liest Typ und datierte Vorschlaege aus beliebigen Objekten mit `due_date`/`reasoning` (z. B. ORM-Zeilen) -
    ausschliesslich lesend; abgelehnte Vorschlaege bleiben aussen vor."""
    items: list[LocalDeadline] = []
    for deadline in deadlines:  # type: ignore[attr-defined]
        due = getattr(deadline, "due_date", None)
        if getattr(deadline, "review_status", None) == "rejected" or due is None:
            continue
        kind = kind_from_reasoning(getattr(deadline, "reasoning", None))
        if kind is not None:
            items.append(LocalDeadline(due_date=due if type(due) is date else due.date(), kind=kind))
    return LocalSnapshot(document_type=document_type or "Unbekannt", deadlines=tuple(items))


def compare(local: LocalSnapshot, outcome: OpinionOutcome) -> Comparison:
    if outcome.status is not OpinionStatus.OK or outcome.opinion is None:
        status = OpinionStatus.INVALID if outcome.status is OpinionStatus.OK else outcome.status
        return Comparison(status=status, hints=(), type_agreement=None, cloud_type=None, detail=outcome.detail)
    opinion = outcome.opinion
    local_kinds: dict[date, set[str]] = {}
    for item in local.deadlines:
        local_kinds.setdefault(item.due_date, set()).add(item.kind)
    cloud_kinds: dict[date, str] = {}
    for kind, values in opinion.dates_by_kind().items():
        for value in values:
            cloud_kinds[value] = kind

    hints: list[CloudHint] = []
    for value in sorted(set(local_kinds) | set(cloud_kinds)):
        cloud_kind = cloud_kinds.get(value)
        locals_here = local_kinds.get(value, set())
        local_kind = next((k for k in DATE_KINDS if k in locals_here), None)
        if cloud_kind is None:
            agreement = Agreement.ONLY_LOCAL
        elif not locals_here:
            agreement = Agreement.ONLY_CLOUD
        elif cloud_kind in locals_here:
            agreement, local_kind = Agreement.MATCH, cloud_kind
        else:
            agreement = Agreement.DIFFERENT_KIND
        hints.append(CloudHint(due_date=value, agreement=agreement, cloud_kind=cloud_kind, local_kind=local_kind))
    return Comparison(status=OpinionStatus.OK, hints=tuple(hints),
                      type_agreement=opinion.typ == local.document_type, cloud_type=opinion.typ)


class AdoptionRefused(ValueError):
    """Uebernahme unzulaessig (kein ok-Ergebnis, kein "nur Cloud"-Hinweis oder keine ausdrueckliche Nutzerhandlung)."""


def adopt_hint(comparison: Comparison, hint: CloudHint, *, user_confirmed: bool, actor: str) -> DeadlineProposal:
    """Macht aus einem "nur Cloud"-Hinweis einen NEUEN, unbestaetigten Fristvorschlag. Nur auf ausdrueckliche
    Nutzerhandlung (`user_confirmed=True` und benannter Akteur) und nur aus einem `ok`-Vergleich."""
    if comparison.status is not OpinionStatus.OK:
        raise AdoptionRefused(f"keine Uebernahme bei Status {comparison.status.value}")
    if hint not in comparison.hints:
        raise AdoptionRefused("Hinweis stammt nicht aus diesem Vergleich")
    if hint.agreement is not Agreement.ONLY_CLOUD or hint.cloud_kind is None:
        raise AdoptionRefused("nur Daten, die allein die Cloud nennt, koennen als neuer Vorschlag uebernommen werden")
    if user_confirmed is not True or not isinstance(actor, str) or not actor.strip():
        raise AdoptionRefused("Uebernahme nur durch ausdrueckliche Handlung eines benannten Nutzers")
    return DeadlineProposal(due_date=hint.due_date, kind=hint.cloud_kind, review_status="unreviewed",
                            origin="cloud_zweitmeinung", adopted_by=actor.strip())
