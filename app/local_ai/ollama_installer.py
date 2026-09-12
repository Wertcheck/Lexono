"""OllamaInstaller – automatisierte Erkennung/Installation von Ollama unter
Windows (§68).

Alle externen Effekte (Prozessaufrufe, HTTP-Downloads, Datei-Hashing) sind
als eigene, injizierbare Funktionen gehalten (Default = echte Implementierung)
- exakt dasselbe Dependency-Injection-Muster wie `RetryService` in
`app/documents/service.py` - damit Unit-Tests die komplette Installer-Logik
(Versionserkennung, Kompatibilitätsprüfung, Integritätsprüfung,
Fehlerfälle) OHNE echten Prozessstart/Netzwerkzugriff/1,5-GB-Download
prüfen können. Echte Windows-Installationstests laufen ausschließlich
manuell auf einer echten Maschine (siehe ARCHITECTURE.md §68, real bereits
einmal in §66 durchgeführt).

INTEGRITÄT (Vorgabe, wörtlich: "Wenn eine notwendige Integritätsinformation
... nicht verlässlich verfügbar ist: nicht erfinden, als offene
Sicherheitsgrenze dokumentieren"): GitHub liefert über die Releases-API
(`api.github.com/repos/ollama/ollama/releases/latest`) einen `digest`
(SHA256) je Release-Asset, EINSCHLIESSLICH `OllamaSetup.exe` - real
verifiziert (ARCHITECTURE.md §68: der in dieser Sitzung real heruntergeladene
Installer wurde gehasht, der Hash stimmte exakt mit dem von GitHub gemeldeten
Digest überein). Diese Prüfung schützt gegen Übertragungsfehler/
Man-in-the-Middle zwischen GitHub und diesem Rechner - NICHT gegen einen
kompromittierten Ollama-GitHub-Account selbst (kein von den Ollama-
Maintainern separat GPG-signierter Hash bekannt) - siehe ehrlich benannte
Sicherheitsgrenze im Abschlussbericht.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx

_GITHUB_RELEASE_API = "https://api.github.com/repos/ollama/ollama/releases/latest"
_OFFICIAL_INSTALLER_ASSET_NAME = "OllamaSetup.exe"
# Offizielle, stets aktuelle Weiterleitung (siehe Moduldocstring in
# app/ai_providers/ollama_provider.py fuer den bereits real verifizierten
# Redirect auf github.com/ollama/ollama/releases) - bewusst NICHT die
# GitHub-Asset-URL fest verdrahtet, da sich Versions-Tags in der URL
# aendern; ollama.com leitet immer auf die aktuell neueste Version weiter.
_OFFICIAL_INSTALLER_URL = "https://ollama.com/download/OllamaSetup.exe"


@dataclass(frozen=True)
class OllamaVersionPolicy:
    """Zentrale Versions-Kompatibilitaetsdefinition (Vorgabe: "darf nicht
    wild im Code verteilt sein"). `minimum_supported_version` ist bewusst
    niedrig gewaehlt (0.5.0) - das Ziel ist NICHT, eine funktionierende
    Installation wegen einer knapp aelteren Version abzulehnen ("keine
    blinden Upgrades"), sondern nur wirklich veraltete Installationen ohne
    die von diesem Projekt benoetigten API-Endpunkte (`/api/generate`,
    `/api/pull`, `/api/tags`, seit sehr fruehen Ollama-Versionen stabil)
    auszuschliessen."""

    minimum_supported_version: str = "0.5.0"
    installer_source: str = _OFFICIAL_INSTALLER_URL
    github_release_api: str = _GITHUB_RELEASE_API
    installer_asset_name: str = _OFFICIAL_INSTALLER_ASSET_NAME


DEFAULT_VERSION_POLICY = OllamaVersionPolicy()


@dataclass
class OllamaInstallResult:
    success: bool
    already_installed: bool
    installed_version: str | None
    # Stufe, an der der Vorgang endete (Erfolg oder Fehler) - fuer
    # nachvollziehbares, gestuftes Fehler-Reporting statt einer einzigen
    # generischen Fehlermeldung (Vorgabe: "an definierter Stelle
    # kontrolliert fehlschlagen").
    stage: str
    error: str | None = None


def _parse_version(value: str) -> tuple[int, ...]:
    """Toleranter Punkt-Versions-Parser - dieselbe, bewusst einfache
    Logik wie `app/updater/checker.py::_parse_version` (dort privat und
    auf App-Versionen bezogen, hier bewusst als eigene, kleine Kopie fuer
    Ollama-Versionen statt einer Modul-uebergreifenden Kopplung zweier
    fachlich unabhaengiger Versionsbegriffe)."""
    parts: list[int] = []
    for piece in value.strip().split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _default_run_command(args: list[str], *, timeout: float = 30.0) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)


def _default_fetch_release_info(url: str, *, timeout: float = 15.0) -> dict:
    response = httpx.get(
        url, timeout=timeout, headers={"Accept": "application/vnd.github+json"}
    )
    response.raise_for_status()
    return response.json()


def _default_download_file(url: str, dest: Path, *, timeout: float = 900.0) -> None:
    """Streaming-Download (Installer ist ~1,5 GB, siehe §66) - laedt nie
    die gesamte Datei in den Speicher."""
    with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as response:
        response.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                f.write(chunk)


def _default_compute_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _default_start_background_process(args: list[str]) -> None:
    """Startet einen Prozess im Hintergrund, OHNE auf sein Ende zu warten
    (`subprocess.run` wäre hier falsch - `ollama app.exe`/`ollama serve`
    laufen dauerhaft weiter, ein wartender Aufruf würde blockieren/immer
    per Timeout abbrechen). Fehler beim Start werden bewusst verschluckt -
    der Aufrufer (`ensure_running`) prüft ohnehin per Health-Check, ob der
    Start tatsächlich gewirkt hat."""
    try:
        subprocess.Popen(  # noqa: S603 - fester, nicht nutzergesteuerter Befehl
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except Exception:  # noqa: BLE001
        pass


def default_ollama_app_path() -> Path:
    """Installationsort des offiziellen Windows-Installers (real
    beobachtet, §66: `%LOCALAPPDATA%\\Programs\\Ollama\\ollama app.exe`,
    der vom Installer als Autostart-/Tray-Prozess registrierte
    Hauptprozess - NICHT dasselbe wie `ollama.exe`, die reine CLI)."""
    import os

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    return Path(local_app_data) / "Programs" / "Ollama" / "ollama app.exe"


def default_ollama_cli_path() -> Path:
    """Installationsort der Ollama-CLI (`ollama.exe`, NICHT die Tray-
    Anwendung `ollama app.exe` - siehe `default_ollama_app_path`), am
    selben Ort wie oben. Real gefundener Fehler (Installer-Reality-Check,
    Referenzmaschine i5-1145G7): der offizielle Ollama-Installer traegt
    `%LOCALAPPDATA%\\Programs\\Ollama` zwar in die PATH-Registry ein, ein
    bereits LAUFENDER Prozess (z. B. `Lexono.exe`, gestartet ueber
    Start.vbs beim Login) erbt diese Aenderung nie - Windows aktualisiert
    `os.environ`/den PATH eines laufenden Prozesses nicht rueckwirkend. Ein
    reiner `subprocess.run(["ollama", "--version"])`-PATH-Aufruf schlaegt
    deshalb sowohl VOR als auch (real beobachtet) DIREKT NACH einer
    frischen automatischen Installation fehl, obwohl `ollama.exe`
    nachweislich am erwarteten, vom eigenen Installer verwendeten Ort
    liegt - `detect_installed_version` faellt deshalb auf diesen Pfad
    zurueck, statt eine funktionierende Installation faelschlich als
    "nicht gefunden" zu melden."""
    import os

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    return Path(local_app_data) / "Programs" / "Ollama" / "ollama.exe"


class OllamaInstaller:
    def __init__(
        self,
        *,
        version_policy: OllamaVersionPolicy | None = None,
        run_command: Callable[..., subprocess.CompletedProcess] = _default_run_command,
        fetch_release_info: Callable[..., dict] = _default_fetch_release_info,
        download_file: Callable[..., None] = _default_download_file,
        compute_sha256: Callable[[Path], str] = _default_compute_sha256,
        start_background_process: Callable[[list[str]], None] = _default_start_background_process,
    ) -> None:
        self.version_policy = version_policy or DEFAULT_VERSION_POLICY
        self._run_command = run_command
        self._fetch_release_info = fetch_release_info
        self._download_file = download_file
        self._compute_sha256 = compute_sha256
        self._start_background_process = start_background_process

    def detect_installed_version(self) -> str | None:
        """`ollama --version` - `None`, wenn Ollama nicht installiert
        bzw. nicht auf PATH ist ODER der Aufruf aus irgendeinem Grund
        fehlschlaegt (kein Absturz, siehe HardwareDetector-Prinzip).

        Faellt bei einem PATH-Fehlschlag auf den bekannten Installationsort
        (`default_ollama_cli_path`) zurueck, BEVOR "nicht installiert"
        gefolgert wird - siehe dortige Begruendung: ein laufender Prozess
        sieht eine waehrend seiner Laufzeit aktualisierte PATH-Registry
        nicht, eine tatsaechlich vorhandene Installation darf dadurch nicht
        faelschlich als fehlend gelten."""
        result = self._try_version_command(["ollama", "--version"])
        if result is None:
            cli_path = default_ollama_cli_path()
            if cli_path.is_file():
                result = self._try_version_command([str(cli_path), "--version"])
        if result is None or result.returncode != 0:
            return None
        match = re.search(r"(\d+\.\d+\.\d+)", result.stdout or "")
        return match.group(1) if match else None

    def _try_version_command(self, args: list[str]) -> subprocess.CompletedProcess | None:
        try:
            return self._run_command(args, timeout=10.0)
        except Exception:  # noqa: BLE001 - jeder Fehler bedeutet "so nicht erreichbar"
            return None

    def is_version_compatible(self, version: str) -> bool:
        return _parse_version(version) >= _parse_version(self.version_policy.minimum_supported_version)

    def _resolve_expected_digest(self) -> str | None:
        """Fragt die GitHub-Releases-API nach dem SHA256-Digest des
        Installer-Assets. `None`, wenn die Information nicht verlaesslich
        verfuegbar ist (API nicht erreichbar, Asset/Digest-Feld fehlt) -
        WIRD DANN NICHT ERFUNDEN (Vorgabe woertlich), der Aufrufer
        (`ensure_installed`) entscheidet, ob ohne Pruefsumme fortgefahren
        wird."""
        try:
            data = self._fetch_release_info(self.version_policy.github_release_api)
        except Exception:  # noqa: BLE001
            return None
        for asset in data.get("assets", []):
            if not isinstance(asset, dict):
                continue
            if asset.get("name") == self.version_policy.installer_asset_name:
                digest = asset.get("digest")
                if isinstance(digest, str) and digest.startswith("sha256:"):
                    return digest.removeprefix("sha256:")
        return None

    def ensure_installed(self, *, download_dir: Path) -> OllamaInstallResult:
        """Kernablauf (§68 Punkt "OLLAMA-INSTALLATION"):
        1. bereits installiert + kompatibel? -> wiederverwenden, kein
           blindes Upgrade.
        2. bereits installiert, aber inkompatibel? -> kontrollierter
           Fehler, KEIN automatisches Upgrade (Vorgabe woertlich: "keine
           blinden Upgrades").
        3. nicht installiert -> offiziellen Installer laden, Integritaet
           pruefen (falls verfuegbar), unbeaufsichtigt installieren,
           Installation verifizieren."""
        existing_version = self.detect_installed_version()
        if existing_version is not None:
            if self.is_version_compatible(existing_version):
                return OllamaInstallResult(
                    success=True,
                    already_installed=True,
                    installed_version=existing_version,
                    stage="reused_existing_installation",
                )
            return OllamaInstallResult(
                success=False,
                already_installed=True,
                installed_version=existing_version,
                stage="incompatible_existing_version",
                error=(
                    f"Installierte Ollama-Version {existing_version} liegt unter der "
                    f"Mindestanforderung {self.version_policy.minimum_supported_version} - "
                    "kein automatisches Upgrade (keine blinden Upgrades, siehe Vorgabe)."
                ),
            )

        download_dir.mkdir(parents=True, exist_ok=True)
        installer_path = download_dir / self.version_policy.installer_asset_name

        try:
            self._download_file(self.version_policy.installer_source, installer_path)
        except Exception as exc:  # noqa: BLE001
            return OllamaInstallResult(
                success=False,
                already_installed=False,
                installed_version=None,
                stage="download_failed",
                error=f"Installer-Download fehlgeschlagen: {type(exc).__name__}: {exc}",
            )

        expected_digest = self._resolve_expected_digest()
        if expected_digest is not None:
            actual_digest = self._compute_sha256(installer_path)
            if actual_digest.lower() != expected_digest.lower():
                return OllamaInstallResult(
                    success=False,
                    already_installed=False,
                    installed_version=None,
                    stage="integrity_check_failed",
                    error=(
                        "SHA256-Prüfsumme des heruntergeladenen Installers stimmt nicht "
                        "mit dem von GitHub gemeldeten Digest überein - Installation "
                        "abgebrochen, KEIN Ausführen einer nicht verifizierten Datei."
                    ),
                )

        try:
            result = self._run_command(
                [str(installer_path), "/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES"],
                timeout=600.0,
            )
        except Exception as exc:  # noqa: BLE001
            return OllamaInstallResult(
                success=False,
                already_installed=False,
                installed_version=None,
                stage="install_failed",
                error=f"Installationsprozess fehlgeschlagen: {type(exc).__name__}: {exc}",
            )
        if result.returncode != 0:
            return OllamaInstallResult(
                success=False,
                already_installed=False,
                installed_version=None,
                stage="install_failed",
                error=f"Installer meldete Exit-Code {result.returncode}",
            )

        installed_version = self.detect_installed_version()
        if installed_version is None:
            return OllamaInstallResult(
                success=False,
                already_installed=False,
                installed_version=None,
                stage="post_install_verification_failed",
                error=(
                    "Installer meldete Erfolg, aber 'ollama --version' ist danach "
                    "nicht auffindbar - Installation NICHT als erfolgreich markiert."
                ),
            )

        return OllamaInstallResult(
            success=True,
            already_installed=False,
            installed_version=installed_version,
            stage="installed",
        )

    def ensure_running(
        self,
        *,
        is_reachable: Callable[[], bool],
        app_path: Path | None = None,
        wait_seconds: float = 0.0,
        max_attempts: int = 5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> bool:
        """Best-effort: falls Ollama installiert, aber (z. B. nach einem
        Systemneustart) nicht erreichbar ist, wird versucht, die lokale
        Runtime zu starten (`ollama app.exe`, real beobachtet als der vom
        offiziellen Installer registrierte Autostart-/Tray-Prozess, siehe
        §66). `is_reachable` wird injiziert (typischerweise
        `OllamaLocalLLMProvider.check_health().reachable`) statt hier eine
        zweite HTTP-Implementierung zu bauen. Gibt zurück, ob Ollama
        DANACH tatsächlich erreichbar ist - kein reiner "Startbefehl
        abgesetzt"-Erfolg. Pollt kurz nach (`max_attempts` x `wait_seconds`),
        da Ollama nach dem Start real einen Moment braucht, bis der
        API-Port gebunden ist - `wait_seconds=0.0` (Testdefault) macht die
        Wartezeit in Unit-Tests injizierbar/entfernbar."""
        if is_reachable():
            return True
        target = app_path or default_ollama_app_path()
        self._start_background_process([str(target)])
        for _ in range(max_attempts):
            if is_reachable():
                return True
            if wait_seconds > 0:
                sleep(wait_seconds)
        return is_reachable()
