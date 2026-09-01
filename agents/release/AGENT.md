# Agent K: Build / Release / Installer

## Verantwortung

PyInstaller, Inno Setup, Installer, Updates, Deinstallation, Runtime-
Dependencies, saubere Builds, Release-Checks.

## Relevante Dateien

- `windows/installer.iss` – Inno Setup, inkl. `[Code]`-Section mit
  `ExtractTemporaryFile('MicrosoftEdgeWebview2Setup.exe')` (WebView2-Fix,
  NICHT rückgängig machen – siehe DECISIONS.md/CLAUDE.md-Historie)
- `run.py` – u. a. `_is_webview2_runtime_available()`,
  `webview.create_window(...)` (natives Fenster, Titel „Lexono“)

## Bekannte harte Lektionen (nicht wiederholen)

- PyInstaller/Inno-Setup NIEMALS über einen verschachtelten
  `powershell -File script.ps1`-Aufruf starten – verliert die
  venv-aktivierte PATH. Stattdessen `$env:Path` direkt im selben
  PowerShell-Aufruf voranstellen.
- LZMA-Kompression des ~1,1GB-Bundles dauert ca. 15–25 Minuten (v. a.
  wegen spaCy `de_core_news_lg`).
- `dontcopy` in Inno Setup extrahiert NICHT automatisch – erfordert
  explizites `ExtractTemporaryFile` im `[Code]`-Abschnitt.

## Bekannte offene Punkte

Fenster-Chrome-Frage (natives WinForms-Fenster vs. frameless mit Custom-
Controls) – siehe `.agentic/OPEN_ISSUES.md` (MEDIUM) und
`agents/frontend/AGENT.md`. Betrifft direkt diesen Agenten, da eine
Fensteränderung einen neuen Installer-Testzyklus erfordert.

## Letzter Stand

Frischer Build+Install+Smoke-Test am 31.08. erfolgreich (Installer
`dist/installer/Lexono_Setup.exe`, ~525MB, WebView2-Fix bestätigt stabil).

**01.09.**: `run.py` wechselte auf `frameless=True` (eigene Titelleiste,
Task #61 - siehe `.agentic/DECISIONS.md`). Erster Installer-Build+Install+
realer Fenstertest fand einen ECHTEN, schwerwiegenden Bug: die Titelleiste
existierte nur in `base.html`, das gebuendelte Fenster startet aber auf
`login.html` (eigenstaendiges Template, erbt nicht von base.html) - das
Fenster war beim ersten echten Test komplett unbedienbar (kein X), der
Nutzer musste ueber den Task-Manager beenden. Behoben durch Auslagerung in
ein gemeinsames Partial (`partials/app_titlebar.html`) + eine gemeinsame
statische JS-Datei (`static/js/app_titlebar.js`), eingebunden in ALLEN
DREI eigenstaendigen Root-Templates (`base.html`, `login.html`,
`unlock.html`). Regressionstest ergaenzt (`test_auth_web.py::
test_login_page_activates_custom_titlebar_script_before_login`).

**Zweiter Rebuild+Install (01.09., spaeter Abend)**: PyInstaller-Build +
Inno-Setup-Compile erfolgreich (`dist/installer/Lexono_Setup.exe`,
~524,7MB). Erster Silent-Install-Versuch haengte sich >18 Min. bei
konstant 0% CPU auf (sichtbares, aber inaktives "Setup - Lexono..."-
Fenster, keine Kindprozesse, install_log2.txt nie erzeugt) - nach
Ausbleiben jeder Fortschrittsaenderung ueber 5+ Min. hinweg als echter
Stall bewertet und der Prozess beendet; ZWEITER Versuch (identischer
Befehl, unveraendertes .exe) lief anschliessend sauber durch und beendete
sich normal. Ursache des ersten Haengers nicht abschliessend geklaert
(Kandidaten: Windows-Defender-Echtzeitpruefung der frisch entpackten
~1,1GB, oder ein einmaliger Inno-Setup-internal-Zustand) - kein
reproduzierbares Muster ueber beide Versuche hinweg, daher nicht als
systemischer Installer-Fehler eingestuft, aber als bekanntes Risiko in
OPEN_ISSUES.md festgehalten.

Automatisierter HTTP-Smoke-Test GEGEN DIE ECHTE INSTALLATION (nicht nur
gegen TestClient) bestaetigt: Login -> Chat, Titelleisten-Markup +
`app_titlebar.js` im ausgelieferten HTML vorhanden, alle bestehenden
Seiten erreichbar (200), Dokument-Upload im Chat funktioniert. Die
App laeuft absichtlich WEITERHIN (windowed mode, PID wurde nicht
gestoppt), damit die Titelleiste beim naechsten Blick auf den Bildschirm
sofort sichtbar ist - visuelle Bestaetigung (Drag/Resize/Close-Button
tatsaechlich klicken) steht noch aus, Nutzer war beim zweiten Rebuild
nicht mehr am Rechner.
