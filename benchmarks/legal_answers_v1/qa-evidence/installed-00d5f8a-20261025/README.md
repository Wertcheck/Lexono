# Installation von Build 00d5f8a und Funktionstest der installierten EXE (25.10.2026)

Getestet wurde mit synthetischen Daten in einer isolierten Instanz. **Das ist kein Cloud-E2E-Test.**

## Installation

- **Installer:** `dist/installer/Lexono_Setup.exe`, SHA-256 `cc274ea2cb0449838ad13b89542f3ffeb7f59b13af7aa6871281677d564a096f`.
  Aufruf: `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART`.
- **Ablauf:** Start 21:43:41, Exit-Code 0, „Installation process succeeded“ um 21:44:56. Die App wurde danach nicht
  automatisch gestartet (`skipifsilent`).
- **Protokoll:** `C:\Users\Bonit\Lexono_backup_20261025_pre_install_00d5f8a\install_00d5f8a.log`.
- **Installationsort:** `%LOCALAPPDATA%\Lexono` (feste App-ID). Installiert ist jetzt `Lexono.exe` mit SHA-256
  `f3d0b6d06abd77402e43efeeefe08c6158acca982c81ac375181ce7fd7006b9d` (Build `00d5f8a`). Vorher war es `DEB6FA9B…`.
- **Sicherungen vor der Installation:** der alte Programmordner unter `…\LocalAppData_Lexono` und die geprüfte
  Datensicherung im selben Ordner.
- **Migrationen:** keine. Die Datenbank steht bereits auf dem Stand `schritt3_037`, und der Build enthält keine
  neuere Migration.
- **Echte Datenbank:** `C:\ProgramData\Lexono\data\kanzlei_ai.db` mit SHA-256 `961737B2…`. Vor und nach der
  Installation und nach dem Test identisch, letzte Änderung weiterhin 18:03:01.

## Funktionstest

Getestet wurde die **installierte** EXE mit `LEXONO_DATA_DIR` im temporären Job-Ordner (frische Datenbank, Modus
`auto_review`), Port 8062 und dem Cloud-Endpunkt `127.0.0.1:9` (tot, vorher geprüft). Die echte Datenbank wurde
dabei nicht benutzt.

| Prüfung | Ergebnis |
|---|---|
| Start und Oberfläche | gestartet, Anmeldung und Chat erreichbar; im Serverprotokoll keine ERROR-Zeilen und kein Traceback |
| Schriftsatzerstellung (PDF, 2 Seiten) | Gate blockiert; Dialog „… und Schriftsatz erstellen“ mit 2 Seiten, 8 Markierungen „wird pseudonymisiert“ und 1 „Rückfrage offen“ |
| Bestätigen bei offener Rückfrage | gesperrt („1 Rückfrage(n) offen“) |
| Rückfrage beantwortet | Bestätigen aktiv |
| Abbrechen | Dialog geschlossen; 0 Datenschutz-Entscheidungen und 0 Freigaben gespeichert, Rückfrage weiter offen |
| Bestätigen | Rückkehr in den Chat; danach technische Fehlermeldung statt KI-Antwort (toter Endpunkt). **Kein E2E-Erfolg.** |
| Dokumentanalyse (DOCX) | Dialog „… und Analyse fortsetzen“; Tabellenzellen markiert; Hinweis auf fehlende Kopf- und Fußzeilen und fehlende Tabellenrahmen |

Nachweise: Screenshots `00`–`07` (darunter `05b` nach dem Abbrechen) und `center_qa.json` mit den beobachteten
Zuständen und den Screenshot-Hashes.

Diese Prüfung ist **keine** Datenschutz-, Pilot-, P0-08- oder C-14-Freigabe.
