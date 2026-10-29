# Installierte EXE aus 64e1300 – Neubau, Installation und native Prüfung (28.10.2026)

Alle Bilder stammen per Win32 `PrintWindow(PW_RENDERFULLCONTENT)` aus dem Fenster der **installierten**
`%LOCALAPPDATA%\Lexono\Lexono.exe` (WebView2), gesteuert über CDP. Skript: `native_install_qa.py` (abgeleitet aus der
Prüfung von 6542754).

**Keine Sicherheits-, Datenschutz-, P0-08-, C-14- oder Pilotfreigabe; keine Freigabe für echte Mandantendaten.**

## 1. Bestandsaufnahme

**Git:**

- Branch `feature/feedback-diagnose`, HEAD `64e1300` = `origin/feature/feedback-diagnose`.
- `app/`, `scripts/`, `run.py`, `windows/`, `migrations/` ohne lokale Änderungen.

**Fremde Änderungen**, unverändert gelassen:

- `PRIVACY_REVIEW_ARCHITECTURE.md` (geändert);
- zwei gelöschte Logos unter `assets/branding/`;
- nicht versionierte `VORSCHLAG_*`- und Evidenzdateien.

**Vorher installiert:** Build aus `6542754`, Lexono.exe SHA-256 `25B33011…6684B896`. Es fehlten 5 Commits:

| Commit | Inhalt |
|---|---|
| `591b4d6` | Pop-up: alle drei Optionen bis 900 px Fensterhöhe, Kontext „in Ihrer Nachricht“ |
| `50a557c` | nur Doku |
| `02553d0` | ruhiges Annotationsdesign der Dokumentmarkierungen |
| `fde5c71` | Chat-Startseite nach Referenz: kompakte Prompt-Karten, breite Eingabe |
| `64e1300` | „Standard-Prompts verwalten“, max. vier Startseiten-Prompts, Migration `schritt3_039`; Sidebar-Einklappknopf 2 px |

**Nachweis am laufenden Programm** (`vorher-125prozent/native_vorher.json`):

- Die alte EXE lieferte `app.css` und `pseudonymization_center.js` byte-gleich zu `6542754` aus, nicht zu HEAD.
- `prompt_manager.js` → HTTP 404.
- Die Chat-Seite hatte weder `chat-panel--start` noch `data-prompt-manager-open`.

Es war also **kein** aktueller Build installiert, und eine Neuinstallation war nötig.

## 2. Tests (Quellstand 64e1300)

**171 Testdateien** (`testdateien_171.txt`, Log `tests_64e1300.log`): Chat, Startseite, Standard-Prompts,
Pop-up/Pseudonymisierung, Datenschutz/Privacy, Gateway, HITL, Freigabe, Review, Migrationen, Web-UI, Design.

| | |
|---|---|
| Bestanden | **4 930** |
| xfailed | 7 |
| Übersprungen | 10 |
| Fehlgeschlagen | **4**, ausschließlich bekannte Logo-Tests |

**Die 4 bekannten Fehler** (`test_design_refresh.py`), getrennt von neuen Fehlern:

- `test_full_logo_lockup_assets_have_a_real_alpha_channel`
- `test_dark_logo_icon_is_byte_identical_to_the_light_logo_icon`
- `test_dark_logo_has_identical_overall_geometry_to_light_logo`
- `test_dark_logo_wordmark_is_recolored_to_white`

Ursache: Die Logo-Dateien sind im Arbeitsbaum fremd gelöscht. Sie wurden bewusst nicht wiederhergestellt; der Build
verwendet sie nicht. **Neue Fehler: keine.**

**Ablauf:** Der Lauf in Blöcken zu je 12 Dateien wurde nach 12 von 15 Blöcken von Claude Code wegen Speichermangels
beendet. Die übrigen 27 Dateien liefen danach im Vordergrund nach, in zwei Teilen: 382 bestanden + 1 xfailed sowie
248 bestanden. Erst danach wurde installiert.

## 3. Sicherung und Build

**Sicherung vor der Installation:** `C:\Users\<Benutzer>\Lexono_backup_20261028_pre_install_64e1300\`

- Daten 920/920 Dateien, App 1015/1015 Dateien, jeweils per SHA-256 verglichen, 0 fehlend, 0 abweichend.
- Zusätzlich die oberste Ebene von `C:\ProgramData\Lexono` (`.env`, `.setup_complete`, `lexono.db`).
- DB-Kopie bytegleich, `quick_check` ok, Schema `schritt3_037`.
- 2 Benutzer, 151 Akten, 201 Dokumente, 129 Unterhaltungen, 511 Chatnachrichten, 26 Entscheidungen.
- Das vollständige Installationslog liegt in der Sicherung; hier eingecheckt ist ein Auszug mit neutralisierten Pfaden.

**Build** (`build_64e1300_auszug.log`):

| | |
|---|---|
| Quellcommit | `64e130018959efa28041c26acdf601c20ea940dd` |
| Zeitpunkt | 28.10.2026 14:39:22 – 14:48:14 |
| Werkzeuge | PyInstaller 6.22.2 (Python 3.13.15) + Inno Setup 6.7.3, beide Exitcode 0 |
| Installer | `dist\installer\Lexono_Setup.exe`, 556 740 795 B |
| SHA-256 Installer | `1A16996034765C30042E805C652F1CBED1D304163741F047299F48DDBA474839` |
| EXE | `dist\Lexono\Lexono.exe`, 35 117 541 B |
| SHA-256 Lexono.exe (gebaut = installiert) | `EC1CF4556C236E026AB3D4E651964F7A48404F4A02AA44EAAEAC9E27D1796E92` |

Ausgeführt wurden dieselben zwei Schritte wie `windows\build.ps1`, direkt aufgerufen; das Skript bricht im
Agenten-Kontext an der stderr-Behandlung ab.

**Vollständigkeit des Bundles gegen HEAD:**

- **App-Module** (`verify_new_exe.py`): 306/306 im Bundle identisch mit HEAD, darunter das neue
  `app.prompt_library.start_prompts`.
- **Nicht gebündelt** sind 10 Quellmodule (`app.workflow`, `app.second_opinion`, `app.synthetic_data`). Sie werden zur
  Laufzeit nicht importiert, nur vom Entwickler-Seed-Skript. Das war beim Build von 6542754 genauso.
- **Templates, Static, Migrationen** (`verify_bundle_data.ps1`): 143/143 Dateien identisch, 0 fehlend, darunter
  `static/js/prompt_manager.js`, `templates/partials/prompt_manager.html`, `app.css` und `schritt3_039`.
- **Abhängigkeiten:** `requirements*`, `pyproject.toml` und `windows/` sind seit 6542754 unverändert. Die
  „Hidden import not found“-Warnungen betreffen nur optionale Module (pycparser-Tabellen, tzdata, pysqlite2,
  MySQLdb).

## 4. Installation

- **Ablauf:** still (`/VERYSILENT`) in 80 s, „Installation process succeeded“ (`install_64e1300_auszug.log`). Vorher
  wurden der Installer-Hash geprüft und sichergestellt, dass kein Lexono-Prozess lief.
- **Ergebnis:** Die installierte `Lexono.exe` ist hashgleich mit dem Build (`EC1CF455…`).
- **Exitcode im Log:** „Process exit code: 2147747880“ (0x80040828) stammt vom mitgelieferten WebView2-Bootstrapper,
  weil die Runtime bereits vorhanden ist. Die Installation von 6542754 zeigte denselben Code.

## 5. Native Prüfung der installierten EXE (nur synthetische Daten)

**Testaufbau:**

- Isolierte Testablage mit eigener SQLite-DB (`LEXONO_DATA_DIR` im Job-Ordner), eigener Port 8066. Die produktive App
  nutzt 8062, dadurch eigener WebView2-Origin.
- Wegwerfkonten `qa-native@lexono.test` (Admin) und `qa-mitarbeiter@lexono.test`.
- Dokumente `QA-SYNTH-*`, zur Laufzeit erzeugt.
- Cloud = kontrollierter Test-Double (`qa_recorder.py` auf 127.0.0.1:8765, `LEXONO_QA_LOOPBACK_CLOUD_ENDPOINT=1`,
  ungültiger Testschlüssel). **Kein echter Cloud-E2E.**

**Ausgelieferter Code** (`nachher-*/native_nachher.json` → `ausgelieferter_code`):

- `app.css`, `pseudonymization_center.js` und `prompt_manager.js` sind **byte-gleich zu HEAD 64e1300** und nicht mehr
  zu 6542754.
- Die Chat-Seite enthält `chat-panel--start`, `data-prompt-manager-open`, das Modal `#prompt-manager`,
  `prompt_manager.js` und die Suchleiste `data-command-bar-open`.
- Die Testablage wurde von der installierten EXE auf **`schritt3_039`** migriert. Die vier Startkarten wurden als
  aktivierte Prompts übernommen.

**Größen und Skalierung:** Der Bildschirm hat 1920×1080 bei 125 % Windows-Skalierung. Deshalb gibt es zwei Läufe:

| Ordner | Skalierung | Viewports (CSS-px) |
|---|---|---|
| `nachher-125prozent/` (71 Aufnahmen, alle Abläufe) | echt (DPR 1,25) | „1920×1080“ = maximiert 1540×844 (realer Bildschirm), 1280×720, 960×600 |
| `nachher-dpr1/` (50 Aufnahmen) | `--force-device-scale-factor=1` | 1920×1055 (Bildschirm minus Titelleiste), 1280×720; 960×600 nicht erreichbar (Mindestfenster bei DPR 1 = 1107×703, aufgenommen als `960x600-*`) |

Jeweils hell und dunkel. Pro Größe:

- `01` Chat-Startseite
- `02` Standard-Prompts verwalten
- `03` Formular „Neuer Prompt“
- `04` Chatverlauf mit Eingabe
- `05`–`08` Pseudonymisierungs-Pop-up: erste Rückfrage mit Markierungen, Rückfrage 2 von 5, mehrere Dokumente, einzelne Rückfrage

**Ergebnisse** (Messwerte aus dem DOM des nativen Fensters):

| Prüfung | Ergebnis |
|---|---|
| Chat-Layout / Startseite | in allen Größen: 4 Startkarten, keine Überlappung mit der Eingabe, kein Querscrollen, keine JS-Fehler |
| Zentrierte Chat-Eingabe | horizontale Abweichung der Eingabe von der Mitte des Chatbereichs **0 px** in allen Startseiten- und Verlaufsaufnahmen beider Läufe; Eingabe immer im Bild |
| Suchleiste | sichtbar und vollständig im Bild in allen Größen |
| Standard-Prompts-Modal | „4 von 4 aktiv“, leerer rechter Zustand, Formular „Neuen Prompt erstellen“; „Abbrechen“ und „Speichern“ in allen Größen sichtbar, kein Querscrollen |
| Prompt deaktivieren + speichern | Startseite zeigt danach nur noch die 3 aktivierten (`11`–`13`); wieder aktivieren → 4 Karten (`14`; reaktivierter Prompt am Ende, wie vorgesehen) |
| Rolle Mitarbeiter | Speichern der Prompt-Verwaltung → **HTTP 403**, Stand unverändert |
| Dokumentmarkierungen | im Pop-up markiert (9 Fundstellen im Hauptdokument, 4 im Einzelvermerk), neues Annotationsdesign |
| Pop-up-Abläufe (125 %) | alle drei Optionen (`20`–`22`), Navigation (`23`, `24`), DOCX-Wechsel (`25`), Original/Ergebnis (`26`, `27`); Abbrechen ohne DB-Änderung und ohne Recorder-Anfrage (`29`) |
| Chat-Rückfrage beim Senden | Pop-up mit „Richter“, vor der Entscheidung 0 Anfragen; Abbrechen ändert nichts; nach Bestätigen genau 1 Anfrage, „Richter“ 0× im Payload, Antwort sichtbar (`30`–`33`) |
| Dokument-Gate | 5 Rückfragen beantwortet, Entscheidungen +5, Freigaben 0 → 1, genau 1 Anfrage, **0 Lecks** für alle Originalwerte, „Kaufmann“ wie entschieden lesbar (`40`–`42`) |
| Serverseitige Prüfungen | offene Rückfragen, veralteter Textstand, fremder Begriff und fremdes Dokument werden abgelehnt, DB und Recorder bleiben unverändert; Mitarbeiter → HTTP 403 |
| JavaScript-Fehler | keine |

## 6. Produktive Datenbank

`C:\ProgramData\Lexono\data\kanzlei_ai.db`:

- SHA-256 `DFD2B763…87EFDAB4` vor der Sicherung, nach der Installation, nach allen drei nativen Läufen und am Ende
  identisch.
- Letzte Änderung 26.10.2026 20:53:15, kein WAL und kein Journal. Es wurde nichts verändert oder gelöscht.
- **Beim nächsten Start der produktiven App durch den Nutzer** migriert die neue Version die DB von `schritt3_037` über
  `038` (`read_at`, Posteingang) auf `039` (Startseiten-Prompts). Das war nicht Teil dieser QA; die Sicherung oben
  deckt den Stand davor ab.

## Offene Probleme

1. **Mehrdokument-Pop-up im maximierten Fenster bei 125 %** (1540×844, hell und dunkel,
   `nachher-125prozent/1920x1080-*-07-…`): Die dritte Option „Unsicher“ liegt teilweise unter dem Panelrand
   (`optionen_sichtbar = 2`).
   - Erreichbar ist sie über das Scrollen des Panels.
   - Bei 1280×720 und 960×600 (125 %) sowie bei allen DPR-1-Größen sind alle drei Optionen sichtbar.
   - Ursache: Die Dokument-Tab-Leiste kostet Höhe. Die Kompaktstufe aus `591b4d6` deckt diese Kombination im nativen
     Fenster nicht vollständig ab.
   - **Nicht behoben**, weil der Auftrag Build und Installation war; eine Korrektur erfordert erneut Build und
     Installation.
2. Kein echter Cloud-E2E (Antwortqualität, Latenz und Gateway-Relay der echten Cloud nicht geprüft).
3. Nur ein Monitor (1920×1080, 125 %). 1920×1080 als CSS-Viewport ist auf diesem Gerät nicht erreichbar.
4. 4 bekannte Logo-Testfehler bleiben bestehen, solange die Logos im Arbeitsbaum fehlen (fremde Änderung).
