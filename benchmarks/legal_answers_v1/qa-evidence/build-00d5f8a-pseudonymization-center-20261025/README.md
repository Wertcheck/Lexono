# App-Test Build 00d5f8a – zentraler Pseudonymisierungsdialog (25.10.2026)

Synthetische Daten, isolierte Instanz, **kein Cloud-E2E-Test**.

## Build

- **Quelle:** Commit `00d5f8a` (Branch `feature/feedback-diagnose`, gepusht). Gebaut mit `windows\build.ps1`.
- **EXE:** `dist/Lexono/Lexono.exe`
  - gebaut 25.10.2026 21:03:24
  - 35.081.104 Bytes
  - SHA-256 `f3d0b6d06abd77402e43efeeefe08c6158acca982c81ac375181ce7fd7006b9d`
- **Installer:** `dist/installer/Lexono_Setup.exe`
  - gebaut 21:08:20
  - 556.719.723 Bytes
  - SHA-256 `cc274ea2cb0449838ad13b89542f3ffeb7f59b13af7aa6871281677d564a096f`
- **Inhaltsprüfung:**
  - 303 `app.*`-Module sind bytecode-gleich mit `HEAD:00d5f8a`, darunter `app.documents.pseudonymization_review`.
  - 140 Template-, Static- und Migrationsdateien sind byte-gleich, darunter `pseudonymization_center.js` und
    `.html`.

## Testinstanz

- Die nicht installierte `dist`-EXE.
- `LEXONO_DATA_DIR` im temporären Job-Ordner, frische Datenbank.
- Freigabemodus `auto_review`.
- Port 8062.
- Cloud-Endpunkt `127.0.0.1:9`, vorher als abgewiesen geprüft.
- Die installierte Version `DEB6FA9B…` wurde nicht verändert.

## Ergebnisse

Siehe `center_qa.json` und die Screenshots `00`–`07`.

| Fall | Beobachtet |
|---|---|
| PDF, 2 Seiten mit Kopf- und Fußzeile, Schriftsatzauftrag | Gate blockiert. Dialog: 2 Originalseiten, 8 Markierungen „wird pseudonymisiert“ und 1 „Rückfrage offen“ (Richter, Seite 2). Bestätigen bleibt gesperrt. |
| Rückfrage beantwortet („Pseudonymisieren“) | 0 offene Rückfragen, Bestätigen aktiv. |
| Bestätigen | Rückkehr in den Chat. Antwort: „Die Antwort konnte aus technischen Gründen nicht erzeugt werden“ (toter Endpunkt). **Keine KI-Antwort, kein E2E.** Die Prüfung meldet danach Status `ok`. |
| DOCX mit Tabelle, Kopf- und Fußzeile, Analyseauftrag | Button „… und Analyse fortsetzen“. Tabellenzellen markiert. Der Hinweis nennt 2 nicht dargestellte Kopf-/Fußzeilen und fehlende Tabellenrahmen. |

Kein Pilot-, P0-08-, C-14- oder Datenschutz-Release.
