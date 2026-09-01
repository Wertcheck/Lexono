"""Reine Erzeugung/Schreiblogik der Produktions-`.env`-Datei (Prompt 37;
schlüsselweise Nachbearbeitung ab 20.08. für app/web/settings_router.py).

`build_env_content` ist bewusst eine reine Funktion (kein Dateizugriff) -
so lässt sich der erzeugte Inhalt ohne Dateisystem-Fixtures prüfen.
`write_env_file` ist der EINMALIGE Ersteinrichtungs-Schreibvorgang und
bewusst NICHT überschreibend per Default (analog zu `scripts/
create_admin.py`, das ebenfalls idempotent ist und einen bestehenden Admin
nicht stillschweigend überschreibt).

`update_env_values` (neu, 20.08.) ist der GEGENTEIL-Fall: gezielte,
schlüsselweise Änderung EINER bereits bestehenden `.env` (z. B. Scan-Ordner
oder E-Mail-Zugangsdaten nachträglich über das Dashboard setzen, siehe
app/web/settings_router.py) - bewusst NICHT die gesamte Datei neu
schreiben, um `SESSION_SECRET_KEY` und alle sonstigen, dem Dashboard
unbekannten Werte unangetastet zu lassen."""

from __future__ import annotations

import json
import re
from pathlib import Path


def build_env_content(
    *,
    data_dir: Path,
    session_secret: str,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> str:
    """Erzeugt den vollständigen Inhalt der Produktions-`.env`-Datei.

    Alle Pfade liegen unterhalb von `data_dir` (siehe `paths.resolve_data_dir`)
    - bewusst NICHT im Installationsverzeichnis, siehe ARCHITECTURE.md.
    Pfadwerte werden in doppelte Anführungszeichen gesetzt, falls der vom
    Betriebssystem gelieferte Datenverzeichnis-Pfad Leerzeichen enthält
    (z. B. ein individuell gewähltes `KANZLEI_AI_DATA_DIR`).
    """
    data_dir_posix = data_dir.as_posix()
    database_path = f"{data_dir_posix}/data/kanzlei_ai.db"
    intake_dir = f"{data_dir_posix}/data/intake"
    mail_attachment_dir = f"{data_dir_posix}/data/mail_attachments"
    log_file_path = f"{data_dir_posix}/logs/kanzlei_ai.log"

    return (
        "# Automatisch erzeugt vom Setup-Assistenten (app/setup/wizard.py, Prompt 37).\n"
        "# NIEMALS von Hand editieren, ohne die Auswirkungen auf SESSION_SECRET_KEY\n"
        "# (aktive Sessions werden bei Aenderung ungueltig) und die Pfade unten zu kennen.\n"
        "\n"
        "APP_ENV=production\n"
        "\n"
        f'DATABASE_URL="sqlite:///{database_path}"\n'
        f'INTAKE_STORAGE_DIR="{intake_dir}"\n'
        f'MAIL_ATTACHMENT_STORAGE_DIR="{mail_attachment_dir}"\n'
        f'LOG_FILE_PATH="{log_file_path}"\n'
        "\n"
        f"SESSION_SECRET_KEY={session_secret}\n"
        "SESSION_COOKIE_SECURE=True\n"
        "\n"
        f"HOST={host}\n"
        f"PORT={port}\n"
    )


def write_env_file(target_path: Path, content: str, *, force: bool = False) -> None:
    """Schreibt `content` nach `target_path`.

    Verweigert das Überschreiben einer bereits bestehenden Konfiguration
    ohne `force=True` - eine bestehende `.env` enthält u. a. den aktiven
    `SESSION_SECRET_KEY`; ein unbeabsichtigtes Überschreiben würde alle
    laufenden Sessions ungültig machen und könnte eine bereits laufende
    Installation durcheinanderbringen.
    """
    if target_path.exists() and not force:
        raise FileExistsError(
            f"{target_path} existiert bereits - Setup-Assistent nicht erneut ohne "
            "ausdrückliche Bestätigung (force=True) ausgeführt, um eine bestehende "
            "Konfiguration (inkl. aktivem SESSION_SECRET_KEY) nicht zu überschreiben."
        )
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(content, encoding="utf-8")


def format_env_value(value: str | bool | list[str] | int | float) -> str:
    """Formatiert einen Python-Wert als `.env`-Zeilenwert, passend zu dem,
    was `pydantic-settings` beim Einlesen zurück erwartet (siehe
    app/config/settings.py):
    - `list[str]` -> JSON-Array (wie in .env.example: `INTAKE_WATCHED_FOLDERS=["..."]`),
      pydantic-settings dekodiert komplexe Feldtypen standardmäßig als JSON.
    - `bool` -> "true"/"false" (Kleinschreibung, wie im bestehenden .env.example).
    - alles andere (str/int/float) -> in doppelte Anführungszeichen gesetzter
      String, IMMER gequotet (nicht nur bei Leerzeichen) - robust gegen
      Sonderzeichen (z. B. "#" würde ohne Quotes als Kommentarbeginn
      fehlinterpretiert) in nutzergesteuerten Werten (Pfade, E-Mail-Zugangsdaten).

    WICHTIG (gefunden bei unabhängiger Review, 01.09.): eingebettete
    Zeilenumbrüche (`\n`/`\r`) MÜSSEN escaped werden, nicht nur `\\`/`"` -
    sonst kann ein Formularwert mit echtem Zeilenumbruch aus seiner
    eigenen gequoteten `KEY="..."`-Zeile "ausbrechen" und eine beliebige
    neue `ANDERER_KEY=...`-Zeile in die `.env` einschleusen (z. B.
    `SESSION_SECRET_KEY` überschreiben, was alle aktiven Sessions
    ungültig macht). `\n`/`\r` als literale Escape-Sequenz innerhalb der
    Anführungszeichen ist sowohl für python-dotenv als auch
    pydantic-settings (beide werten `\n` in gequoteten Werten als echten
    Zeilenumbruch aus) korrekt weiter lesbar - kein Informationsverlust,
    nur kein Ausbruch aus der Zeile mehr möglich.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return json.dumps(value)
    if isinstance(value, (int, float)):
        return str(value)
    escaped = (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )
    return f'"{escaped}"'


def update_env_values(target_path: Path, updates: dict[str, str | bool | list[str] | int | float | None]) -> None:
    """Ändert gezielt EINZELNE Schlüssel einer bereits bestehenden `.env`,
    ohne die übrigen Zeilen (inkl. Kommentare, Reihenfolge, SESSION_SECRET_KEY)
    anzutasten. `value=None` entfernt den Schlüssel ersatzlos (z. B. um ein
    zuvor gesetztes MAIL_PASSWORD wieder zu löschen).

    Existiert `target_path` noch nicht, wird eine neue, minimale Datei
    angelegt (Randfall für Tests/Entwicklungsbetrieb ohne vorherigen
    Setup-Assistenten-Lauf) - im Produktivbetrieb existiert die Datei
    immer bereits (Setup-Assistent lief vor jedem Dashboard-Zugriff).

    Schlüssel, die noch nicht in der Datei stehen, werden in einem neuen,
    klar gekennzeichneten Abschnitt am Dateiende ergänzt."""
    lines = target_path.read_text(encoding="utf-8").splitlines() if target_path.exists() else []

    remaining = dict(updates)
    result_lines: list[str] = []
    for line in lines:
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        key = match.group(1) if match else None
        if key is not None and key in remaining:
            value = remaining.pop(key)
            if value is not None:
                result_lines.append(f"{key}={format_env_value(value)}")
            # value is None -> Zeile ersatzlos entfernt (nicht angehaengt).
        else:
            result_lines.append(line)

    if remaining:
        if result_lines and result_lines[-1].strip():
            result_lines.append("")
        result_lines.append("# Nachträglich über das Dashboard ergänzt (app/web/settings_router.py).")
        for key, value in remaining.items():
            if value is not None:
                result_lines.append(f"{key}={format_env_value(value)}")

    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text("\n".join(result_lines) + "\n", encoding="utf-8")
