"""Auflösung des persistenten Datenverzeichnisses (Prompt 36/37).

Entscheidung (siehe ARCHITECTURE.md, Abschnitt zum Windows-Installer):
Konfiguration (.env), Datenbank und Dokumentenspeicher liegen NICHT im
Installationsverzeichnis (typischerweise "Program Files", nur für
Administratoren beschreibbar und kein sinnvoller Ort für sich ständig
ändernde Mandantendaten) und NICHT in einem vom Nutzerprofil abhängigen,
potenziell durch OneDrive-Ordnerschutz ("Bekannte Ordner sichern") überwachten
Verzeichnis wie Dokumente/Desktop/Bilder. Stattdessen: `%PROGRAMDATA%`
(Standard-Windows-Konvention für maschinenweite, nicht profilgebundene
Anwendungsdaten, per Voreinstellung von normalen Nutzerkonten beschreibbar,
NICHT Teil des OneDrive-"Bekannte Ordner"-Satzes).

KanzleiAI → Lexono Migration (Produktidentitäts-Bereinigung): der
Unterverzeichnisname wechselt von `KanzleiAI` auf `Lexono`. Bestehende
Installationen mit echten Kanzleidaten unter `%PROGRAMDATA%\\KanzleiAI`
dürfen dabei NIEMALS verloren gehen - siehe `_migrate_legacy_dir_if_needed`
für die genaue, bewusst konservative Umsetzung (echter, atomarer
Verzeichnis-Rename, mit sicherem Fallback auf das alte Verzeichnis, falls
der Rename aus irgendeinem Grund nicht möglich ist).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

#: Überschreibt die automatische Ermittlung vollständig - nützlich für Tests
#: und für einen künftigen Portable-/Entwicklungsmodus. Der alte Name bleibt
#: als Fallback gültig (keine stillschweigend brechende Änderung für
#: bestehende Entwickler-/Testskripte, die ihn ggf. bereits setzen).
_OVERRIDE_ENV_VAR = "LEXONO_DATA_DIR"
_LEGACY_OVERRIDE_ENV_VAR = "KANZLEI_AI_DATA_DIR"

#: Name des Unterverzeichnisses unterhalb von %PROGRAMDATA%.
_APP_DIR_NAME = "Lexono"
#: Vorheriger Name (Produktidentität "KanzleiAI") - wird bei Bedarf einmalig
#: und sicher auf `_APP_DIR_NAME` migriert, siehe `_migrate_legacy_dir_if_needed`.
_LEGACY_APP_DIR_NAME = "KanzleiAI"


def _repair_legacy_env_paths(data_dir: Path, legacy_path: Path) -> None:
    """Korrigiert eine bereits bestehende `.env` unterhalb von `data_dir`,
    falls sie noch ABSOLUTE Pfade auf das alte Legacy-Verzeichnis enthaelt.

    ROOT CAUSE (real auf einer Installation gefunden, per direkter
    Dateisystem-/DB-Inspektion bewiesen, nicht angenommen): `app/setup/
    env_writer.py::build_env_content()` schreibt `DATABASE_URL`/
    `INTAKE_STORAGE_DIR`/`MAIL_ATTACHMENT_STORAGE_DIR`/`LOG_FILE_PATH` als
    ABSOLUTE Pfad-Strings, gebildet aus `data_dir.as_posix()` ZUM
    SCHREIBZEITPUNKT. `app/config/settings.py::Settings.database_url` liest
    diesen String beim Programmstart wortwoertlich - er wird NIE anhand des
    AKTUELLEN `resolve_data_dir()`-Ergebnisses neu berechnet. Ein reiner
    Verzeichnis-Rename (das Einzige, was diese Funktion bisher tat) aendert
    aber nur den ORDNERNAMEN, nie den TEXTINHALT der mitgewanderten `.env` -
    eine `.env`, die geschrieben wurde, als `resolve_data_dir()` noch den
    Legacy-Namen lieferte, zeigt danach weiterhin auf den alten Pfad, obwohl
    der Ordner selbst laengst umbenannt ist. Ergebnis (real beobachtet):
    die Anwendung oeffnet eine ANDERE, an einem alten Pfad liegende SQLite-
    Datei als die, die tatsaechlich neben der `.env` liegt - eine stille
    Daten-/Konto-Aufspaltung, kein Absturz, keine Fehlermeldung.

    Bewusst ein einfacher, generischer Text-Ersatz des alten absoluten
    Verzeichnis-Prefixes durch das neue (statt schluesselweises Parsen):
    JEDER von `build_env_content` erzeugte Pfad-Wert beginnt mit demselben
    `data_dir.as_posix()`-Prefix, ebenso jeder spaeter vom Dashboard
    (`update_env_values`) ergaenzte Pfad-Wert, der demselben Muster folgt -
    ein einziger Ersatz deckt daher automatisch auch zukuenftige, hier nicht
    einzeln aufgezaehlte Felder ab. Idempotent (kein Effekt, falls der alte
    Prefix nicht mehr vorkommt) und veraendert ausschliesslich Textinhalt,
    NIEMALS Datenbank-/Dokumentendateien selbst.
    """
    env_path = data_dir / ".env"
    if not env_path.exists():
        return
    legacy_prefix = legacy_path.as_posix()
    current_prefix = data_dir.as_posix()
    if legacy_prefix == current_prefix:
        return
    try:
        content = env_path.read_text(encoding="utf-8")
    except OSError:
        logger.warning(
            "'.env' unter '%s' konnte nicht auf veraltete Legacy-Pfade "
            "geprueft werden (nicht lesbar) - unveraendert gelassen.",
            env_path,
            exc_info=True,
        )
        return
    if legacy_prefix not in content:
        return
    try:
        env_path.write_text(content.replace(legacy_prefix, current_prefix), encoding="utf-8")
    except OSError:
        logger.warning(
            "'.env' unter '%s' enthaelt veraltete absolute Pfade auf '%s', "
            "konnte aber nicht korrigiert werden (nicht beschreibbar).",
            env_path,
            legacy_path,
            exc_info=True,
        )
        return
    logger.warning(
        "'.env' unter '%s' enthielt noch absolute Pfade auf das alte "
        "Verzeichnis '%s' - auf '%s' korrigiert, damit die Anwendung "
        "garantiert die neben dieser '.env' liegenden Daten verwendet, "
        "nicht eine andere, am alten Pfad liegende Datenbank.",
        env_path,
        legacy_path,
        data_dir,
    )


def _migrate_legacy_dir_if_needed(new_path: Path, legacy_path: Path) -> Path:
    """Migriert ein bestehendes `KanzleiAI`-Datenverzeichnis EINMALIG und
    sicher auf den neuen `Lexono`-Namen, sofern noch nicht migriert - und
    korrigiert dabei (siehe `_repair_legacy_env_paths`) eine ggf. bereits
    bestehende `.env` unterhalb von `new_path`, falls diese noch absolute
    Pfade auf `legacy_path` enthaelt (egal ob durch DIESEN Rename entstanden
    oder aus einem frueheren Lauf bereits vorhanden).

    Bewusst konservativ:
    - existiert `new_path` bereits (auch als leeres Verzeichnis, z. B. aus
      einem vorherigen, teilweise fehlgeschlagenen Lauf), wird am
      Verzeichnis selbst NICHTS angefasst - `new_path` gilt dann als bereits
      maßgeblich, um niemals versehentlich zwei Datensätze zu vermischen.
      Eine dort bereits vorhandene `.env` wird trotzdem auf veraltete
      Legacy-Pfade geprueft/korrigiert (s.o.).
    - existiert `legacy_path` nicht, ist nichts zu migrieren (Normalfall bei
      jeder Neuinstallation).
    - der eigentliche Rename ist eine einzelne, atomare Dateisystem-
      operation (`Path.rename`, gleiches Volume garantiert innerhalb von
      `%PROGRAMDATA%`) - kein Kopieren, kein Löschen, kein Zwischenzustand,
      in dem Daten an zwei Orten oder an keinem Ort lägen.
    - schlägt der Rename aus irgendeinem Grund fehl (z. B. eine Datei ist
      gerade durch einen anderen Prozess geöffnet, fehlende Berechtigung):
      NIEMALS abstürzen oder Daten unerreichbar machen - stattdessen wird
      weiterhin `legacy_path` zurückgegeben (funktional identisch zum
      bisherigen, ungewanderten Zustand), nur mit einer Warnung im Log.
    """
    if new_path.exists():
        _repair_legacy_env_paths(new_path, legacy_path)
        return new_path
    if not legacy_path.exists():
        return new_path
    try:
        legacy_path.rename(new_path)
        logger.info(
            "Datenverzeichnis von '%s' auf '%s' migriert (KanzleiAI -> Lexono "
            "Produktidentitaets-Bereinigung).",
            legacy_path,
            new_path,
        )
        _repair_legacy_env_paths(new_path, legacy_path)
        return new_path
    except OSError:
        logger.warning(
            "Migration von '%s' auf '%s' fehlgeschlagen - bestehendes "
            "Verzeichnis wird unveraendert weiterverwendet, keine Daten "
            "wurden veraendert oder verschoben.",
            legacy_path,
            new_path,
            exc_info=True,
        )
        return legacy_path


def resolve_data_dir(*, is_windows: bool | None = None) -> Path:
    """Liefert das persistente Datenverzeichnis für diese Installation.

    Reihenfolge: explizite Überschreibung (`LEXONO_DATA_DIR`, oder der
    ältere Name `KANZLEI_AI_DATA_DIR`) > Windows-Standardpfad
    (`%PROGRAMDATA%\\Lexono`, mit sicherer Einmal-Migration von einem
    bestehenden `%PROGRAMDATA%\\KanzleiAI`, siehe
    `_migrate_legacy_dir_if_needed`) > Fallback für Nicht-Windows-
    Entwicklungsumgebungen (`~/.lexono`) - das Projekt zielt ausschließlich
    auf Windows als Installationsplattform (siehe CLAUDE.md/HANDOFF), der
    Fallback existiert nur, damit dieses Modul auch außerhalb von Windows
    importierbar/testbar bleibt.

    `is_windows` ist bewusst injizierbar (statt die Entscheidung nur intern
    an `os.name` festzumachen): `os.name` global per `monkeypatch.setattr`
    umzubiegen bricht auf Python 3.13 nachweislich `Path.home()`, das seinerseits
    intern auf `os.name` prüft (siehe git-history dieser Datei/zugehöriger
    Test) - ein injizierbarer Parameter macht beide Zweige testbar, ohne den
    tatsächlichen Plattform-Zustand zu verändern.
    """
    override = os.environ.get(_OVERRIDE_ENV_VAR) or os.environ.get(_LEGACY_OVERRIDE_ENV_VAR)
    if override:
        return Path(override)

    if is_windows is None:
        is_windows = os.name == "nt"

    if is_windows:
        program_data = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        base = Path(program_data)
        return _migrate_legacy_dir_if_needed(
            base / _APP_DIR_NAME, base / _LEGACY_APP_DIR_NAME
        )

    return _migrate_legacy_dir_if_needed(
        Path.home() / ".lexono", Path.home() / ".kanzlei_ai"
    )
