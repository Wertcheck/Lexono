"""Update-Prüfung gegen eine statische Version-JSON (Schritt 3, 20.08.).

WICHTIG, ehrlich benannt: es gibt aktuell keine von uns betriebene
Update-Infrastruktur - `settings.update_manifest_url` ist standardmäßig
`None` (deaktiviert), siehe app/config/settings.py. Erst wenn eine
konkrete URL bewusst konfiguriert wird, findet überhaupt ein ausgehender
Netzwerkaufruf statt.

Bewusst NUR ein lesender Abruf einer statischen JSON-Datei
(`{"version": "0.2.0", "download_url": "https://..."}`) - kein zentraler
Backend-Dienst, kein automatischer Download, keine automatische
Installation (siehe CLAUDE.md: "keine automatische externe Kommunikation
ohne explizite Freigabe" - ein Update-Download/-Installation ist eine
ebenso bewusste, vom Anwalt/Admin manuell auszulösende Aktion wie ein
E-Mail-Versand). Das Ergebnis dient ausschließlich einem unaufdringlichen
Hinweis im Dashboard mit einem Link zur manuellen Installation.

Schlägt NIEMALS hart fehl - jeder Fehler (kein Netzwerk, Timeout,
ungültiges JSON, fehlendes "version"-Feld) führt zu einem stillen
`checked=False`-Ergebnis, nie zu einem Absturz des App-Starts (siehe
app/main.py: lifespan)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

#: Muss synchron mit `[project].version` in pyproject.toml gehalten werden -
#: kein automatischer Abgleich, da pyproject.toml zur Laufzeit einer
#: PyInstaller-.exe nicht mehr vorliegt (siehe windows/lexono.spec).
CURRENT_APP_VERSION = "0.1.0"


@dataclass(frozen=True)
class UpdateCheckResult:
    checked: bool
    update_available: bool
    latest_version: str | None = None
    download_url: str | None = None
    error: str | None = None


def _parse_version(value: str) -> tuple[int, ...]:
    """Toleranter Versions-Parser (nur Punkt-getrennte Ganzzahlen, z. B.
    "0.2.0") - kein Anspruch auf volle SemVer-Kompatibilität (Suffixe wie
    "-beta" werden ignoriert statt einen Fehler auszulösen), da dieses
    Projekt selbst nur einfache "x.y.z"-Versionsnummern verwendet."""
    parts: list[int] = []
    for piece in value.strip().split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def check_for_update(
    manifest_url: str | None,
    *,
    current_version: str = CURRENT_APP_VERSION,
    timeout_seconds: float = 3.0,
) -> UpdateCheckResult:
    if not manifest_url:
        return UpdateCheckResult(checked=False, update_available=False)

    try:
        response = httpx.get(manifest_url, timeout=timeout_seconds)
        response.raise_for_status()
        data = response.json()
        latest_version = str(data.get("version", "")).strip()
        if not latest_version:
            return UpdateCheckResult(
                checked=False,
                update_available=False,
                error="Antwort enthielt kein 'version'-Feld",
            )
        download_url = data.get("download_url")
        is_newer = _parse_version(latest_version) > _parse_version(current_version)
        return UpdateCheckResult(
            checked=True,
            update_available=is_newer,
            latest_version=latest_version,
            download_url=download_url if isinstance(download_url, str) else None,
        )
    except Exception as exc:  # noqa: BLE001 - Update-Pruefung darf App-Start nie gefaehrden
        logger.warning("Update-Prüfung fehlgeschlagen: %s", exc)
        return UpdateCheckResult(checked=False, update_available=False, error=str(exc))
