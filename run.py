"""Windows-Entry-Point für die gebündelte Anwendung (Prompt 36/37, Prompt 46).

Dies ist die einzige Datei, die PyInstaller bündelt (siehe
windows/lexono.spec) - ein dünner Dispatcher, keine Fachlogik. Bietet
fünf Subkommandos:

    Lexono.exe serve          (Standard, auch ohne Argument) - startet
                                   den Webserver UND öffnet ein natives
                                   Fenster (Edge-WebView2, siehe Prompt 46),
                                   das auf das Dashboard zeigt - kein
                                   Browser-Tab, keine Adressleiste. Führt
                                   vorher automatisch ausstehende
                                   Datenbankmigrationen aus ("bei jedem
                                   Update", siehe HANDOFF-Doku) und stößt
                                   bei fehlender Konfiguration automatisch
                                   den Setup-Assistenten an. Prüft VORHER
                                   per Single-Instance-Mutex (Schritt 3,
                                   `_acquire_single_instance_lock`), ob
                                   bereits eine Instanz für dieses
                                   Benutzerkonto läuft, und bricht mit einer
                                   klaren Fehlermeldung ab statt zwei
                                   Prozesse gleichzeitig auf dieselbe
                                   SQLite-Datei zugreifen zu lassen.
        --no-window                - nur der Server, kein Fenster (bisheriges
                                   Verhalten vor Prompt 46, weiterhin nützlich
                                   für Entwickler/Debugging/Kopfstationen).
    Lexono.exe setup          - Ersteinrichtung: Datenverzeichnis,
                                   `.env` (inkl. generiertem
                                   SESSION_SECRET_KEY), Migration, Admin,
                                   optional (Standardvorschlag: ja) lokale
                                   KI (siehe local-ai-setup unten) - Phase 3,
                                   §71: "Local AI ist jetzt Pflicht" der
                                   Zielarchitektur, ein Fehlschlag/Ablehnen
                                   dieses Schritts verhindert aber nicht die
                                   Installation/Nutzung der Anwendung.
    Lexono.exe migrate        - führt nur `alembic upgrade head` aus.
    Lexono.exe create-admin   - ruft scripts/create_admin.py auf
                                   (liest ADMIN_EMAIL/ADMIN_INITIAL_PASSWORD
                                   aus der Prozessumgebung).
    Lexono.exe reset-admin-password
                                   - ruft scripts/reset_admin_password.py auf
                                   (liest ADMIN_EMAIL/RESET_PASSWORD aus der
                                   Prozessumgebung) - Recovery-Pfad, falls das
                                   initiale, nur einmalig angezeigte Admin-
                                   Passwort verloren ging.
    Lexono.exe local-ai-setup - erkennt Hardware, empfiehlt/installiert
                                   ein passendes lokales Ollama-Modell und
                                   aktiviert `LOCAL_AI_ENABLED` in `.env`
                                   (Phase 3, §71 - siehe
                                   app/local_ai/setup_orchestrator.py).
                                   Eigenständig jederzeit erneut aufrufbar,
                                   nicht nur während `setup`.
    Lexono.exe restore        - stellt Datenbank + Dokumentenspeicher aus
                                   einem Backup-Archiv wieder her (Schritt 3,
                                   siehe app/backup/restore_service.py). Die
                                   Anwendung MUSS dafür gestoppt sein - bewusst
                                   KEINE Restore-Aktion im laufenden Dashboard.
                                   `--archive <pfad.zip>` [--yes].

WICHTIG zu Prompt 46 (natives Fenster): der bestehende Web-Stack (FastAPI,
Jinja2, HTMX, app/main.py, app/web/*) wird NICHT verändert - der Server
läuft unverändert wie bisher, nur zusätzlich in einem Hintergrund-Thread
statt blockierend im Hauptthread, weil `webview.start()` selbst den
Hauptthread braucht (Standard-Einschränkung von GUI-Event-Loops unter
Windows). Das Fenster zeigt schlicht die bestehende Login-Seite im Browser-
Fenster-Gewand an - siehe ARCHITECTURE.md für die ausführliche Begründung,
warum dieser Ansatz (natives Fenster UM den Stack) gewählt wurde statt einer
Neuentwicklung.

WICHTIG zur Prozessarchitektur: `setup` ruft `migrate`/`create-admin` NICHT
direkt als Python-Funktionsaufruf im selben Prozess auf, sondern startet
sich selbst als NEUEN Subprozess (`_self_command`). Grund: `app.config.
get_settings()` ist `@lru_cache`d und `app/db/session.py` erzeugt die
SQLAlchemy-Engine bereits beim Modul-Import - beides liest die Konfiguration
also spätestens beim ERSTEN Import im laufenden Prozess. Da `setup` selbst
die `.env`-Datei erst währenddessen schreibt, muss jeder nachfolgende
Schritt in einem GARANTIERT frischen Prozess laufen, der die neue `.env`
von Anfang an sieht - alles andere wäre eine fragile Abhängigkeit von der
Importreihenfolge. Siehe auch app/setup/wizard.py (dort ausführlicher
begründet, dort injiziert statt hier fest verdrahtet - macht die eigentliche
Ablauflogik ohne Subprozesse testbar).

Vor JEDEM Subkommando wechselt dieser Entry-Point in das persistente
Datenverzeichnis (siehe app/setup/paths.py), unabhängig davon, wie/von wo
die .exe gestartet wurde (Startmenü-Verknüpfung mit gesetztem Arbeits-
verzeichnis, Doppelklick im Installationsordner, Windows-Aufgabenplanung).
Das ist der einzige Mechanismus, der sicherstellt, dass relative Pfade in
den Settings (DATABASE_URL, INTAKE_STORAGE_DIR, ...) IMMER im
Datenverzeichnis landen, nie versehentlich im schreibgeschützten
Installationsordner.
"""

from __future__ import annotations

import argparse
import getpass
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

#: WebView2-"Client"-GUIDs (Runtime/Beta/Dev/Canary) - dieselben, die
#: pywebview intern selbst prüft (siehe webview/platforms/winforms.py,
#: `_is_chromium()`), hier unabhängig reimplementiert (siehe
#: `_is_webview2_runtime_available` weiter unten für die Begründung, warum
#: wir NICHT einfach pywebview automatisch entscheiden lassen).
_WEBVIEW2_CLIENT_GUIDS = (
    "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",  # WebView2 Runtime (stabil)
    "{2CD8A007-E189-409D-A2C8-9AF4EF3C72AA}",  # WebView2 Beta
    "{0D50BFEC-CD6A-4F9A-964C-C7416E3ACB10}",  # WebView2 Dev
    "{65C35B14-6C1D-4122-AC46-7148CC9D6497}",  # WebView2 Canary
)
_WEBVIEW2_DOWNLOAD_URL = "https://developer.microsoft.com/en-us/microsoft-edge/webview2/"

#: Minimalgroesse des nativen Fensters (siehe `webview.create_window(...,
#: min_size=...)` in `_serve_with_window`) - verhindert ein auf (fast) 0
#: verkleinertes, nicht mehr bedienbares Fenster. Werte grosszuegig unter
#: der Startgroesse (1400x900), aber hoch genug, dass Sidebar+Chat noch
#: sinnvoll nutzbar bleiben. Seit der Umstellung auf natives Fenster-Chrome
#: (Rueckbau von Masterprompt V2 Task #61s frameless-Loesung - echtes
#: Windows-Maximieren/Snap/Alt+Tab/Taskbar-Verhalten war mit einem
#: frameless-Fenster strukturell nicht erreichbar) durchgesetzt vom
#: Betriebssystem selbst (WM_GETMINMAXINFO), nicht mehr per Hand in
#: `_NativeApi` nachgebildet.
_MIN_WINDOW_WIDTH = 900
_MIN_WINDOW_HEIGHT = 600


def _bundle_base_dir() -> Path:
    """Verzeichnis mit `alembic.ini`/`migrations/` - im Dev-Betrieb das
    Repository-Root (diese Datei liegt dort), in der gebündelten .exe das
    von PyInstaller bereitgestellte Bundle-Verzeichnis (siehe
    windows/lexono.spec, `datas`-Eintrag für beide)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent


def _self_command(*extra_args: str) -> list[str]:
    """Kommandozeile, um DIESES Programm (dev: `python run.py ...`,
    gebündelt: `Lexono.exe ...`) als neuen Subprozess zu starten."""
    if getattr(sys, "frozen", False):
        return [sys.executable, *extra_args]
    return [sys.executable, str(Path(__file__).resolve()), *extra_args]


def cmd_migrate() -> int:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(_bundle_base_dir() / "alembic.ini"))
    command.upgrade(cfg, "head")
    return 0


def cmd_create_admin() -> int:
    from scripts.create_admin import main as create_admin_main

    return create_admin_main()


def cmd_reset_admin_password() -> int:
    """Recovery-Pfad fuer "Admin existiert, Passwort ist unbekannt" (real
    aufgetretener Fall: das einmalig beim Setup angezeigte Zufallspasswort
    ging verloren, `.env` existiert bereits, First Run wird deshalb korrekt
    NICHT erneut ausgeloest). `scripts/reset_admin_password.py` selbst
    existierte bereits, war aber - anders als `create-admin`/`restore` -
    weder hier noch in windows/lexono.spec (hiddenimports) angebunden
    und dadurch aus der installierten .exe heraus nicht erreichbar. Liest
    wie `create-admin` ADMIN_EMAIL/RESET_PASSWORD aus der Prozessumgebung."""
    from scripts.reset_admin_password import main as reset_admin_password_main

    return reset_admin_password_main()


def cmd_restore(*, archive: str, yes: bool) -> int:
    from scripts.restore_backup import main as restore_backup_main

    argv = ["--archive", archive]
    if yes:
        argv.append("--yes")
    return restore_backup_main(argv)


def _http_check(url: str) -> bool:
    """Echter HTTP-GET-Bereitschaftscheck (Standardimplementierung von
    `_wait_for_server_ready`) - eigenständige Funktion, damit Tests sie
    durch einen Fake ersetzen können, ohne einen echten Server zu
    brauchen."""
    import urllib.request

    with urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310 - feste lokale URL
        return response.status == 200


def _wait_for_server_ready(
    url: str,
    *,
    timeout: float = 15.0,
    interval: float = 0.3,
    check: Callable[[str], bool] = _http_check,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> None:
    """Wartet, bis `check(url)` `True` liefert, oder wirft `TimeoutError`.

    `check`/`sleep`/`now` sind bewusst injizierbar (Standard: echter HTTP-
    Aufruf/echtes Warten/echte Uhr) - macht sowohl den Erfolgs- als auch den
    Timeout-Pfad ohne echten Netzwerk-Server und ohne echtes Warten testbar
    (siehe tests/test_run_entrypoint.py).
    """
    deadline = now() + timeout
    last_error: Exception | None = None
    while now() < deadline:
        try:
            if check(url):
                return
        except Exception as exc:  # noqa: BLE001 - waehrend des Serverstarts erwartete Verbindungsfehler
            last_error = exc
        sleep(interval)
    detail = f" ({last_error})" if last_error is not None else ""
    raise TimeoutError(
        f"Server unter {url} hat innerhalb von {timeout:.0f} Sekunden nicht "
        f"geantwortet{detail}."
    )


def _is_webview2_runtime_available() -> bool:
    """Prüft per Registry, ob die Microsoft-Edge-WebView2-Runtime installiert
    ist - dieselbe Erkennung (Client-GUID unter
    ...\\Microsoft\\EdgeUpdate\\Clients\\...), die auch pywebview intern
    verwendet (siehe webview/platforms/winforms.py, `_is_chromium()`).

    UNABHÄNGIG reimplementiert statt pywebview einfach entscheiden zu lassen:
    fehlt WebView2, fällt pywebview NICHT mit einem Fehler auf, sondern
    still auf die veraltete Internet-Explorer-Engine (MSHTML) zurück (siehe
    dieselbe Quelldatei) - das würde das moderne HTMX-Dashboard nicht
    sichtbar zum Absturz bringen, aber kaputt/unbenutzbar aussehen lassen.
    Diese Prüfung VOR dem Fensteraufbau macht daraus einen klaren Fehler
    statt einer stillen, schwer diagnostizierbaren Verschlechterung.

    WICHTIGER FUND (beim echten End-to-End-Test auf einer 64-Bit-Windows-
    Maschine mit tatsächlich installiertem WebView2 entdeckt): der
    WebView2-Runtime-Installer ist selbst ein 32-Bit-Programm und schreibt
    seinen `HKEY_LOCAL_MACHINE`-Registrierungseintrag deshalb NICHT unter
    den "nativen" 64-Bit-Pfad, sondern unter den von Windows automatisch
    umgeleiteten `WOW6432Node`-Zweig - ein reines `winreg.OpenKey(HKLM,
    "SOFTWARE\\Microsoft\\...")` findet ihn auf einer 64-Bit-Maschine daher
    NIE, obwohl die Runtime installiert ist (erste Version dieser Funktion
    hatte genau diesen Fehler - fälschlich "nicht gefunden" trotz
    installierter Runtime). `HKEY_CURRENT_USER`-Einträge sind von dieser
    Umleitung nicht betroffen. Nachgebildet nach demselben Muster, das
    pywebview selbst in `_is_chromium()` verwendet (`machine() == 'x86' or
    key_type == 'HKEY_CURRENT_USER'` entscheidet zwischen beiden Pfaden).
    """
    if os.name != "nt":
        return True  # Prüfung ergibt nur unter Windows Sinn (Zielplattform)

    import platform
    import winreg

    is_32bit_machine = platform.machine() == "x86"

    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        # HKCU ist nie von der WOW6432Node-Umleitung betroffen; HKLM auf
        # einer 64-Bit-Maschine schon (siehe Docstring oben).
        use_wow6432node = hive == winreg.HKEY_LOCAL_MACHINE and not is_32bit_machine
        subpath = "WOW6432Node\\Microsoft" if use_wow6432node else "Microsoft"
        for guid in _WEBVIEW2_CLIENT_GUIDS:
            try:
                key = winreg.OpenKey(hive, rf"SOFTWARE\{subpath}\EdgeUpdate\Clients\{guid}")
                winreg.QueryValueEx(key, "pv")
                return True
            except OSError:
                continue
    return False


#: Bewusst OHNE "Global\"-Präfix: ein "Global\"-Mutex würde ALLE Windows-
#: Benutzerkonten auf derselben Maschine gegenseitig blockieren - passt
#: nicht zum Installationsmodell (%LocalAppData%, eine Installation pro
#: Benutzerkonto, siehe windows/installer.iss). Ohne Präfix ist der Mutex
#: sitzungslokal (effektiv: pro angemeldetem Benutzer) - genau eine
#: laufende Instanz PRO NUTZER, nicht pro Maschine.
_SINGLE_INSTANCE_MUTEX_NAME = "Lexono_SingleInstance_Mutex"
_ERROR_ALREADY_EXISTS = 183


def _win32_create_mutex(name: str) -> tuple[int, int]:
    """Echte Windows-API-Implementierung (`CreateMutexW` + `GetLastError`) -
    Standardimplementierung für `_acquire_single_instance_lock`.
    `use_last_error=True` liefert einen thread-lokalen, von anderen
    ctypes-Aufrufen unabhängigen Fehlercode (empfohlenes ctypes-Idiom für
    GetLastError-basierte Win32-APIs, statt des global geteilten
    `ctypes.windll`-Zustands)."""
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.CreateMutexW(None, False, name)
    return handle, ctypes.get_last_error()


def _win32_close_handle(handle: int) -> None:
    import ctypes

    ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(handle)


def _acquire_single_instance_lock(
    *,
    is_windows: bool | None = None,
    create_mutex: Callable[[str], tuple[int, int]] = _win32_create_mutex,
    close_handle: Callable[[int], None] = _win32_close_handle,
) -> int | None:
    """Verhindert, dass die Anwendung mehrfach parallel läuft (z. B. durch
    doppeltes Anklicken der Startmenü-/Desktop-Verknüpfung, während bereits
    eine Instanz läuft) - zwei parallele uvicorn-Server auf demselben Port
    UND derselben SQLite-Datei wären ein Datenintegritätsrisiko (siehe
    app/db/session.py: EINE Engine pro Prozess, keine Vorkehrung für
    mehrere gleichzeitig schreibende Prozesse).

    Nutzt einen benannten Windows-Mutex (`CreateMutexW` +
    `GetLastError() == ERROR_ALREADY_EXISTS`) statt z. B. eines Lock-Files -
    ein Mutex wird vom Betriebssystem GARANTIERT freigegeben, sobald der
    besitzende Prozess endet (auch bei einem harten Absturz). Ein Lock-File
    könnte nach einem Absturz verwaist zurückbleiben und jeden künftigen
    Start fälschlich blockieren - genau die Art von Fehler, die dieses
    Muster vermeiden soll.

    Gibt das offene Mutex-Handle zurück (muss bis zum Prozessende offen
    bleiben, siehe `_release_single_instance_lock`), oder `None`, wenn
    bereits eine andere Instanz läuft. `is_windows`/`create_mutex` sind
    bewusst injizierbar (identisches Muster zu `resolve_data_dir`/
    `_wait_for_server_ready`) - macht beide Zweige testbar, ohne einen
    echten Windows-Mutex anzulegen. Auf Nicht-Windows-Plattformen
    (Entwicklung/Tests) immer "kein Konflikt" (`-1` als Platzhalter-Handle),
    da dort ohnehin nie die gepackte .exe läuft."""
    if is_windows is None:
        is_windows = os.name == "nt"
    if not is_windows:
        return -1

    handle, last_error = create_mutex(_SINGLE_INSTANCE_MUTEX_NAME)
    if not handle:
        raise OSError(f"Mutex konnte nicht erstellt werden (Fehlercode {last_error})")
    if last_error == _ERROR_ALREADY_EXISTS:
        close_handle(handle)
        return None
    return handle


def _release_single_instance_lock(
    handle: int, *, close_handle: Callable[[int], None] = _win32_close_handle
) -> None:
    if handle == -1:
        return  # Platzhalter (Nicht-Windows) - nichts freizugeben
    close_handle(handle)


#: COLORREF-Werte (0x00BBGGRR, umgekehrte Byte-Reihenfolge gegenueber
#: RGB-Hex) fuer DWMWA_CAPTION_COLOR/DWMWA_TEXT_COLOR - exakt dieselben
#: Marken-Farbwerte wie app/web/static/css/app.css: --paper-100 (#F8FAFC)
#: und --seal-green/CI-Farbcode (#101828).
_TITLE_BAR_CAPTION_COLORREF = 0x00FCFAF8  # #F8FAFC
_TITLE_BAR_TEXT_COLORREF = 0x00281810  # #101828


def _apply_light_title_bar(window: object | None = None) -> None:
    """Erzwingt eine HELLE native Windows-Titelleiste fuer das WebView2-
    Fenster (20.08., "kritischer Design-Fix") - unabhaengig vom
    System-Dark-Mode.

    Hintergrund: pywebview spiegelt auf Windows automatisch den
    System-Theme-Modus auf die Titelleiste (siehe .venv/Lib/site-packages/
    webview/platforms/winforms.py: update_title_bar_theme/is_dark_theme,
    per DWMWA_USE_IMMERSIVE_DARK_MODE ueber die Windows-DWM-API) - bei
    einem Windows-Rechner im systemweiten Dunkelmodus wurde die Titelleiste
    dadurch SCHWARZ, ein deutlicher Bruch mit dem durchgehend hellen
    Apple-Layout der eigentlichen Anwendung. Dieselbe DWM-API (`DwmSetWindow
    Attribute`, siehe genau dieselbe Technik im o. g. pywebview-Modul) wird
    hier ERNEUT aufgerufen, NACHDEM pywebview seine eigene (system-
    theme-abhaengige) Einstellung bereits gesetzt hat (`window.events.shown`
    feuert nach der internen `update_title_bar_theme()`-Zuweisung) - das
    ueberschreibt pywebviews Wahl bewusst und dauerhaft mit "hell".

    `DWMWA_CAPTION_COLOR`/`DWMWA_TEXT_COLOR` (Attribute 35/36) setzen
    zusaetzlich die exakte Marken-Off-White-Farbe als Titelleisten-
    Hintergrund - nur ab Windows 11 22H2 unterstuetzt; auf aelteren
    Windows-Versionen schlaegt der Aufruf einfach folgenlos fehl (HRESULT
    ungleich S_OK, kein Python-Fehler), `DWMWA_USE_IMMERSIVE_DARK_MODE`
    (Attribut 20, seit Windows 10 2004) greift als Fallback trotzdem -
    zumindest keine schwarze Titelleiste mehr, selbst ohne exakte Farbe.

    Darf unter KEINEN Umstaenden den App-Start verhindern (rein kosmetisch)
    - jeder Fehler (z. B. sehr alte Windows-Version, dwmapi fehlt) wird
    daher verschluckt, analog zu allen anderen "darf nie hart fehlschlagen"
    Diagnose-/Komfortfunktionen in diesem Projekt (siehe z. B.
    app/updater/checker.py)."""
    try:
        import ctypes

        hwnd = window.native.Handle.ToInt32()  # type: ignore[union-attr]
        dwmapi = ctypes.windll.dwmapi  # type: ignore[attr-defined]

        def _set(attribute: int, value: int) -> None:
            dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(ctypes.c_int(value)), 4)

        _set(20, 0)  # DWMWA_USE_IMMERSIVE_DARK_MODE = aus -> helle Titelleiste
        _set(35, _TITLE_BAR_CAPTION_COLORREF)  # DWMWA_CAPTION_COLOR
        _set(36, _TITLE_BAR_TEXT_COLORREF)  # DWMWA_TEXT_COLOR
    except Exception:  # noqa: BLE001 - rein kosmetisch, darf den Start nie gefaehrden
        pass


#: DWMWA_WINDOW_CORNER_PREFERENCE (Windows 11, Build 22000+) - macht die
#: aeusseren Fensterecken des nativen Fenster-Chrome (siehe
#: _serve_with_window: kein `frameless` mehr) modern abgerundet, angelehnt
#: an aktuelle native Microsoft-Apps (z. B. Outlook), OHNE das Fenster
#: selbst zu einem Custom-/Frameless-Fenster zu machen - Maximieren/Snap/
#: Alt+Tab/Taskleiste bleiben dabei vollstaendig unveraendert vom
#: Betriebssystem bereitgestellt, genau wie bei jedem anderen normalen
#: Windows-Fenster.
_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWCP_ROUND = 2


def _apply_rounded_corners(window: object | None = None) -> None:
    """Aktiviert native abgerundete Fensterecken (Windows 11) fuer das
    Lexono-Hauptfenster. Nutzt dieselbe DWM-API/dasselbe Aufrufmuster wie
    `_apply_light_title_bar` (eigener try/except-Block, gleiche
    hwnd-Ermittlung ueber `window.native.Handle`) - bewusst NICHT in
    dieselbe Funktion zusammengelegt, damit ein Fehler bei einer der beiden
    rein kosmetischen Einstellungen niemals die andere verhindern kann.

    Auf Windows-Versionen vor Build 22000 (kein Windows 11) liefert
    `DwmSetWindowAttribute` fuer dieses Attribut lediglich einen
    Fehler-HRESULT zurueck (kein Python-Fehler, kein Effekt) - eckige
    Fensterecken auf aelterem Windows sind dort das normale, erwartete
    Verhalten, kein Bug."""
    try:
        import ctypes

        hwnd = window.native.Handle.ToInt32()  # type: ignore[union-attr]
        dwmapi = ctypes.windll.dwmapi  # type: ignore[attr-defined]
        dwmapi.DwmSetWindowAttribute(
            hwnd,
            _DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(ctypes.c_int(_DWMWCP_ROUND)),
            4,
        )
    except Exception:  # noqa: BLE001 - rein kosmetisch, darf den Start nie gefaehrden
        pass


#: GWL_EXSTYLE/WS_EX_DLGMODALFRAME/SetWindowPos-Flags - siehe
#: _remove_title_bar_icon fuer die Begruendung des ROOT-CAUSE-FIX (03.10.,
#: Owner-Direktive "WINDOWS-TASKLEISTEN-ICON, FENSTERIDENTITAET UND
#: DESKTOP-VERKNUEPFUNG").
_GWL_EXSTYLE = -20
_WS_EX_DLGMODALFRAME = 0x00000001
_SWP_NOMOVE = 0x0002
_SWP_NOSIZE = 0x0001
_SWP_NOZORDER = 0x0004
_SWP_FRAMECHANGED = 0x0020


def _remove_title_bar_icon(window: object | None = None) -> None:
    """Entfernt das App-Icon aus der nativen Titelleiste (Nutzerauftrag,
    13.09.: "das kleine Icon oben links muss weg") - bewusst NUR das
    Titelleisten-Icon, NICHT das Icon der .exe selbst (Taskleiste/
    Datei-Explorer/Alt+Tab zeigen weiterhin das echte Lexono-Icon, das
    kommt direkt aus der .exe-Ressource, nicht von hier).

    ROOT-CAUSE-FIX (03.10., Owner-Direktive "WINDOWS-TASKLEISTEN-ICON,
    FENSTERIDENTITAET UND DESKTOP-VERKNUEPFUNG"): die bisherige, am 13.09.
    gefundene Loesung (ein VOLLSTAENDIG TRANSPARENTES Icon-Handle per
    WM_SETICON fuer BEIDE Slots, ICON_SMALL *und* ICON_BIG) wurde damals
    nur gegen die TITELLEISTE verifiziert ("Titelleiste zeigt danach nur
    noch den Text 'Lexono'") - NICHT gegen die Taskleiste. Per echter
    Live-Fenster-Diagnose (EnumWindows/WM_GETICON gegen die tatsaechlich
    laufende, installierte Instanz) jetzt nachgewiesen: WM_SETICON setzt
    das ECHTE Fenster-Icon (nicht nur einen Titelleisten-Anzeigewert) -
    ICON_BIG ist exakt der Wert, den die Windows-Taskleiste fuer die
    Schaltflaeche des laufenden Fensters liest. Das transparente Handle
    ersetzte dadurch unbeabsichtigt NICHT NUR das Titelleisten-Icon,
    sondern das gesamte Taskleisten-/Alt+Tab-Icon des LAUFENDEN Fensters
    mit einem unsichtbaren Bild - der tatsaechliche, vom Owner gemeldete
    Fehler ("transparenter Taskleisteneintrag"). Die vorherige
    Code-Annahme ("Taskleiste/Alt+Tab zeigen weiterhin das echte
    Lexono-Icon") war fuer die TITELLEISTE korrekt beobachtet, aber fuer
    die TASKLEISTE nachweislich falsch.

    Echter Fix: `WS_EX_DLGMODALFRAME` ist ein dokumentierter, verbreiteter
    Win32-Trick, der die Titelleiste anweist, GAR KEINEN Icon-Platz zu
    reservieren (statt ein Icon zu ersetzen) - zusammen mit
    `SetWindowPos(..., SWP_FRAMECHANGED)`, das Windows zwingt, den
    nicht-client Fensterrahmen (inkl. Titelleiste) mit dem neuen Stil neu
    zu zeichnen. `WM_SETICON`/`Form.Icon` werden dabei UEBERHAUPT NICHT
    angefasst - das von pywebview beim Fenster-Erzeugen bereits korrekt
    aus der .exe extrahierte Icon (siehe .venv/Lib/site-packages/webview/
    platforms/winforms.py, `ExtractIconW(..., sys.executable, 0)`) bleibt
    dadurch fuer Taskleiste/Alt+Tab/Fenster-Vorschau vollstaendig
    unveraendert erhalten - nur der Titelleisten-Icon-Platz verschwindet.

    ERGAENZUNG (04.10., Owner-Direktive "MANDANTENDETAIL: VERTIKALE
    FLAECHENNUTZUNG OPTIMIEREN UND NATIVES MINI-LOGO ENTFERNEN"): per
    echtem Owner-Screenshot der installierten Anwendung (Windows 11,
    Build 10.0.26200) bestaetigt, dass das Icon trotz dieses bereits
    bestehenden `WS_EX_DLGMODALFRAME`-Fixes WEITERHIN sichtbar ist - dies
    ist ein reiner Win32-Stilbit-Trick aus der Windows-XP/7/10-Aera; die
    ab Windows 11 ueberarbeitete, DWM-basierte Titelleisten-Darstellung
    scheint dieses Bit fuer die Icon-Platzreservierung nicht mehr in
    jedem Fall zu respektieren (nicht abschliessend geklaert, da ohne
    neuen Installer-Build/native Pruefung nicht reproduzierbar isoliert
    werden kann). Als robustere, von WinForms selbst offiziell
    unterstuetzte Ergaenzung (nicht Ersatz - beide Mechanismen schliessen
    sich nicht aus) wird zusaetzlich `Form.ShowIcon = False` gesetzt: ein
    dokumentiertes .NET-WinForms-Property, das GEZIELT nur das Titel-
    leisten-Icon ausblendet (MSDN: "Gets or sets a value indicating
    whether an icon is displayed in the caption bar of the form") - die
    Taskleisten-/Alt+Tab-Darstellung liest das Icon ueber einen
    getrennten Mechanismus (weiterhin direkt aus der .exe-Ressource,
    unabhaengig von `ShowIcon`) und bleibt dadurch unberuehrt, exakt wie
    beim bereits bestehenden Win32-Fix beabsichtigt. `window.native` ist
    bereits das echte WinForms-`Form`-Objekt (siehe `self.pywebview_
    window.native = self` in winforms.py) - kein zusaetzlicher Import
    noetig. UNGETESTET in der echten installierten Anwendung (kann per
    Dev-Server/CDP nicht geprueft werden - betrifft ausschliesslich die
    native Fenster-Chrome, nicht den HTML-Inhalt) - siehe Abschlussbericht
    fuer die ausdrueckliche Einschraenkung."""
    try:
        import ctypes

        hwnd = window.native.Handle.ToInt32()  # type: ignore[union-attr]
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]

        ex_style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, ex_style | _WS_EX_DLGMODALFRAME)
        user32.SetWindowPos(
            hwnd,
            0,
            0,
            0,
            0,
            0,
            _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOZORDER | _SWP_FRAMECHANGED,
        )
    except Exception:  # noqa: BLE001 - rein kosmetisch, darf den Start nie gefaehrden
        pass
    try:
        window.native.ShowIcon = False  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001 - rein kosmetisch, darf den Start nie gefaehrden
        pass


class _NativeApi:
    """JS-Brücke für das native WebView2-Fenster (20.08., Scan-Ordner-Dialog)
    - macht `webview.Window.create_file_dialog` als `window.pywebview.api.
    pick_folder()` im Frontend aufrufbar (siehe app/web/templates/
    settings.html: "Ordner auswählen"-Button neben dem Scan-Ordner-
    Pfadfeld), damit Nutzer:innen einen echten nativen Windows-
    Ordnerauswahldialog statt manueller Pfadeingabe bekommen.

    `_window` wird ERST NACH `webview.create_window(...)` gesetzt (die
    js_api-Instanz muss bereits beim Erzeugen des Fensters existieren,
    kennt das fertige Fenster-Objekt selbst aber noch nicht - siehe
    `_serve_with_window` unten). Nur im gebündelten Fenster relevant: im
    reinen Browser-/`--no-window`-Modus existiert `window.pywebview` im
    Frontend gar nicht, das Textfeld bleibt dort die einzige Eingabe
    (Feature-Detection im Template, kein Fehlerfall).

    BEWUSST `_window` (führender Unterstrich), NICHT `window`: pywebviews
    eigene JS-Bridge-Generierung (`webview/util.py::get_functions`) läuft
    beim Laden jeder Seite per `dir()`/`getattr()` rekursiv über alle
    NICHT mit "_" beginnenden Attribute dieser Klasse, um sie als
    JS-aufrufbare Funktionen verfügbar zu machen (der Mechanismus, der
    `pick_folder()` unten überhaupt erst als `window.pywebview.api.
    pick_folder()` im Frontend bereitstellt - siehe Docstring oben). Ein
    öffentliches `window`-Attribut wäre selbst kein Zyklus, brächte diese
    Rekursion aber in den echten `webview.Window`/nativen WinForms-
    Objektgraphen hinein (u. a. `.native.AccessibilityObject.Bounds`) -
    dort liefert jeder Property-Zugriff über die .NET-Interop-Schicht
    (pythonnet) ein NEUES Wrapper-Objekt mit neuer Python-`id()`, wodurch
    `get_functions`s eigene Zyklus-Erkennung (Ablage besuchter `id()`-Werte)
    nie zuschlägt und die Rekursion faktisch endlos weiterläuft (real
    beobachtet: "Lexono (Keine Rückmeldung)" nach dem Rendern des Login-
    Fensters, Live-Thread-Dump zeigte `Thread-3 (generate_js_object)`
    dauerhaft in `get_functions` auf genau dieser Kette hängend). Der
    führende Unterstrich nutzt `get_functions`s eigene, dafür vorgesehene
    Ausschlussregel (`if name.startswith('_'): continue`, VOR jedem
    `getattr()`-Aufruf geprüft) und verhindert so, dass dieser gefährliche
    Objektgraph überhaupt erreicht wird - ohne die JS-Exposition selbst
    (weiterhin aktiv für `pick_folder()`) oder pywebview selbst
    anzutasten."""

    _window: object | None = None

    def pick_folder(self) -> str:
        if self._window is None:
            return ""
        import webview

        result = self._window.create_file_dialog(webview.FOLDER_DIALOG)  # type: ignore[attr-defined]
        if not result:
            return ""
        return str(result[0])

    def open_data_folder(self) -> bool:
        """Oeffnet das tatsaechliche Lexono-Datenverzeichnis (siehe
        app/setup/paths.py::resolve_data_dir) im Windows-Explorer (06.10.,
        Owner-Direktive "LEXONO - EINSTELLUNGEN UI REBUILD", Karte "Daten &
        Speicher" -> "Speicherort der Daten"). Rein lesende Komfortfunktion
        (oeffnet nur, erstellt/veraendert nichts) - selbe Feature-Detection
        wie `pick_folder()` oben: im reinen Browser-/--no-window-Modus
        existiert `window.pywebview` nicht, der Button bleibt dort per
        JS-Feature-Detection ausgeblendet (siehe settings.html)."""
        from app.setup.paths import resolve_data_dir

        data_dir = resolve_data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(data_dir))  # type: ignore[attr-defined]
        except OSError:
            return False
        return True

    # Frueher (Masterprompt V2, Task #61): vier JS-aufrufbare Methoden
    # (minimize_window/close_window/move_window_by/resize_window_by), die
    # eine HTML-Titelleiste im frameless-Fenster mit Verschieben/Resize/
    # Minimieren/Schliessen ausstatteten. Real gefundener, schwerwiegender
    # Nachteil dieser Loesung (Window-Chrome-Review): ein frameless-Fenster
    # hat strukturell KEIN echtes Windows-Maximieren, keinen funktionierenden
    # Windows-Snap und keine garantiert normale Alt+Tab-/Taskleisten-
    # Darstellung - Verhalten, das eine echte Windows-Desktop-Anwendung
    # haben MUSS. Deshalb zurueckgebaut auf natives Fenster-Chrome
    # (`frameless` nicht mehr gesetzt, siehe `_serve_with_window`) - Windows
    # selbst stellt Verschieben/Resize/Minimieren/Maximieren/Schliessen/Snap/
    # Alt+Tab/Taskleiste bereit, keine JS-Bruecke mehr dafuer noetig. Das
    # zugehoerige HTML/CSS/JS (partials/app_titlebar.html, static/js/
    # app_titlebar.js) bleibt im Repository, aktiviert sich aber NICHT mehr:
    # dessen eigene Feature-Detection prueft exakt auf die Existenz von
    # `move_window_by`/`close_window` auf diesem JS-Api-Objekt (siehe dort).


def cmd_serve(*, open_window: bool = True) -> int:
    from app.config import get_settings

    lock_handle = _acquire_single_instance_lock()
    if lock_handle is None:
        print(
            "FEHLER: Lexono läuft bereits - eine zweite gleichzeitige Instanz "
            "würde sich denselben Port und dieselbe Datenbankdatei teilen. Bitte das "
            "bereits geöffnete Fenster verwenden.",
            file=sys.stderr,
        )
        return 1

    try:
        settings = get_settings()

        # Ausstehende Migrationen automatisch anwenden - laut Handoff-Doku
        # "muss beim ersten Start (und bei jedem Update) laufen". Alembic-
        # Upgrades sind idempotent (kein Effekt, wenn bereits auf "head").
        migrate_exit_code = cmd_migrate()
        if migrate_exit_code != 0:
            return migrate_exit_code

        if not open_window:
            import uvicorn

            from app.main import app

            uvicorn.run(
                app,
                host=settings.host,
                port=settings.port,
                log_level=settings.log_level.lower(),
            )
            return 0

        return _serve_with_window(settings)
    finally:
        _release_single_instance_lock(lock_handle)


#: Stabile, produktspezifische Windows-App-Identitaet (AppUserModelID,
#: "AUMID") - ROOT-CAUSE-FIX (03.10., Owner-Direktive "WINDOWS-
#: TASKLEISTEN-ICON, FENSTERIDENTITAET UND DESKTOP-VERKNUEPFUNG"): per
#: Live-Fenster-Diagnose bestaetigt, dass PROJEKTWEIT noch nie eine
#: explizite AppUserModelID gesetzt wurde - Windows vergibt dann pro
#: Prozess automatisch eine implizite ID anhand des jeweiligen EXE-Pfads.
#: Real relevant, weil der Desktop-/Startmenue-Shortcut (siehe
#: windows/installer.iss) NICHT direkt auf Lexono.exe zeigt, sondern auf
#: "wscript.exe ... Start.vbs" (bewusst, fuer den unsichtbaren Start-
#: Fall, siehe Start.vbs) - ohne eine vom Launcher UNABHAENGIGE, feste
#: Identitaet koennte Windows das spaeter tatsaechlich laufende
#: Lexono.exe-Fenster shell-seitig inkonsistent dem Shortcut zuordnen.
#: Format nach Microsoft-Vorgabe ("CompanyName.ProductName[.SubProduct]",
#: jedes Segment <=64, Gesamtlaenge <=128 Zeichen) - siehe
#: MyAppPublisher/MyAppName in windows/installer.iss fuer die Herkunft
#: der beiden Namensteile. Bewusst eine EIGENE, stabile Kennung (keine
#: generische/fremde Identitaet) und bewusst erst bei "serve --window"
#: (hier) gesetzt, nicht projektweit in main() - die reinen CLI-
#: Unterkommandos (migrate/setup/...) erzeugen nie ein Fenster und
#: brauchen deshalb keine Shell-Taskleisten-Identitaet.
_APP_USER_MODEL_ID = "LexonoProjekt.Lexono"


def _set_app_user_model_id() -> None:
    """Setzt die explizite AppUserModelID fuer den AKTUELLEN Prozess -
    MUSS laut Microsoft-Dokumentation aufgerufen werden, BEVOR das erste
    Fenster erzeugt wird (hier: vor `webview.create_window(...)` weiter
    unten in `_serve_with_window`), da Windows die Taskleisten-Identitaet
    eines Fensters beim Erzeugen seiner ersten Taskleisten-Schaltflaeche
    einfriert. Nur unter Windows verfuegbar (`shell32.
    SetCurrentProcessExplicitAppUserModelID`) - rein kosmetisch/
    Shell-Integration, darf den Start deshalb nie gefaehrden."""
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(_APP_USER_MODEL_ID)
    except Exception:  # noqa: BLE001 - Shell-Integration darf den Start nie verhindern
        pass


def _hide_console_window() -> None:
    """Versteckt die Konsole DIESES Prozesses, falls noch sichtbar
    (ROOT-CAUSE-FIX 03.10., Owner-Direktive "WINDOWS-TASKLEISTEN-ICON,
    FENSTERIDENTITAET UND DESKTOP-VERKNUEPFUNG").

    ECHTER, per Live-Fenster-Diagnose gefundener ZWEITER Beitrag zum
    gemeldeten Fehler (zusaetzlich zum WM_SETICON-Fund bei
    `_remove_title_bar_icon`): ein direkter Start der gebauten .exe (ohne
    den Start.vbs-Hide-Pfad) zeigte eine ZWEITE, tatsaechlich SICHTBARE
    Top-Level-Fensterklasse namens "PseudoConsoleWindow" (gehoert zum
    `console=True`-Build, siehe windows/lexono.spec) - mit `WM_GETICON`
    bestaetigt KOMPLETT OHNE eigenes Icon (weder ICON_SMALL noch
    ICON_BIG gesetzt). Ein zusaetzlicher, leer/generisch wirkender
    Taskleisteneintrag NEBEN dem echten Lexono-Fenster - exakt das vom
    Owner gemeldete Symptom ("ein transparenter oder unsichtbarer
    zusaetzlicher Taskleisteneintrag").

    `Start.vbs` versteckt diese Konsole bereits separat (SW_HIDE direkt
    beim Prozessstart ueber `objShell.Run(..., 0, False)`), aber NUR fuer
    GENAU DEN darueber gestarteten Pfad - ein direkter Doppelklick auf
    `Lexono.exe` (Phase-D-Testfall "Direktstart" dieser Direktive) oder
    der in Start.vbs selbst bereits dokumentierte, seltene
    Upgrade-Randfall ("Konsole faelschlich sichtbar gestartet, obwohl
    kein Setup noetig ist") blieben davon unberuehrt. Deshalb zusaetzlich
    HIER, im Python-Prozess selbst, unabhaengig vom jeweiligen externen
    Startweg - sicher und ohne Prozessarchitektur-Aenderung (dieselbe
    bereits vom Betriebssystem erzeugte Konsole wird nur ausgeblendet,
    nicht entfernt/neu erzeugt; `console=True` im PyInstaller-Spec bleibt
    unveraendert, siehe dessen Begruendung dort).

    GARANTIERT sicher fuer den interaktiven Setup-Assistenten: `main()`
    ruft `_serve_with_window` (einziger Aufrufer dieser Funktion) laut
    seiner eigenen Ablauflogik NUR auf, NACHDEM ein etwaiger `cmd_setup()`
    (braucht eine sichtbare Konsole fuer `input()`/`getpass()`) bereits
    vollstaendig abgeschlossen und zurueckgekehrt ist - an dieser Stelle
    wird garantiert keine Konsoleneingabe mehr erwartet."""
    try:
        import ctypes

        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            _SW_HIDE = 0
            ctypes.windll.user32.ShowWindow(hwnd, _SW_HIDE)
    except Exception:  # noqa: BLE001 - rein kosmetisch, darf den Start nie gefaehrden
        pass


def _serve_with_window(settings) -> int:  # noqa: ANN001 - Settings-Typ nur lazy importierbar
    """Startet den Server in einem Hintergrund-Thread und öffnet darüber ein
    natives WebView2-Fenster im Hauptthread (Prompt 46). Der bestehende
    Web-Stack (app/main.py, app/web/*) läuft dabei vollkommen unverändert -
    dieselbe FastAPI-App wie im `--no-window`-Pfad, nur eben nicht
    blockierend im Hauptthread gestartet, weil `webview.start()` genau das
    für sich selbst braucht (Standard-Einschränkung nativer GUI-Event-Loops
    unter Windows)."""
    _set_app_user_model_id()
    _hide_console_window()

    import uvicorn

    from app.main import app

    config = uvicorn.Config(
        app, host=settings.host, port=settings.port, log_level=settings.log_level.lower()
    )
    server = uvicorn.Server(config)
    server_thread = threading.Thread(target=server.run, name="lexono-uvicorn", daemon=True)
    server_thread.start()

    base_url = f"http://{settings.host}:{settings.port}"

    def _shutdown_server() -> None:
        server.should_exit = True
        server_thread.join(timeout=10)

    try:
        _wait_for_server_ready(f"{base_url}/health")
    except TimeoutError as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        _shutdown_server()
        return 1

    if not _is_webview2_runtime_available():
        print(
            "FEHLER: Die Microsoft-Edge-WebView2-Runtime wurde auf diesem Rechner nicht "
            "gefunden. Ohne sie würde das native Fenster still auf eine veraltete, mit "
            "dem Dashboard nicht kompatible Anzeige-Engine zurückfallen.\n"
            f"Bitte die Runtime herunterladen und installieren: {_WEBVIEW2_DOWNLOAD_URL}\n"
            "Alternativ jetzt ohne Fenster starten und im Browser öffnen: "
            "Lexono.exe serve --no-window",
            file=sys.stderr,
        )
        _shutdown_server()
        return 1

    import webview

    native_api = _NativeApi()
    window = webview.create_window(
        # Bewusst LEER, nicht "Lexono" (Owner-Fund, 19.09., per neuem
        # Login-Referenzbild bestaetigt und gegen die bereits laenger
        # bestehende Dashboard-Referenz 05_chat_startseite.png
        # gegengeprueft - BEIDE zeigen eine leere native Titelleiste ohne
        # Text, nicht "Lexono"): `_remove_title_bar_icon` (siehe dort)
        # entfernte bereits am 13.09. nur das Icon, liess den Text
        # "Lexono" aber unveraendert stehen - dadurch erschien die Marke
        # ZWEIMAL gleichzeitig (einmal hier in der nativen Titelleiste,
        # einmal in der eigenen Sidebar-/Login-Kartenmarke). Die Taskleiste/
        # Alt+Tab zeigen weiterhin das echte Lexono-Icon (kommt direkt aus
        # der .exe-Ressource, unabhaengig vom Fenstertitel-Text).
        "",
        f"{base_url}/dashboard/login",
        width=1400,
        height=900,
        min_size=(_MIN_WINDOW_WIDTH, _MIN_WINDOW_HEIGHT),
        resizable=True,
        # Bewusst KEIN frameless=True mehr (siehe _NativeApi-Kommentar
        # oben) - natives Fenster-Chrome ist Voraussetzung fuer echtes
        # Windows-Maximieren/Snap/Alt+Tab/Taskleisten-Verhalten.
        background_color="#F8FAFC",
        js_api=native_api,
        # ECHTER FUND (Nutzerfeedback, 13.09.): pywebview deaktiviert
        # Textauswahl/-markierung standardmaessig (`text_select=False` ist
        # der Default in webview.window.Window.__init__) - eine Chat-
        # Antwort liess sich dadurch nicht markieren/kopieren, obwohl
        # nichts im eigenen CSS/JS das verhinderte.
        text_select=True,
    )
    native_api._window = window
    # Alle drei Funktionen sind rein kosmetisch und unabhaengig voneinander
    # (siehe deren eigene try/except-Bloecke) - _apply_light_title_bar
    # ist seit dem Rueckbau von frameless=True wieder wirksam (echte
    # native Titelleiste vorhanden).
    window.events.shown += _apply_light_title_bar
    window.events.shown += _apply_rounded_corners
    window.events.shown += _remove_title_bar_icon
    # Blockiert im Hauptthread, bis der Nutzer das Fenster schließt.
    webview.start()

    _shutdown_server()
    return 0


def _run_migrate_subprocess(data_dir: Path) -> None:
    result = subprocess.run(_self_command("migrate"), cwd=str(data_dir), check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Datenbankmigration fehlgeschlagen (Exit-Code {result.returncode}).")


def _run_create_admin_subprocess(data_dir: Path, email: str, password: str | None) -> None:
    env = dict(os.environ)
    env["ADMIN_EMAIL"] = email
    if password:
        env["ADMIN_INITIAL_PASSWORD"] = password
    else:
        env.pop("ADMIN_INITIAL_PASSWORD", None)
    result = subprocess.run(_self_command("create-admin"), cwd=str(data_dir), env=env, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Anlegen des Admin-Nutzers fehlgeschlagen (Exit-Code {result.returncode}).")


_LOCAL_AI_SETUP_HEARTBEAT_INTERVAL_SECONDS = 30.0


def cmd_local_ai_setup() -> int:
    """Erkennt Hardware, waehlt ein passendes lokales Modell und richtet
    Ollama automatisiert ein (Phase 3, §71) - siehe
    app/local_ai/setup_orchestrator.py::LocalAiSetupService.run_setup fuer
    die eigentliche Ablauflogik (Hardware -> Empfehlung -> Ollama-Install
    -> Modell-Download -> Health Check -> `.env`-Eintrag). Laeuft (wie
    `create-admin`/`migrate`) als eigener Subprozess mit dem
    Datenverzeichnis als Arbeitsverzeichnis, damit `LocalAiSetupService`s
    Standard-`.env`-Pfad (relativ zu `cwd`) korrekt aufgeloest wird.

    `run_setup()` ist EIN blockierender Aufruf ueber den gesamten Ablauf
    (Hardware -> Ollama-Install -> Modell-Download -> Health-Check) - bei
    einem grossen, hardware-abhaengig gewaehlten Modell (real beobachtet:
    5,2 GB, ueber eine Stunde auf einer normalen Internetverbindung) gibt
    es sonst zwischen dem einleitenden `print()` und dem Abschluss-`print()`
    ueberhaupt keine Konsolenausgabe - fuer einen Benutzer nicht von einem
    Haenger zu unterscheiden (real beobachtet, siehe OPEN_ISSUES.md).
    Deshalb hier ein simpler Heartbeat-Thread: keine erfundene
    Fortschritts-/Prozentanzeige (Ollamas `/api/pull` laeuft bewusst mit
    `stream:false`, siehe ollama_provider.py._DOWNLOAD_TIMEOUT), nur eine
    ehrliche, periodische "laeuft noch"-Meldung."""
    from pathlib import Path as _Path

    from app.local_ai.setup_orchestrator import LocalAiSetupService

    print("Erkenne Hardware und ermittle ein passendes lokales KI-Modell...")
    service = LocalAiSetupService()

    stop_heartbeat = threading.Event()

    def _print_heartbeat() -> None:
        elapsed = 0.0
        while not stop_heartbeat.wait(_LOCAL_AI_SETUP_HEARTBEAT_INTERVAL_SECONDS):
            elapsed += _LOCAL_AI_SETUP_HEARTBEAT_INTERVAL_SECONDS
            print(
                f"... Einrichtung laeuft noch ({elapsed:.0f}s vergangen). "
                "Je nach Hardware und Internetverbindung kann der Download "
                "des lokalen KI-Modells laenger dauern - dies ist kein Fehler.",
                flush=True,
            )

    heartbeat_thread = threading.Thread(target=_print_heartbeat, daemon=True)
    heartbeat_thread.start()
    try:
        result = service.run_setup(download_dir=_Path("local_ai_download"))
    finally:
        stop_heartbeat.set()
        heartbeat_thread.join(timeout=1.0)
    if not result.success:
        print(
            f"HINWEIS: Lokale KI konnte nicht automatisch eingerichtet werden "
            f"(Schritt: {result.stage.value}): {result.error}\n"
            "Die Anwendung funktioniert weiterhin - die Cloud-Anbindung ist "
            "davon unabhaengig. Die Einrichtung kann spaeter erneut versucht werden.",
            file=sys.stderr,
        )
        return 1
    print(f"Lokale KI eingerichtet: Modell '{result.installed_model}' ist einsatzbereit.")
    return 0


def _run_local_ai_setup_subprocess(data_dir: Path) -> bool:
    """Wie `_run_migrate_subprocess`, aber bewusst NICHT ladungstragend -
    gibt nur zurueck, ob es geklappt hat, statt bei Fehlschlag eine
    Exception zu werfen (siehe run_setup_wizard-Docstring: ein
    fehlgeschlagener Local-AI-Setup darf die Ersteinrichtung nicht
    scheitern lassen)."""
    result = subprocess.run(_self_command("local-ai-setup"), cwd=str(data_dir), check=False)
    return result.returncode == 0


def _first_run_setup_required(data_dir: Path) -> bool:
    """Entscheidet, ob die Ersteinrichtung (noch einmal) laufen muss.

    ROOT CAUSE (real reproduziert, siehe OPEN_ISSUES.md): die fruehere
    Bedingung pruefte AUSSCHLIESSLICH, ob `.env` existiert. `.env` wird
    aber als ALLERERSTER Schritt von `run_setup_wizard()` geschrieben,
    VOR Migration und Admin-Anlage (siehe app/setup/wizard.py) - schlaegt
    einer dieser beiden spaeteren, tatsaechlich ladungstragenden Schritte
    fehl (z. B. ein einmaliger Subprozess-/Datenbankfehler, unterbrochene
    Installation, Antivirus-Interferenz waehrend des ersten Starts), bleibt
    `.env` bestehen, OHNE dass je ein Admin angelegt wurde. Jeder folgende
    Start hat die alte Bedingung dann als "Ersteinrichtung bereits erfolgt"
    gewertet und direkt die Login-Seite geoeffnet - fuer einen echten
    Endanwender ohne bekannte Zugangsdaten eine Sackgasse (kein Setup, kein
    Login moeglich). Deshalb genuegt eine bestehende `.env` allein nicht
    mehr - zusaetzlich muss mindestens ein Benutzer tatsaechlich in der
    Datenbank existieren.

    Bewusst zustandslos fuer den Rest des Prozesses: der `get_settings()`-
    Cache wird nach der Pruefung wieder geleert, damit ein anschliessender
    `cmd_setup()`/`cmd_serve()`-Aufruf garantiert die aktuelle `.env`
    frisch einliest (siehe run_setup_wizard-Docstring zum selben Thema)."""
    env_path = data_dir / ".env"
    if not env_path.exists():
        return True
    from app.config import get_settings

    try:
        from app.db.session import SessionLocal
        from app.models import User

        db = SessionLocal()
        try:
            return db.query(User).first() is None
        finally:
            db.close()
    except Exception:
        # DB/Tabelle fehlt oder ist aus einem anderen Grund nicht lesbar -
        # dann ist die Ersteinrichtung ebenfalls nicht abgeschlossen. Lieber
        # den Assistenten erneut anbieten, als den Benutzer in einer
        # Login-Sackgasse ohne Zugangsdaten zu lassen.
        return True
    finally:
        get_settings.cache_clear()


def cmd_setup(data_dir: Path, *, force: bool) -> int:
    from app.config.settings import Settings
    from app.setup import WizardError, run_setup_wizard

    print("=== Lexono Setup-Assistent ===")
    print(f"Datenverzeichnis: {data_dir}")
    admin_email = input("E-Mail-Adresse des ersten Admin-Nutzers: ").strip()
    entered_password = getpass.getpass(
        "Initiales Admin-Passwort (leer lassen, um automatisch eines zu generieren): "
    )
    admin_password = entered_password or None

    # Lokale KI ist Bestandteil der Zielarchitektur (Phase 3, §6: "Local AI
    # ist jetzt Pflicht") - deshalb als Standardvorschlag beim Setup
    # angeboten (leere Eingabe = Ja), aber NICHT erzwungen: ein Nein hier
    # (oder ein spaeterer Fehlschlag, z. B. fehlendes Internet) verhindert
    # nicht die Installation/Nutzung der Anwendung, siehe run_setup_wizard.
    setup_local_ai_answer = input(
        "Lokale KI (Ollama) jetzt automatisch einrichten? Erkennt die "
        "Hardware und laedt bei Bedarf ein passendes Modell herunter "
        "(Groesse und Dauer haengen von der erkannten Hardware ab - von "
        "unter einer Minute bis zu einer Stunde oder mehr, je nach "
        "Internetverbindung; waehrend des Downloads erscheint regelmaessig "
        "eine Statusmeldung). [J/n]: "
    ).strip().lower()
    setup_local_ai = setup_local_ai_answer not in ("n", "nein", "no")

    default_host = Settings.model_fields["host"].default
    default_port = Settings.model_fields["port"].default

    try:
        result = run_setup_wizard(
            data_dir=data_dir,
            admin_email=admin_email,
            admin_password=admin_password,
            run_migrations=lambda: _run_migrate_subprocess(data_dir),
            create_admin=lambda email, password: _run_create_admin_subprocess(
                data_dir, email, password
            ),
            run_local_ai_setup=(
                (lambda: _run_local_ai_setup_subprocess(data_dir)) if setup_local_ai else None
            ),
            host=default_host,
            port=default_port,
            force=force,
        )
    except (WizardError, FileExistsError, RuntimeError) as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 1

    print(f"Setup abgeschlossen. Konfiguration geschrieben nach: {result.env_path}")
    if result.local_ai_setup_succeeded is True:
        print("Lokale KI wurde erfolgreich eingerichtet.")
    elif result.local_ai_setup_succeeded is False:
        print(
            "Lokale KI konnte nicht automatisch eingerichtet werden - die "
            "Anwendung ist trotzdem einsatzbereit. Ein erneuter Versuch ist "
            "spaeter jederzeit moeglich (Lexono.exe local-ai-setup)."
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv

    parser = argparse.ArgumentParser(prog="Lexono", description=__doc__)
    subparsers = parser.add_subparsers(dest="command")
    serve_parser = subparsers.add_parser(
        "serve", help="Startet den Webserver + natives Fenster (Standard ohne Argument)"
    )
    serve_parser.add_argument(
        "--no-window",
        action="store_true",
        help=(
            "Kein natives Fenster öffnen, nur den Server starten (Verhalten vor Prompt "
            "46, weiterhin nützlich für Entwickler/Debugging)"
        ),
    )
    setup_parser = subparsers.add_parser("setup", help="Führt die Ersteinrichtung durch")
    setup_parser.add_argument(
        "--force",
        action="store_true",
        help="Bestehende .env überschreiben (Vorsicht: macht laufende Sessions ungültig)",
    )
    subparsers.add_parser("migrate", help="Führt ausstehende Datenbankmigrationen aus")
    subparsers.add_parser(
        "create-admin",
        help="Legt den initialen Admin-Nutzer an (liest ADMIN_EMAIL/ADMIN_INITIAL_PASSWORD)",
    )
    subparsers.add_parser(
        "reset-admin-password",
        help="Setzt das Passwort eines bestehenden Admin-Nutzers zurück, falls das initiale "
        "Passwort verloren ging (liest ADMIN_EMAIL/RESET_PASSWORD)",
    )
    subparsers.add_parser(
        "local-ai-setup",
        help="Richtet die lokale KI (Ollama) automatisiert ein (Hardware-Erkennung, "
        "Modell-Empfehlung, Installation, Download, Health Check)",
    )
    restore_parser = subparsers.add_parser(
        "restore",
        help="Stellt Datenbank + Dokumentenspeicher aus einem Backup-Archiv wieder her "
        "(Anwendung muss dafür gestoppt sein)",
    )
    restore_parser.add_argument("--archive", required=True, help="Pfad zum Backup-ZIP")
    restore_parser.add_argument(
        "--yes", action="store_true", help="Bestätigung überspringen"
    )

    args = parser.parse_args(argv)
    command = args.command or "serve"

    from app.setup import resolve_data_dir

    data_dir = resolve_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(data_dir)

    if command == "setup":
        return cmd_setup(data_dir, force=args.force)
    if command == "migrate":
        return cmd_migrate()
    if command == "create-admin":
        return cmd_create_admin()
    if command == "reset-admin-password":
        return cmd_reset_admin_password()
    if command == "local-ai-setup":
        return cmd_local_ai_setup()
    if command == "restore":
        return cmd_restore(archive=args.archive, yes=args.yes)

    # command == "serve" (auch der implizite Default ohne jedes Argument -
    # dort hat argparse die "serve"-Subparser-Attribute nie befüllt, daher
    # getattr mit sicherem Default statt args.no_window direkt).
    open_window = not getattr(args, "no_window", False)

    if _first_run_setup_required(data_dir):
        env_existed_already = (data_dir / ".env").exists()
        if env_existed_already:
            print(
                "Unvollstaendige Ersteinrichtung erkannt (Konfiguration vorhanden, "
                "aber kein Benutzer angelegt) - Ersteinrichtung wird erneut gestartet."
            )
        else:
            print("Keine Konfiguration gefunden - Ersteinrichtung wird gestartet.")
        setup_exit_code = cmd_setup(data_dir, force=env_existed_already)
        if setup_exit_code != 0:
            return setup_exit_code
    return cmd_serve(open_window=open_window)


if __name__ == "__main__":
    raise SystemExit(main())
