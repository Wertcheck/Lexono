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
