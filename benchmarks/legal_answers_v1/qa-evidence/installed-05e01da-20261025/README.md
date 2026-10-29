# Installation aus Quellstand 05e01da und Funktionstest der installierten EXE (25.10.2026)

Getestet wurde mit synthetischen Daten in einer isolierten Instanz. **Das ist kein Cloud-E2E-Test.**

## Build

- **Quellstand:** Commit `05e01da`, gepusht. Gebaut mit `windows\build.ps1`.
- **Installer:** `dist/installer/Lexono_Setup.exe`, gebaut 22:13:26, 556.719.674 Bytes.
  SHA-256 `1a3e2fee9cdbe5cd6fbf99a59f47e81ace082f9774198e5d107b919fe64bf914`.
- **EXE:** `dist/Lexono/Lexono.exe`, gebaut 22:08:30, 35.081.104 Bytes.
  SHA-256 `f3d0b6d06abd77402e43efeeefe08c6158acca982c81ac375181ce7fd7006b9d`.
  - Die EXE ist byte-gleich mit dem Build aus `00d5f8a`. Seit `00d5f8a` hat sich am Anwendungscode nichts geändert,
    es kamen nur QA-Nachweise hinzu.
- **Inhaltsprüfung:**
  - 303 `app.*`-Module sind bytecode-gleich mit HEAD.
  - 140 Template-, Static- und Migrationsdateien sind byte-gleich.
  - Das Paket enthält 55 Migrationen, so viele wie das Repository.

## Installation

- **Ablauf:** still installiert ab 22:14:16, Exit-Code 0, „Installation process succeeded“ um 22:15:22.
- **Protokoll:** `C:\Users\Bonit\Lexono_backup_20261025_pre_install_00d5f8a\install_05e01da.log`.
- **Installierte EXE:** `%LOCALAPPDATA%\Lexono\Lexono.exe` mit SHA-256 `f3d0b6d0…`.
- **Migrationen:** keine. Datenbank und Paket stehen beide auf `schritt3_037`.
- **Echte Datenbank:** SHA-256 `961737B2…`, letzte Änderung 18:03:01. Vor und nach der Installation und nach dem
  Test unverändert.

## Funktionstest

Getestet wurde die installierte EXE mit `LEXONO_DATA_DIR` im temporären Job-Ordner, Modus `auto_review`, Port 8062
und dem Cloud-Endpunkt `127.0.0.1:9` (tot).

| Prüfung | Ergebnis |
|---|---|
| Schriftsatzerstellung (PDF, 2 Seiten) | Dialog mit 2 Seiten, 8 Markierungen „wird pseudonymisiert“ und 1 „Rückfrage offen“; Bestätigen gesperrt |
| Rückfrage beantwortet | Bestätigen aktiv |
| Abbrechen | Dialog geschlossen; 0 Entscheidungen und 0 Freigaben gespeichert, Rückfrage weiter offen |
| Ansichten | markierte Vorschau, Original und pseudonymisiertes Ergebnis (Screenshots `01`, `05`, `04`) |
| Bestätigen | Rückkehr in den Chat; danach technische Fehlermeldung statt KI-Antwort (toter Endpunkt) – **kein E2E** |
| Dokumentanalyse (DOCX) | Dialog „… und Analyse fortsetzen“; Tabellenzellen markiert; Hinweis auf fehlende Kopf- und Fußzeilen und Tabellenrahmen |
| Seitenleisten-Button | 8 px unter der Navigationskante, 22 px breit; ein Klick klappt die Seitenleiste ein |
| Datenschutz-Info-Button | kein Schatten, Radius 12 px, Info-„i“ vorhanden; ein Klick öffnet das Info-Fenster |
| Serverprotokoll | 0 ERROR-Zeilen, kein Traceback |

Diese Prüfung ist keine Datenschutz-, Pilot-, P0-08- oder C-14-Freigabe.
