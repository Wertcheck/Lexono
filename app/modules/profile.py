"""Build-Profile (28.10.2026, Owner-Freigabe): welche Produktmodule ein Paket ueberhaupt enthaelt.

- ``kanzlei-pilot``: erstes Pilotpaket - nur das Basispaket "Lexono Kanzlei". Finance (und Steuern) werden NICHT
  ausgeliefert: Router nicht registriert (direkte URLs -> 404), keine Navigation, keine Templates/Code im Bundle,
  auch eine gueltige Lizenz schaltet sie nicht frei.
- ``full``: alle Module im Katalog; Freischaltung wie bisher ueber die signierte Lizenz.

Datenschicht bleibt in BEIDEN Profilen: Modelle (Tabellendefinitionen) und die Migrationen 040-042, weil bestehende
Datenbanken auf Revision 042 sonst nicht mehr starten ("alembic upgrade head" braucht alle Revisionsdateien).

Ermittlung des Profils:
- Installierte EXE (PyInstaller, ``sys.frozen``): NUR die beim Build ins Bundle geschriebene Datei
  ``lexono_build_profile.txt``. Fehlt sie oder ist sie unbekannt -> ``kanzlei-pilot`` (fail-closed). Eine
  Umgebungsvariable kann ein Paket NICHT nachtraeglich erweitern.
- Quellstand (Entwicklung/Tests): Umgebungsvariable ``LEXONO_BUILD_PROFILE``, Standard ``full``.
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

from app.modules.catalog import MODULE_FINANZEN, MODULE_KANZLEI, MODULE_STEUERN

PROFILE_PILOT = "kanzlei-pilot"
PROFILE_FULL = "full"
PROFILES: dict[str, frozenset[str]] = {
    PROFILE_PILOT: frozenset({MODULE_KANZLEI}),
    PROFILE_FULL: frozenset({MODULE_KANZLEI, MODULE_FINANZEN, MODULE_STEUERN}),
}
PROFILE_FILE_NAME = "lexono_build_profile.txt"
ENV_VAR = "LEXONO_BUILD_PROFILE"


def _frozen_profile() -> str:
    base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    try:
        value = (base / PROFILE_FILE_NAME).read_text(encoding="utf-8").strip()
    except OSError:
        return PROFILE_PILOT
    return value if value in PROFILES else PROFILE_PILOT


@lru_cache(maxsize=1)
def current_profile() -> str:
    if getattr(sys, "frozen", False):
        return _frozen_profile()
    value = os.environ.get(ENV_VAR, PROFILE_FULL).strip()
    if value not in PROFILES:
        raise RuntimeError(f"Unbekanntes Build-Profil {value!r} (erlaubt: {sorted(PROFILES)})")
    return value


def available_modules() -> frozenset[str]:
    return PROFILES[current_profile()]


def module_available(module: str) -> bool:
    return module in available_modules()
