# Skill: Installer Build & Test

## Zweck

Einen funktionsfähigen, getesteten Windows-Installer erzeugen.

## Voraussetzungen

Aktiviertes `.venv`, PyInstaller + Inno Setup installiert (siehe
`windows/`).

## Vorgehensweise

1. PyInstaller-Build: `$env:Path` in DERSELBEN PowerShell-Session um
   `.venv\Scripts` ergänzen (kein verschachtelter `powershell -File`-
   Aufruf – verliert sonst die venv-PATH, siehe
   `agents/release/AGENT.md`).
2. Inno Setup kompilieren (`ISCC.exe windows/installer.iss`) – dauert bei
   der aktuellen Bundle-Größe (~1,1GB, dominiert von spaCy
   `de_core_news_lg`) ca. 15–25 Minuten.
3. Installer real installieren (nicht nur Build-Erfolg als „fertig“
   werten).
4. App starten, Login, Chat als Startseite, Dokument-Upload, Local-AI-
   Status, bestehende Funktionsseiten prüfen.
5. App sauber stoppen, temporäre Test-Artefakte (Logs,
   Smoke-Test-Skripte) aufräumen, `git status --porcelain` prüfen.

## Prüfungen

WebView2-Bootstrapper wird tatsächlich extrahiert (kein
`CreateProcess`-Fehler beim Silent-Install) – bekannter, bereits gefixter
Bug, siehe `windows/installer.iss` `[Code]`-Section.

## Relevante Dateien

`windows/installer.iss`, `run.py`, `agents/release/AGENT.md`
