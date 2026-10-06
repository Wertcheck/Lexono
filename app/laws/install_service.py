"""Installations-Orchestrierung für die Kanzleiwissen-Weboberfläche (26.09.,
Owner-Direktive "KANZLEIWISSEN FINAL PRODUCT IMPLEMENTATION").

Verbindet NUR bereits bestehende Bausteine (app/laws/catalog.py,
app/laws/gesetze_im_internet.py, app/laws/service.py) mit einem echten
Hintergrund-Download+Fortschritt, den `app/web/laws_router.py` per HTMX-
Polling anzeigen kann - KEIN neues Gesetzesmodell, KEIN zweiter
Downloadservice (Direktive §32): dieselben `Law`/`LawSection`-Tabellen,
derselbe `import_norm_sections`.

Fortschritt lebt bewusst NUR im Prozessspeicher (ein einfaches Dict statt
einer eigenen DB-Tabelle) - Lexono ist eine lokale Single-User-Desktop-
Anwendung (siehe run.py), ein Download-Fortschritt muss keinen
Neustart/mehrere Prozesse überleben; verschwindet er durch einen Neustart
mitten im Download, zeigt die Seite beim nächsten Laden einfach wieder
"Nicht installiert" - kein inkonsistenter Zustand, da `Law`/`LawSection`
in diesem Fall (Absturz vor dem abschließenden Commit in
`import_norm_sections`) ohnehin nie geschrieben wurden."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.laws.catalog import CatalogEntry, get_catalog_entry_for_code
from app.laws.gesetze_im_internet import (
    GesetzeImInternetError,
    ParsedNormSection,
    extract_xml_from_zip,
    fetch_law_xml_zip,
    fetch_source_etag,
    import_norm_sections,
    parse_law_xml,
)
from app.models import Law

logger = logging.getLogger(__name__)

# Reihenfolge entspricht der von der Direktive verlangten Statusmaschine
# (§13): "Die UI darf niemals einen technischen Zwischenzustand als
# erfolgreichen Zustand darstellen."
STATUS_DOWNLOADING = "downloading"
STATUS_INSTALLING = "installing"
STATUS_INSTALLED = "installed"
STATUS_ERROR = "error"

# --- Automatisierte Aktualisierung (03.10., Owner-Direktive "RELIABLE
# LEGAL KNOWLEDGE UPDATES") ---
# Persistente Pruef-/Update-Zustaende (`Law.last_check_status`) - bewusst
# eine ANDERE Konstantengruppe als STATUS_* oben: STATUS_* beschreibt den
# EPHEMEREN Fortschritt eines GERADE LAUFENDEN Downloads (lebt nur im
# Prozessspeicher, siehe `_progress`), waehrend CHECK_* den DAUERHAFTEN
# Ausgang des letzten abgeschlossenen Pruef- bzw. Update-Versuchs in der
# DB festhaelt (ueberlebt einen Neustart). Direktive Phase B verlangt
# genau diese fuenf, klar unterscheidbaren Zustaende:
#   CHECK_UNCHANGED         - Quelle enthaelt nachweislich keine Aenderung
#                             (ETag identisch zum zuletzt importierten).
#   CHECK_UPDATE_AVAILABLE  - ETag weicht ab, neue Fassung noch nicht
#                             uebernommen.
#   CHECK_UPDATED           - neue Fassung wurde validiert und erfolgreich
#                             gespeichert (von `_run_install` gesetzt).
#   CHECK_FAILED            - Abruf/Parsing/Validierung/Speicherung der
#                             neuen Fassung ist fehlgeschlagen (von
#                             `_run_install` gesetzt, letzte gueltige
#                             Fassung bleibt unveraendert erhalten).
#   CHECK_UNREACHABLE       - die Status-/Versionspruefung selbst konnte
#                             nicht durchgefuehrt werden (Quelle nicht
#                             erreichbar, kein ETag, bereits ein Lauf
#                             aktiv, Gesetz/Katalogeintrag unbekannt) -
#                             wird NIE mit CHECK_UNCHANGED verwechselt.
CHECK_UNCHANGED = "unchanged"
CHECK_UPDATE_AVAILABLE = "update_available"
CHECK_UPDATED = "updated"
CHECK_FAILED = "failed"
CHECK_UNREACHABLE = "unreachable"

# Direktive §4.2: "Verwende keine willkürlichen festen Normenzahlen als
# universelles Qualitätskriterium." - deshalb KEIN fixer Schwellenwert wie
# "mindestens 50 Normen", sondern ein RELATIVER Vergleich mit dem
# BISHERIGEN Bestand DESSELBEN Gesetzes (siehe `_validate_new_sections`).
# Nur angewendet, wenn vorher bereits eine nicht-triviale Anzahl Normen
# vorlag (`_MIN_PREVIOUS_COUNT_FOR_CHECK`) - ein Gesetz mit z. B. 2 Normen
# darf durch eine legitime Novelle durchaus auf 1 schrumpfen, ohne dass
# das als technischer Fehler gewertet wird.
_MIN_RETENTION_RATIO = 0.5
_MIN_PREVIOUS_COUNT_FOR_RATIO_CHECK = 5


@dataclass
class InstallProgress:
    status: str
    received_bytes: int = 0
    total_bytes: int | None = None
    error_message: str | None = None

    @property
    def percent(self) -> int | None:
        if self.total_bytes and self.total_bytes > 0:
            return min(100, round(self.received_bytes / self.total_bytes * 100))
        return None


_progress: dict[str, InstallProgress] = {}
_progress_lock = threading.Lock()


@dataclass
class LawUpdateCheckResult:
    """Ergebnis einer einzelnen, leichtgewichtigen Pruefung (siehe
    `check_law_for_update`) - `status` ist einer der `CHECK_*`-Werte
    oben."""

    law_code: str
    status: str
    message: str | None = None


def _validate_new_sections(
    law_code: str,
    *,
    previous_section_count: int,
    sections: list[ParsedNormSection],
) -> None:
    """Pruefung VOR jeder Uebernahme in die produktiven `Law`/`LawSection`-
    Tabellen (Direktive §4.2) - wirft `GesetzeImInternetError` bei einer
    technisch unplausiblen Antwort, damit `_run_install` sie wie jeden
    anderen Abruf-/Parse-Fehler behandelt (der Aufrufer rollt die Session
    zurueck, die zuletzt gueltige Fassung bleibt vollstaendig erhalten -
    siehe dortigen Kommentar). Greift sowohl bei einer Erstinstallation
    (`previous_section_count == 0`) als auch bei einer Aktualisierung."""
    if not sections:
        raise GesetzeImInternetError("Keine zitierfähigen Einzelnormen in der Quelle gefunden.")

    seen_numbers: set[str] = set()
    for section in sections:
        if section.section_number in seen_numbers:
            raise GesetzeImInternetError(
                f"'{law_code}': Norm '{section.section_number}' kommt in der Quelle "
                "mehrfach vor - unplausible Antwort, keine Übernahme."
            )
        seen_numbers.add(section.section_number)

    if (
        previous_section_count >= _MIN_PREVIOUS_COUNT_FOR_RATIO_CHECK
        and len(sections) < previous_section_count * _MIN_RETENTION_RATIO
    ):
        raise GesetzeImInternetError(
            f"'{law_code}': neue Fassung enthält nur {len(sections)} von zuvor "
            f"{previous_section_count} Normen (unter "
            f"{int(_MIN_RETENTION_RATIO * 100)} %) - wirkt wie eine unvollständige "
            "oder fehlerhafte Antwort, keine Übernahme."
        )


def check_law_for_update(db: Session, law_code: str) -> LawUpdateCheckResult:
    """Leichtgewichtige Aenderungspruefung fuer EIN bereits installiertes
    Gesetz (03.10., Owner-Direktive "RELIABLE LEGAL KNOWLEDGE UPDATES",
    Phase C 4.1) - lädt NIEMALS den vollen Gesetzesinhalt, nur den HTTP-
    Status der offiziellen Quelle (`fetch_source_etag`, ein HEAD-Request).
    Persistiert Ergebnis + Zeitpunkt SOFORT (auch bei Fehlschlag), damit
    der angezeigte Status jederzeit dem echten letzten Versuch entspricht
    - niemals ein stiller/stummer Fehlschlag.

    Reine Pruefung, KEINE inhaltliche Aenderung an `LawSection` - das
    Uebernehmen einer erkannten neuen Fassung bleibt ein separater,
    expliziter Schritt (`start_install`, siehe Docstring dort)."""
    law = db.query(Law).filter_by(code=law_code).first()
    if law is None:
        return LawUpdateCheckResult(
            law_code, CHECK_UNREACHABLE, "Gesetz ist lokal nicht installiert."
        )
    entry = get_catalog_entry_for_code(law_code)
    if entry is None:
        return LawUpdateCheckResult(
            law_code, CHECK_UNREACHABLE, "Keine offizielle Quelle für dieses Gesetz bekannt."
        )
    if is_install_running(law_code):
        return LawUpdateCheckResult(
            law_code, CHECK_UNREACHABLE, "Es läuft bereits ein Download/Update für dieses Gesetz."
        )

    now = datetime.now(timezone.utc)
    try:
        remote_etag = fetch_source_etag(entry.slug)
    except GesetzeImInternetError as exc:
        law.last_checked_at = now
        law.last_check_status = CHECK_UNREACHABLE
        law.last_check_error = str(exc)
        db.commit()
        return LawUpdateCheckResult(law_code, CHECK_UNREACHABLE, str(exc))

    law.last_checked_at = now
    if remote_etag is None:
        # Direktive: "Eine fehlgeschlagene Prüfung darf niemals als 'keine
        # Änderungen vorhanden' ausgegeben werden." - ohne ETag gibt es
        # keinen belastbaren Vergleichswert, also ehrlich "nicht
        # überprüfbar" statt eines erfundenen "unverändert".
        law.last_check_status = CHECK_UNREACHABLE
        law.last_check_error = (
            "Die Quelle liefert keinen ETag - Änderungserkennung auf diesem Weg nicht möglich."
        )
        db.commit()
        return LawUpdateCheckResult(law_code, CHECK_UNREACHABLE, law.last_check_error)

    law.last_check_error = None
    if law.source_etag == remote_etag:
        law.last_check_status = CHECK_UNCHANGED
        db.commit()
        return LawUpdateCheckResult(law_code, CHECK_UNCHANGED)

    law.last_check_status = CHECK_UPDATE_AVAILABLE
    db.commit()
    return LawUpdateCheckResult(law_code, CHECK_UPDATE_AVAILABLE)


def get_progress(law_code: str) -> InstallProgress | None:
    with _progress_lock:
        return _progress.get(law_code)


def _set_progress(law_code: str, progress: InstallProgress | None) -> None:
    with _progress_lock:
        if progress is None:
            _progress.pop(law_code, None)
        else:
            _progress[law_code] = progress


def is_install_running(law_code: str) -> bool:
    progress = get_progress(law_code)
    return progress is not None and progress.status in (STATUS_DOWNLOADING, STATUS_INSTALLING)


def _run_install(entry: CatalogEntry) -> None:
    """Läuft in einem Hintergrund-Thread (siehe `start_install`) - braucht
    deshalb eine EIGENE DB-Session (die Request-Session ist zu diesem
    Zeitpunkt längst geschlossen)."""
    try:
        _set_progress(entry.code, InstallProgress(status=STATUS_DOWNLOADING))

        def on_progress(received: int, total: int | None) -> None:
            _set_progress(
                entry.code,
                InstallProgress(status=STATUS_DOWNLOADING, received_bytes=received, total_bytes=total),
            )

        zip_bytes = fetch_law_xml_zip(entry.slug, on_progress=on_progress)
        _set_progress(
            entry.code,
            InstallProgress(status=STATUS_INSTALLING, received_bytes=len(zip_bytes), total_bytes=len(zip_bytes)),
        )
        xml_bytes = extract_xml_from_zip(zip_bytes)
        sections = parse_law_xml(xml_bytes, law_code=entry.code)

        db = SessionLocal()
        try:
            # Validierung VOR jeder Uebernahme (03.10., "RELIABLE LEGAL
            # KNOWLEDGE UPDATES" §4.2/§4.3) - ersetzt die vorherige reine
            # "if not sections"-Pruefung durch die vollstaendige
            # Plausibilitaetspruefung (`_validate_new_sections`), die auch
            # eine Aktualisierung (nicht nur eine Erstinstallation)
            # korrekt gegen den BISHERIGEN Bestand DESSELBEN Gesetzes
            # prueft. Ein hier geworfener `GesetzeImInternetError` wird
            # unten im `except Exception`-Block zurueckgerollt (noch
            # nichts geschrieben) und dann vom AEUSSEREN
            # `except GesetzeImInternetError`-Handler behandelt - die
            # zuletzt gueltige Fassung bleibt dadurch garantiert
            # unveraendert.
            existing_law = db.query(Law).filter_by(code=entry.code).first()
            previous_section_count = len(existing_law.sections) if existing_law is not None else 0
            _validate_new_sections(
                entry.code, previous_section_count=previous_section_count, sections=sections
            )

            import_norm_sections(
                db, law_code=entry.code, law_title=entry.title, law_slug=entry.slug, sections=sections
            )
            law = db.query(Law).filter_by(code=entry.code).first()
            if law is not None:
                now = datetime.now(timezone.utc)
                law.is_active = True
                law.source_size_bytes = len(zip_bytes)
                # Nachvollziehbarkeit (Direktive §4.4): dieser Lauf hat die
                # Fassung tatsaechlich erfolgreich uebernommen - sowohl der
                # "wann zuletzt geprueft" ALS AUCH der "wann zuletzt
                # inhaltlich aktualisiert"-Zeitstempel werden gesetzt.
                law.last_checked_at = now
                law.last_source_update_at = now
                law.last_check_status = CHECK_UPDATED
                law.last_check_error = None
                try:
                    # Baseline-ETag fuer kuenftige Checks festhalten - ein
                    # EIGENER HEAD-Request direkt nach dem erfolgreichen
                    # Download, da `fetch_law_xml_zip` den ETag der
                    # tatsaechlich geladenen Antwort nicht zurueckgibt
                    # (bewusst unveraenderte Signatur, siehe dortigen
                    # Docstring - der bestehende Aufrufer
                    # scripts/import_gesetze_im_internet.py bleibt dadurch
                    # unberuehrt). Ein Fehlschlag HIER darf den bereits
                    # erfolgreichen Import nicht zunichtemachen.
                    law.source_etag = fetch_source_etag(entry.slug)
                except GesetzeImInternetError:
                    logger.warning(
                        "Baseline-ETag für '%s' konnte nach erfolgreichem Import nicht "
                        "ermittelt werden - nächster Check zeigt sicherheitshalber "
                        "'Aktualisierung verfügbar'.",
                        entry.code,
                    )
                db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

        # Erfolgreich abgeschlossen: Fortschritt wird entfernt statt auf
        # "installed" gesetzt - der TATSAECHLICHE Installationsstatus kommt
        # ab jetzt aus der echten `Law`-Tabelle (Direktive §13: kein
        # Zwischenzustand darf als dauerhafter Erfolg missverstanden
        # werden - die DB-Zeile IST die Wahrheit, nicht dieser Cache).
        _set_progress(entry.code, None)
    except GesetzeImInternetError as exc:
        logger.warning("Installation von '%s' fehlgeschlagen: %s", entry.code, exc)
        # WICHTIG: `_record_failed_check` (eigene DB-Arbeit) MUSS VOR
        # `_set_progress(..., STATUS_ERROR, ...)` laufen - `is_install_
        # running()`/`_wait_until_terminal` (siehe Tests) gelten den
        # Hintergrund-Thread bereits als fertig, sobald der Fortschritt
        # einen terminalen Status zeigt. Waere die Reihenfolge vertauscht,
        # koennte ein Aufrufer (Test oder UI-Polling) die `Law`-Zeile
        # bereits lesen, WAEHREND dieser Thread sie noch per eigener,
        # unter derselben `SessionLocal()` laufender Session schreibt -
        # ein echter, per Testlauf reproduzierter Nebenlaeufigkeitsfehler.
        _record_failed_check(entry.code, str(exc))
        _set_progress(entry.code, InstallProgress(status=STATUS_ERROR, error_message=str(exc)))
    except Exception as exc:  # noqa: BLE001 - siehe Kommentar: darf die Seite nie unbeobachtet abstürzen lassen
        logger.exception("Unerwarteter Fehler bei der Installation von '%s'", entry.code)
        message = f"Unerwarteter Fehler: {exc}"
        _record_failed_check(entry.code, message)
        _set_progress(entry.code, InstallProgress(status=STATUS_ERROR, error_message=message))


def _record_failed_check(law_code: str, message: str) -> None:
    """Persistiert einen fehlgeschlagenen Update-/Installationsversuch auf
    der `Law`-Zeile (03.10., Direktive §4.4) - NUR wenn das Gesetz bereits
    lokal existiert (bei einer gescheiterten ERSTinstallation gibt es noch
    keine Zeile, die den Fehlschlag tragen könnte; der `_progress`-Eintrag
    oben deckt diesen Fall bereits vollständig ab). Eigene, kurzlebige
    Session (läuft im selben Hintergrund-Thread wie `_run_install`, dessen
    eigene Session zu diesem Zeitpunkt bereits geschlossen/zurückgerollt
    ist)."""
    db = SessionLocal()
    try:
        law = db.query(Law).filter_by(code=law_code).first()
        if law is None:
            return
        law.last_checked_at = datetime.now(timezone.utc)
        law.last_check_status = CHECK_FAILED
        law.last_check_error = message
        db.commit()
    finally:
        db.close()


def start_install(law_code: str) -> bool:
    """Startet den echten Download+Import im Hintergrund. Liefert False,
    wenn der Code nicht im Katalog steht oder bereits ein Versuch läuft
    (kein doppelter paralleler Download derselben Quelle)."""
    entry = get_catalog_entry_for_code(law_code)
    if entry is None:
        return False
    if is_install_running(law_code):
        return True
    _set_progress(law_code, InstallProgress(status=STATUS_DOWNLOADING))
    thread = threading.Thread(target=_run_install, args=(entry,), daemon=True)
    thread.start()
    return True


def clear_error(law_code: str) -> None:
    """Setzt einen Fehlerzustand zurück (z. B. bevor "Erneut versuchen"
    einen neuen Download startet) - siehe Direktive §15."""
    progress = get_progress(law_code)
    if progress is not None and progress.status == STATUS_ERROR:
        _set_progress(law_code, None)
