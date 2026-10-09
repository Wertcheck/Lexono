"""Briefkoepfe einer Kanzlei: Aufloesen, Verwalten, Standard.

Ein "Briefkopf" ist entweder der des Kanzlei-Profils (`FIRM_LETTERHEAD_REF`, Briefkopffelder des
`FirmProfile`, Name in `FirmProfile.letterhead_name`) oder eine `Letterhead`-Zeile. Beide haben
dieselben Felder (Name/Anschrift/Kontakt/Logo/Unterzeichner) und werden von den Renderern
(app/export/letterhead.py, Editor, Export, Chat) gleich behandelt. Der Briefkopf eines Schriftsatzes
steht in `Draft.letterhead_ref`; Folgeversionen uebernehmen ihn (app/drafting/versioning.py).

Aufloesung: `None` (aeltere Entwuerfe) und unbekannte Referenzen -> Briefkopf des Kanzlei-Profils,
damit sich ein bestehender Entwurf nie stillschweigend aendert. Der Standard gilt nur fuer NEUE
Entwuerfe ohne ausdrueckliche Auswahl."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.firm_profile.service import get_firm_profile
from app.models import Draft, Letterhead

FIRM_LETTERHEAD_REF = "firm"

#: Felder, die beim Anlegen/Aendern eines Briefkopfs gesetzt werden duerfen.
EDITABLE_FIELDS = (
    "firm_name",
    "legal_form",
    "street",
    "address_addition",
    "postal_code",
    "city",
    "phone",
    "email",
    "website",
    "signatory_name",
)


class LetterheadError(ValueError):
    """Fachlicher Fehler (leerer/doppelter Name, nicht erlaubte Aktion) mit nutzerlesbarem Text."""


@dataclass(frozen=True)
class LetterheadChoice:
    ref: str
    name: str
    is_default: bool
    letterhead: Any  # FirmProfile oder Letterhead (gleiche Briefkopffelder)


def default_ref(db: Session) -> str:
    profile = get_firm_profile(db)
    if profile.default_letterhead_id and db.get(Letterhead, profile.default_letterhead_id) is not None:
        return profile.default_letterhead_id
    return FIRM_LETTERHEAD_REF


def list_letterheads(db: Session) -> list[LetterheadChoice]:
    """Alle Briefkoepfe: zuerst der des Kanzlei-Profils, danach die zusaetzlichen (Anlegereihenfolge)."""
    profile = get_firm_profile(db)
    current_default = default_ref(db)
    choices = [
        LetterheadChoice(
            FIRM_LETTERHEAD_REF,
            profile.letterhead_name or "Kanzlei allgemein",
            current_default == FIRM_LETTERHEAD_REF,
            profile,
        )
    ]
    for row in db.query(Letterhead).order_by(Letterhead.created_at.asc(), Letterhead.name.asc()).all():
        choices.append(LetterheadChoice(row.id, row.name, current_default == row.id, row))
    return choices


def resolve_letterhead(db: Session, ref: str | None) -> Any:
    """Briefkopf-Objekt zu einer Referenz (siehe Moduldocstring zu None/unbekannt)."""
    if ref and ref != FIRM_LETTERHEAD_REF:
        row = db.get(Letterhead, ref)
        if row is not None:
            return row
    return get_firm_profile(db)


def resolve_ref_for_new_draft(db: Session, requested_ref: str | None) -> str:
    """Ausdrueckliche, gueltige Auswahl -> diese; sonst der Standard."""
    if requested_ref == FIRM_LETTERHEAD_REF:
        return FIRM_LETTERHEAD_REF
    if requested_ref and db.get(Letterhead, requested_ref) is not None:
        return requested_ref
    return default_ref(db)


def letterhead_for_draft(db: Session, draft: Draft) -> Any:
    return resolve_letterhead(db, draft.letterhead_ref)


def letterhead_name(db: Session, ref: str | None) -> str:
    resolved = resolve_letterhead(db, ref)
    return getattr(resolved, "letterhead_name", None) or getattr(resolved, "name", "") or "Kanzlei allgemein"


# --- Verwaltung ----------------------------------------------------------------------------------


def _clean_name(db: Session, name: str, *, exclude_ref: str | None = None) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise LetterheadError("Der Briefkopf braucht einen Namen.")
    for choice in list_letterheads(db):
        if choice.ref != exclude_ref and choice.name.strip().lower() == cleaned.lower():
            raise LetterheadError(f'Es gibt bereits einen Briefkopf mit dem Namen "{cleaned}".')
    return cleaned


def _apply_fields(target: Any, fields: dict[str, str | None]) -> None:
    for key in EDITABLE_FIELDS:
        if key in fields:
            value = (fields[key] or "").strip()
            setattr(target, key, value if key == "firm_name" else (value or None))


def create_letterhead(db: Session, *, name: str, actor: str, **fields: str | None) -> Letterhead:
    cleaned = _clean_name(db, name)
    if not (fields.get("firm_name") or "").strip():
        raise LetterheadError("Der Briefkopf braucht einen Kanzleinamen.")
    row = Letterhead(name=cleaned, firm_name="", updated_by_actor=actor)
    _apply_fields(row, fields)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_letterhead(db: Session, ref: str, *, name: str, actor: str, **fields: str | None) -> Any:
    """Aendert einen Briefkopf (auch den des Kanzlei-Profils: Name = `letterhead_name`).
    Entwuerfe, die ihn verwenden, zeigen sofort die neuen Daten (der Briefkopf wird beim Anzeigen
    und Exportieren aus den Briefkopfdaten zusammengesetzt) - ein Wechsel auf einen ANDEREN
    Briefkopf ist dagegen eine eigene, nachvollziehbare Entwurfsversion."""
    target = resolve_letterhead(db, ref)
    is_firm = not isinstance(target, Letterhead)
    cleaned = _clean_name(db, name, exclude_ref=FIRM_LETTERHEAD_REF if is_firm else target.id)
    if not (fields.get("firm_name") or "").strip():
        raise LetterheadError("Der Briefkopf braucht einen Kanzleinamen.")
    _apply_fields(target, fields)
    if is_firm:
        target.letterhead_name = cleaned
    else:
        target.name = cleaned
    target.updated_by_actor = actor
    db.commit()
    return target


def set_default(db: Session, ref: str) -> None:
    profile = get_firm_profile(db)
    if ref == FIRM_LETTERHEAD_REF:
        profile.default_letterhead_id = None
    elif db.get(Letterhead, ref) is not None:
        profile.default_letterhead_id = ref
    else:
        raise LetterheadError("Dieser Briefkopf existiert nicht.")
    db.commit()


def usage_count(db: Session, ref: str) -> int:
    return db.query(Draft).filter(Draft.letterhead_ref == ref).count()


def delete_letterhead(db: Session, ref: str) -> None:
    """Loescht einen zusaetzlichen Briefkopf - nicht den des Kanzlei-Profils und nicht, solange
    Entwuerfe ihn verwenden (sie wuerden sonst stillschweigend einen anderen Briefkopf bekommen)."""
    if ref == FIRM_LETTERHEAD_REF:
        raise LetterheadError("Der Briefkopf des Kanzlei-Profils kann nicht gelöscht werden.")
    row = db.get(Letterhead, ref)
    if row is None:
        raise LetterheadError("Dieser Briefkopf existiert nicht.")
    used = usage_count(db, ref)
    if used:
        raise LetterheadError(
            f"Der Briefkopf wird von {used} Entwurf/Entwürfen verwendet und kann nicht gelöscht werden."
        )
    profile = get_firm_profile(db)
    if profile.default_letterhead_id == ref:
        profile.default_letterhead_id = None
    db.delete(row)
    db.commit()
