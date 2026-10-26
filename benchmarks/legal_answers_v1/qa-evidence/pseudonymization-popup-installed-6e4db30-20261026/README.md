# Installierte Anwendung aus 6e4db30 – Pseudonymisierungsdialog, Seitenumbruch, Markierungen (26.10.2026)

Geprüft wurde die **installierte** EXE in einer isolierten Instanz mit synthetischen Dokumenten.
**Das ist kein Cloud-E2E-Test:** Der Cloud-Endpunkt war absichtlich tot (`127.0.0.1:9`). Eine vollständige
Cloud-Verarbeitung ist damit nicht belegt.

## Quellstand, Build, Installation

- **Quellstand:** `6e4db30` auf `feature/feedback-diagnose`, identisch mit `origin`. Keine uncommitteten Änderungen in
  `app/`, `tests/`, `windows/` und `run.py`.
  - Fremde Änderungen im Arbeitsbaum (§90 in `PRIVACY_REVIEW_ARCHITECTURE.md`, gelöschte Logos unter
    `assets/branding/`) gehen nicht in den Build ein.
  - Seit dem bisher installierten Stand `4306389` haben sich nur 5 Dateien geändert: `docx_layout.py`,
    `pseudonymization_review.py`, `app.css`, `pseudonymization_center.js` und das Template. Keine Migration, keine
    neuen Abhängigkeiten.
- **Build:** `windows\build.ps1` (PyInstaller + Inno Setup), erfolgreich.
  - Installer `dist/installer/Lexono_Setup.exe`, 26.10.2026 13:05:03, 556.657.986 Bytes,
    SHA-256 `00044dfc539d4002d24c07bb09c26bf6bb792e972da4107df4992015c95576d0`.
  - EXE `dist/Lexono/Lexono.exe`, 13:00:14, 35.095.989 Bytes,
    SHA-256 `97f8deb686b062157f24aaf50eebf8dff6540411833196da659fc11eb2cc120e`.
- **Inhaltsprüfung des Bundles gegen HEAD:**
  - 304/304 `app.*`-Module bytecode-gleich, darunter `app.documents.docx_layout` und
    `app.documents.pseudonymization_review`.
  - 139/139 Template-, Static- und Migrationsdateien byte-gleich.
- **Sicherung vor der Installation:** `C:\Users\Bonit\Lexono_backup_20261026_pre_install_6e4db30\`
  - Datenordner (907/907 Dateien), `.env`, `.setup_complete`, `lexono.db` sowie die bisher installierte App
    (1013/1013 Dateien, EXE `38115ebc…`).
  - Kopie der DB byte-gleich; `quick_check` ok, Schema `schritt3_037`.
- **Installation:** still, Exit-Code 0, 80 s, „Installation process succeeded“ um 13:08:34.
  - Protokoll: `install_6e4db30.log` im Sicherungsordner.
  - Installiert ist jetzt `%LOCALAPPDATA%\Lexono\Lexono.exe` mit SHA-256 `97f8deb6…`, identisch mit dem Build.
- **Produktive Datenbank** (`C:\ProgramData\Lexono\data\kanzlei_ai.db`):
  - vor und nach Installation und Test unverändert: SHA-256 `b9b34d30…`, letzte Änderung 25.10.2026 22:54:27 durch
    die App des Nutzers;
  - `quick_check` ok, Schema `schritt3_037`, 907 Dateien im Datenordner;
  - keine Testdaten (die Tests liefen auf einer eigenen Datenbank).

## Testinstanz

- Die installierte EXE (`serve --no-window`), Port 8062.
- `LEXONO_DATA_DIR` im temporären Job-Ordner, frische Datenbank nach `migrate`, Modus `auto_review`.
- Synthetisches Wegwerf-Konto (Admin). Cloud-Endpunkt tot, lokale KI aus.
- Serverprotokoll: 0 ERROR-Zeilen, kein Traceback.
- Browser: Headless Edge über CDP.
- Fenster:
  - 1400×830 = tatsächliches App-Fenster (`run.py` 1400×900 auf 1536×864 logisch);
  - außerdem 1920×1080, 1600×1000, 1280×720 und 960×600 (App-Minimum 900×600).

## Ergebnisse (Messwerte in `ux_redesign.json`)

**DOCX-Briefvorlage** (ganzer Inhalt in einer großen Tabellenzelle, mehrzeiliger Anschriftblock, manueller
Seitenumbruch, „Steffen Hoffmann“), Screenshots 00d–00f:

- 3 Seiten. Seite 1 ist gefüllt: Anschrift, Anrede, „Richter“, Absätze 01–17. Seite 2 enthält den Rest des Briefs,
  Seite 3 die Anlage nach dem manuellen Umbruch.
- **Kein Absatz fehlt.** Jeder der 18 Absätze trägt eine eindeutige synthetische Rufnummer. Alle 18 wurden erkannt
  und als Markierung auf einer Seite gefunden (17 auf Seite 1, 1 auf Seite 2). `nicht_gefunden` ist leer.
- **Ligatur:** „Steffen Hoffmann“ ist auf Seite 2 markiert. Vor 6e4db30 wurde er nicht gefunden.
- „Richter“ ist als offene Rückfrage auf Seite 1 markiert. Der mehrzeilige Anschriftblock bleibt in drei Zeilen; die
  Markierungen sitzen auf „Berlin“ und „Jakobstrasse 6“.

**Pop-up**, 30 geprüfte Zustände:

- Keine horizontale Scrollbar, keine überlaufenden Bereiche.
- Fußleiste, Abbrechen und Bestätigen immer sichtbar. Die Antworten der aktiven Rückfrage sind in jedem Zustand
  vollständig sichtbar, auch bei 1280×720 und 960×600.
- **Vorschau nutzt den Platz:** Seitenbreite 950/982 px (App-Fenster), 1062/1094 px (1600 breit), 870/902 px
  (1280×720), maximiert 1100 px. Die Auflösung folgt der Anzeigegröße (96–144 dpi).
- **Startposition:** beginnt an der Seite mit der Fundstelle (PDF: Seite 2 statt des leeren Endes von Seite 1).
  70-seitiges PDF: Sprung zu Seite 65.
- **Entscheidungen:**
  - nach Dokument- und Ansichtswechsel bleibt „Richter“ auf „pseudonymisiert“;
  - nach Wechsel über die Auswahlliste bleiben 3 von 9 entschieden;
  - Bestätigen ist gesperrt, solange eine Rückfrage offen ist, und aktiv, wenn alle beantwortet sind.
- **Abbrechen und Schnellaktion:** DB unverändert (Entscheidungen, Freigaben und Nachrichten vorher und nachher
  0/0/19). Abbruch einer Chat-Rückfrage: unverändert, die Blase „Rückfrage öffnen“ erscheint.
- **Bestätigen:**
  - am Dokument: Entscheidungen 0 → 2, Rückkehr in den Chat;
  - Chat-Rückfrage: 2 → 3.
  - Danach jeweils Wiederaufnahme bis zum Cloud-Aufruf, der am toten Endpunkt endet („technische Gründe“).

| Nr. | Fenster | Inhalt |
|---|---|---|
| 00d–00f | 1400×830 | DOCX-Briefvorlage: Seite 1 gefüllt, Seitenübersicht, Anlage auf Seite 3 |
| 01–11 | 1920×1080 | PDF + DOCX, Seitenübersicht, Dokumentwechsel (DOCX-Tabelle), Original, Ergebnis, maximiert, Sperre, alle beantwortet, Abbrechen |
| 12/13 | 1280×720 | Pop-up, Panel unabhängig gescrollt |
| 14–18 | 1600×1000 / 1280×720 | 6 Dokumente, 9 Rückfragen, Fortschritt, „Nächste offene“, Auswahlliste |
| 19–22 | 1280×720 | 70 Seiten: Sprung zu Seite 65, Miniaturen, Seite 3, Zoom 140 % |
| 23/24 | 1600×1000 | Prüfansicht ohne Auftrag: Namensschutz, Schnellaktion (nur Vorbelegung) |
| 24b/24c | 1600×1000 | Rückfrage beim Senden im selben Pop-up, Abbrechen |
| 25 | 960×600 | Mindestfenster |
| 26 | 1600×1000 | Dark Mode |
| 27–31 | 1280×720 / 1600×1000 | Bestätigen am Dokument und bei Chat-Rückfrage |

## Tests (Quellstand 6e4db30)

- Rendering-, HITL- und Pop-up-Gruppen (18 Dateien, u. a. `test_docx_layout_pagination`,
  `test_pseudonymization_center*`, `test_schriftsatz_release_resume`, `test_web_privacy_review`, `test_web_chat`,
  Rendering, Dokumentansicht): **513 bestanden, 1 xfailed, 4 fehlgeschlagen**.
- Die 4 Fehlschläge sind ausschließlich die bekannten `test_design_refresh`-Logo-Tests. Ursache: Die Logos unter
  `assets/branding/` wurden außerhalb dieser Arbeit gelöscht. Sie sind nicht wiederhergestellt; das ist kein Fehler
  des Builds.

## Einschränkungen

- Kein Cloud-E2E: Es wurde kein funktionierender Cloud-Endpunkt getestet.
- Der lokale DOCX-Satz bleibt eine Näherung (Schriftarten, Bilder, Textfelder und Fußnoten nicht dargestellt;
  Umbrüche können von Word abweichen).
- Die Screenshots stammen aus Headless Edge gegen die installierte EXE, nicht aus dem WebView2-Fenster. Server,
  Templates und Skripte sind identisch; die Fensterdarstellung von WebView2 selbst ist nicht abgebildet.
- Keine Datenschutz-, Pilot-, P0-08- oder C-14-Freigabe. Die Leckfreiheit ist nicht belegt.
