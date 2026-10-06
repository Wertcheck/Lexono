"""Kanzleifachprofil – fachliche Schwerpunkte der Kanzlei (03.10., Owner-
Direktive "KANZLEIFACHPROFIL UND JURISTISCHE WISSENSSTEUERUNG").

Reine Lese-/Schreiblogik fuer `FirmPracticeArea` (siehe dortiges Modul fuer
die volle Architekturbegruendung: installationsweites Singleton, keine
Mehr-Kanzlei-Frage, eigene Tabelle statt CSV-Spalte). Getrennt von
`app/firm_profile/service.py` (das nur `get_firm_profile` enthaelt), weil
diese Datei eine eigene, nicht triviale Validierungsregel traegt
(Abgleich gegen `PRACTICE_AREA_SUGGESTIONS`) - "Klare Trennung der
Verantwortlichkeiten" (Direktive §5)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.clients.service import PRACTICE_AREA_SUGGESTIONS
from app.firm_profile.service import get_firm_profile
from app.models import AuditEvent, FirmPracticeArea


class InvalidPracticeAreaError(Exception):
    """Mindestens ein uebermittelter Wert steht nicht in
    `PRACTICE_AREA_SUGGESTIONS` - wird VOR jedem Schreibzugriff geprueft
    (Direktive §4.2: "konsistente Transaktionen"), damit ein ungueltiger
    Wert niemals auch nur teilweise gespeichert wird."""

    def __init__(self, invalid_values: list[str]) -> None:
        self.invalid_values = invalid_values
        super().__init__(
            "Ungueltige Rechtsgebiete: " + ", ".join(invalid_values)
        )


@dataclass(frozen=True)
class PracticeAreaOption:
    """Eine Zeile fuer die Checkbox-Liste im Kanzleiprofil - trennt
    bewusst die aktuell GUELTIGEN Vorschlaege (`PRACTICE_AREA_SUGGESTIONS`)
    von bereits gespeicherten, aber nicht mehr erkannten Werten (siehe
    `get_display_options` unten, Direktive §2: "Ungueltige oder nicht
    mehr verfuegbare Rechtsgebiets-IDs kontrolliert behandeln")."""

    name: str
    selected: bool
    recognized: bool


def get_selected_practice_areas(db: Session) -> list[str]:
    """Die aktuell gespeicherten Schwerpunkte der Kanzlei, alphabetisch
    sortiert - genutzt sowohl von der Profilseite als auch von jeder
    kuenftigen Relevanzintegration (z. B. Kanzleiwissen-Sortierung)."""
    profile = get_firm_profile(db)
    return sorted(area.practice_area for area in profile.practice_areas)


def get_display_options(db: Session) -> list[PracticeAreaOption]:
    """Vollstaendige Liste fuer die Profilseite: jeder aktuell gueltige
    Vorschlag (angekreuzt, falls bereits gespeichert) PLUS jeder
    gespeicherte, aber nicht mehr in `PRACTICE_AREA_SUGGESTIONS`
    enthaltene Wert (als "nicht mehr verfuegbar" markiert, trotzdem
    anzeigbar/entfernbar - niemals stillschweigend geloescht)."""
    selected = set(get_selected_practice_areas(db))
    options = [
        PracticeAreaOption(name=name, selected=name in selected, recognized=True)
        for name in PRACTICE_AREA_SUGGESTIONS
    ]
    orphaned = sorted(selected - set(PRACTICE_AREA_SUGGESTIONS))
    options.extend(
        PracticeAreaOption(name=name, selected=True, recognized=False) for name in orphaned
    )
    return options


def set_practice_areas(db: Session, selected: list[str], *, actor: str) -> list[str]:
    """Ersetzt die Auswahl der GUELTIGEN Schwerpunkte (Checkbox-Liste:
    angekreuzt = gewuenschter Endzustand) in EINER Transaktion (Direktive
    §4.2: "konsistente Transaktionen") - deckt "hinzufuegen"/"aendern"/
    "entfernen" fuer alle Werte aus `PRACTICE_AREA_SUGGESTIONS` gemeinsam
    ab. Validiert VOR jeder Aenderung an der Session - ein ungueltiger
    Wert fuehrt zu `InvalidPracticeAreaError`, OHNE dass irgendetwas
    geschrieben wird.

    Bereits gespeicherte, inzwischen "verwaiste" Werte (nicht mehr in
    `PRACTICE_AREA_SUGGESTIONS`, siehe `PracticeAreaOption.recognized`)
    bleiben von dieser Funktion UNBERUEHRT - sie werden separat, einzeln
    und explizit ueber `remove_practice_area` entfernt (Direktive:
    "kontrolliert behandeln", kein stillschweigendes Zwangs-Cleanup bei
    jedem Speichern)."""
    cleaned = sorted({value.strip() for value in selected if value.strip()})
    invalid = [value for value in cleaned if value not in PRACTICE_AREA_SUGGESTIONS]
    if invalid:
        raise InvalidPracticeAreaError(invalid)

    profile = get_firm_profile(db)
    current_recognized = {
        area.practice_area: area
        for area in profile.practice_areas
        if area.practice_area in PRACTICE_AREA_SUGGESTIONS
    }
    target = set(cleaned)

    for name, row in current_recognized.items():
        if name not in target:
            db.delete(row)
    for name in target - set(current_recognized):
        db.add(FirmPracticeArea(firm_profile_id=profile.id, practice_area=name))

    db.add(
        AuditEvent(
            entity_type="FirmProfile",
            entity_id=profile.id,
            event_type="firm_practice_areas_updated",
            actor=actor,
            details=f"Fachliche Schwerpunkte gesetzt: {', '.join(sorted(target)) or '(keine)'}",
        )
    )
    db.commit()
    return sorted(target)


def remove_practice_area(db: Session, practice_area: str, *, actor: str) -> None:
    """Entfernt GENAU EINEN gespeicherten Schwerpunkt - der einzige Weg,
    einen "verwaisten" (nicht mehr in `PRACTICE_AREA_SUGGESTIONS`
    enthaltenen) Wert loszuwerden, siehe settings.html (Tab "Kanzlei").
    Funktioniert
    unveraendert auch fuer einen aktuell gueltigen Wert (aequivalent zum
    Abwaehlen in der Checkbox-Liste) - keine zweite Code-Pfad-Logik
    noetig. Idempotent: ein bereits entfernter/nie vorhandener Wert ist
    ein wirkungsloses No-Op, kein Fehler."""
    profile = get_firm_profile(db)
    row = next(
        (area for area in profile.practice_areas if area.practice_area == practice_area), None
    )
    if row is None:
        return
    db.delete(row)
    db.add(
        AuditEvent(
            entity_type="FirmProfile",
            entity_id=profile.id,
            event_type="firm_practice_areas_updated",
            actor=actor,
            details=f"Fachlicher Schwerpunkt entfernt: {practice_area}",
        )
    )
    db.commit()
