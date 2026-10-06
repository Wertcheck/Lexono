"""LawImportService – Import/Abruf für die digitale Gesetzesbibliothek
(20.08.).

Import ist bewusst UPSERT/idempotent (Schlüssel: `law_code` +
`section_number`) - beliebig oft wiederholbar (Skript, Bootstrap beim
ersten Seitenaufruf, siehe app/web/laws_router.py), ohne Duplikate zu
erzeugen. Inhalte entstehen ausschließlich über diese kontrollierte
Import-Funktion aus lokalen JSON-Fixtures - siehe app/models/law.py-
Moduldocstring ("Die KI darf keine Quelle erfinden")."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models import Law, LawSection

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

_SECTION_NUMBER_DIGITS_RE = re.compile(r"\d+")


class LawFixtureError(Exception):
    """Fixture-Datei fehlt einem Pflichtfeld oder ist kein gültiges JSON -
    wird VOR jedem Schreibzugriff geprüft."""


@dataclass
class LawImportResult:
    law_code: str
    law_created: bool
    sections_created: int
    sections_updated: int


def _require_field(data: dict, field: str, *, context: str) -> object:
    if field not in data or data[field] in (None, ""):
        raise LawFixtureError(f"Pflichtfeld '{field}' fehlt ({context})")
    return data[field]


def import_law_fixture_data(db: Session, data: dict) -> LawImportResult:
    """Importiert EIN geparstes Fixture-Dict (siehe app/laws/fixtures/*.json
    für das Format) - legt das Gesetzeswerk an (falls neu) und
    aktualisiert/erstellt jeden Paragraphen einzeln.

    Validiert/parst BEWUSST das GESAMTE Fixture-Dict VOR dem ersten
    Schreibzugriff auf die Session (siehe `_parsed_sections` unten) -
    ein fehlerhafter Paragraph weiter hinten in der Datei darf keine
    Karteileiche hinterlassen (Gesetzeswerk oder vorherige Paragraphen
    bereits `db.add()`-ed, aber wegen des Fehlers nie committet -
    "gefunden" durch einen Test, der genau das prüft, siehe
    tests/test_laws_service.py)."""
    code = str(_require_field(data, "code", context="Gesetzeswerk")).strip()
    title = str(_require_field(data, "title", context="Gesetzeswerk")).strip()

    parsed_sections: list[tuple[str, str, str, date]] = []
    for entry in data.get("sections", []):
        section_number = str(
            _require_field(entry, "section_number", context=f"{code}-Paragraph")
        ).strip()
        entry_title = str(_require_field(entry, "title", context=section_number)).strip()
        text_content = str(
            _require_field(entry, "text_content", context=section_number)
        ).strip()
        last_updated_raw = _require_field(entry, "last_updated", context=section_number)
        last_updated = date.fromisoformat(str(last_updated_raw))
        parsed_sections.append((section_number, entry_title, text_content, last_updated))

    law = db.query(Law).filter_by(code=code).first()
    law_created = False
    if law is None:
        law = Law(code=code, title=title)
        db.add(law)
        db.flush()
        law_created = True
    else:
        law.title = title

    sections_created = 0
    sections_updated = 0
    for section_number, entry_title, text_content, last_updated in parsed_sections:
        existing = (
            db.query(LawSection)
            .filter_by(law_code=code, section_number=section_number)
            .first()
        )
        if existing is None:
            db.add(
                LawSection(
                    law_code=code,
                    section_number=section_number,
                    title=entry_title,
                    text_content=text_content,
                    last_updated=last_updated,
                )
            )
            sections_created += 1
        else:
            existing.title = entry_title
            existing.text_content = text_content
            existing.last_updated = last_updated
            sections_updated += 1

    db.commit()
    return LawImportResult(
        law_code=code,
        law_created=law_created,
        sections_created=sections_created,
        sections_updated=sections_updated,
    )


def import_law_fixture_file(db: Session, path: Path) -> LawImportResult:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LawFixtureError(f"Fixture-Datei '{path.name}' nicht lesbar: {exc}") from exc
    return import_law_fixture_data(db, data)


def import_all_fixtures(db: Session, *, fixtures_dir: Path | None = None) -> list[LawImportResult]:
    """Importiert ALLE `*.json`-Dateien im Fixture-Verzeichnis (Standard:
    app/laws/fixtures/) - genutzt vom Setup-Skript
    (scripts/import_law_fixtures.py) UND vom lazy Bootstrap in
    app/web/laws_router.py (leere Bibliothek beim ersten Aufruf)."""
    directory = fixtures_dir or FIXTURES_DIR
    results = []
    for path in sorted(directory.glob("*.json")):
        results.append(import_law_fixture_file(db, path))
    return results


def sort_sections_naturally(sections: list[LawSection]) -> list[LawSection]:
    """"§ 2" vor "§ 13" statt lexikografisch "§ 13" vor "§ 2" - extrahiert
    die erste Zahl aus `section_number` als Sortierschlüssel, Text-Reste
    (z. B. "a"/"b"-Anhänge wie "§ 823a") als sekundäres Kriterium."""

    def sort_key(section: LawSection) -> tuple[int, str]:
        match = _SECTION_NUMBER_DIGITS_RE.search(section.section_number)
        number = int(match.group()) if match else 0
        return (number, section.section_number)

    return sorted(sections, key=sort_key)


def get_laws(db: Session) -> list[Law]:
    return db.query(Law).order_by(Law.code).all()


def get_law_stats(db: Session) -> dict[str, dict]:
    """Echte Kennzahlen je Gesetz (Anzahl Normen + juengster Stand) - EINE
    gruppierte Abfrage statt eines Zaehl-Querys pro Gesetz (kein N+1).
    Verschoben aus app/web/laws_router.py (26.09., Owner-Direktive
    "KANZLEIWISSEN FINAL PRODUCT IMPLEMENTATION"), damit die neue
    Kanzleiwissen-Katalogtabelle (app/web/knowledge_router.py) dieselbe
    Berechnung nutzt statt einer zweiten, abweichenden Kopie."""
    stats_rows = (
        db.query(
            LawSection.law_code,
            func.count(LawSection.id),
            func.max(LawSection.last_updated),
        )
        .group_by(LawSection.law_code)
        .all()
    )
    return {code: {"count": count, "stand": stand} for code, count, stand in stats_rows}


def toggle_law_active(db: Session, law_code: str, *, active: bool) -> Law | None:
    """Echte Aktivierung/Deaktivierung (26.09., Owner-Direktive
    "KANZLEIWISSEN FINAL PRODUCT IMPLEMENTATION" §4/§5) - loescht KEINE
    bereits heruntergeladenen Paragraphen (kein erneuter Download beim
    Wiedereinschalten noetig), blendet die Quelle aber aus den KI-
    Funktionen aus (siehe app/chat/service.py::_find_law_section,
    app/search/global_search_service.py::_search_law_sections). Liefert
    `None`, wenn das Gesetz lokal (noch) gar nicht installiert ist - der
    Aufrufer (Router) unterscheidet das von einem echten Fehler."""
    law = get_law_by_code(db, law_code)
    if law is None:
        return None
    law.is_active = active
    db.commit()
    db.refresh(law)
    return law


def get_law_by_code(db: Session, law_code: str) -> Law | None:
    return db.query(Law).filter_by(code=law_code).first()


def get_sections(db: Session, law_code: str, *, search: str | None = None) -> list[LawSection]:
    query = db.query(LawSection).filter_by(law_code=law_code)
    if search and search.strip():
        like = f"%{search.strip()}%"
        query = query.filter(
            or_(LawSection.section_number.ilike(like), LawSection.title.ilike(like))
        )
    return sort_sections_naturally(query.all())
