# Installierte EXE aus 3e6aa98 – native Prüfung im WebView2-Fenster (26.10.2026)

Alle Bilder wurden mit Win32 `PrintWindow(PW_RENDERFULLCONTENT)` direkt aus dem Fenster der **installierten**
`%LOCALAPPDATA%\Lexono\Lexono.exe` aufgenommen. Es handelt sich nicht um Browser-Screenshots. Die Steuerung lief über CDP
(`WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port`). **Keine Datenschutz- oder Pilotfreigabe.**

## Build und Installation

| | |
|---|---|
| Quellcommit | `3e6aa98` (gepusht, `feature/feedback-diagnose`) |
| Installer | `dist\installer\Lexono_Setup.exe`, 26.10.2026 21:34:16, 556 684 863 B |
| SHA-256 Installer | `1AD1C96A007CF44556552DF2FE14AA7441DBFB2C2FFE9C1EFCEFD85B03174DC3` |
| SHA-256 Lexono.exe (gebaut = installiert) | `81C9ED788048C7D6AEAF30CFE81A3D33E90E0DA1D97E265645DBF5D393F53235` |

**Bundle gegen HEAD geprüft:**

- 304/304 App-Module und 139/139 Datendateien sind identisch.
- Der Bytecode des Einstiegsskripts `run.py` ist identisch und enthält `_should_start_maximized`.

**Sicherung vorher:** `C:\Users\Bonit\Lexono_backup_20261026_pre_install_3e6aa98\`

- Daten 920/920 und App 1013/1013 Dateien.
- Die DB-Kopie ist bytegleich, `quick_check` ergibt ok.

**Stille Installation:**

- Exitcode 0, 81 s, Log `install_3e6aa98.log` in der Sicherung.
- Die produktive DB ist durch die Installation unverändert.

## Isolierung

- Eigene Ablage (`LEXONO_DATA_DIR` im Job-Ordner) mit synthetischem Konto `qa-install@lexono.test` und ausschließlich
  synthetischen Dokumenten (`QA-SYNTH-*`).
- Cloud-Gateway auf einem toten Endpunkt (`127.0.0.1:9`), dadurch ohne Cloud-Aufruf.
- Die Antwort in `01-installiert-…` stammt vom lokalen QA-Aufzeichnungsserver („QA-Recorder (synthetische Antwort, keine
  echte KI)“). Das ist **kein** Cloud-E2E-Nachweis.

## Die drei Korrekturen (`start-und-korrekturen/`, `native_verify.json`)

| Prüfung | Ergebnis installierte EXE |
|---|---|
| Start maximiert, wenn 1400×900 nicht in den Arbeitsbereich passt | `IsZoomed` = true; Client 0,29–1920,1080 liegt im Arbeitsbereich 1920×1080; Viewport 1536×842 bei 125 % |
| Kein Querscrollen durch den Tooltip der Datenschutz-Ecke | `div.main` Überstand **0 px**; `::after` mit `right: 0px` (Eckvariante) |
| Anfangsfokus im Pseudonymisierungs-Pop-up | `H2#pseudo-center-title`, kein sichtbarer Fokusrahmen; Fußzeile im Viewport |

Sichtprüfung der Bilder:

- 01: keine horizontale Bildlaufleiste.
- 02: kein Fokusrahmen am ersten Ansichts-Tab, Fußzeile mit „Abbrechen“ sichtbar.

## Szenarien (`szenarien/`, `native_qa.json`)

20 Aufnahmen in exakten Client-Größen 1400×830, 1280×720 und 960×600 (125 %). Das Bild `00-…` zeigt das wiederhergestellte
Standardfenster mit 1386×844, die übrigen Bilder die gesetzten Größen.

In allen Pop-up-Zuständen:

- keine horizontale Bildlaufleiste auf Seite oder Dialog;
- keine überlaufenden Bereiche;
- Fußzeile, Abbrechen- und Bestätigen-Button sowie die Antwortoptionen sind sichtbar.

**Geprüfte Abläufe:**

- **Auftrag blockiert:** Bei einem Auftrag am noch nicht freigegebenen Dokument wurde nichts gesendet (alle 3
  Unterhaltungen).
- **Mehrdokument-Pop-up:** PDF + DOCX, 14 Funde, 2 Rückfragen; Bestätigen bleibt gesperrt, solange Rückfragen offen sind.
- **Ansichten:**
  - Seitenübersicht, Zoom 140 %, Original und pseudonymisiertes Ergebnis;
  - DOCX-Tabelle;
  - Briefvorlage mit Seitenumbrüchen (26 Markierungen über 2 Seiten, einschließlich „Steffen Hoffmann“ auf Seite 2).
- **Entscheidungen bleiben erhalten:** Sie überleben Dokument- und Ansichtswechsel („Richter“ → pseudonymisiert).
- **Abbrechen:** Die DB ist unverändert (Entscheidungen 6/6, Freigaben 0/0, Nachrichten 52/52), der Chat bleibt
  erhalten.
- **Bestätigen:** Die Entscheidungen steigen von 6 auf 8, die Rückkehr in den Chat funktioniert.
- **Nach dem Lauf:** Die produktive DB ist unverändert, es laufen 0 Lexono-Prozesse.

## Grenzen

- Geprüft wurden nur ein Monitor (1920×1080, 125 %) und der Windows-11-Arbeitsplatz des Owners. Andere Skalierungen und
  Mehrmonitorbetrieb wurden nicht geprüft.
- Der Tooltip wurde über die berechnete Stilregel und den Überstand geprüft, nicht im Hover-Zustand fotografiert.
