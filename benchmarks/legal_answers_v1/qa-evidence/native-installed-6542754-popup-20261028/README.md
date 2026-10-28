# Installierte EXE aus 6542754 – Pseudonymisierungs-Pop-up, native Prüfung (28.10.2026)

Alle Bilder wurden mit Win32 `PrintWindow(PW_RENDERFULLCONTENT)` direkt aus dem Fenster der **installierten**
`%LOCALAPPDATA%\Lexono\Lexono.exe` aufgenommen (WebView2). Es handelt sich nicht um Browser-Screenshots. Die Steuerung lief
über CDP (`WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port`). Skript: `native_popup_qa.py`.

**Keine Sicherheits-, Datenschutz-, P0-08-, C-14- oder Pilotfreigabe; keine Freigabe für echte Mandantendaten.**

## Build und Installation

| | |
|---|---|
| Quellcommit | `6542754` (in `origin/feature/feedback-diagnose` enthalten); `app/`, `scripts/`, `run.py`, `windows/`, `migrations/` ohne lokale Änderungen |
| Build | PyInstaller 6.22.2 + Inno Setup 6, Auszug `build_6542754_auszug.log` |
| Installer | `dist\installer\Lexono_Setup.exe`, 28.10.2026 07:53:52, 556 723 044 B |
| SHA-256 Installer | `97A478A2B90467EC74F971A747CF9C9B5A7096D8C697B76D945CF22597E8A8D4` |
| SHA-256 Lexono.exe (gebaut = installiert) | `25B330117F5ADC49F546E720340AE01DA8C312E56C957496D75F56A76684B896` |
| Vorher installiert (3e6aa98) | `81C9ED788048C7D6AEAF30CFE81A3D33E90E0DA1D97E265645DBF5D393F53235` |
| Bundle gegen HEAD | 305/305 App-Module identisch, 140/140 Templates/Static/Migrationen identisch |
| Installation | still (`/VERYSILENT`), Exitcode 0, 81 s, „Installation process succeeded“ |

**Nachweis im Programm:** Die installierte EXE liefert `pseudonymization_center.js` und `app.css` byte-gleich zu `6542754`
aus. Die neuen Marker (`data-pseudo-q-apply`, `pseudo-question__found`) sind vorhanden (`nachher-*/native_nachher.json` →
`ausgelieferter_code`).

**Build-Hinweis:** `windows\build.ps1` brach im Agenten-Kontext an der PowerShell-Fehlerbehandlung ab (stderr von
PyInstaller wird dort zum Fehler). Ausgeführt wurden deshalb dieselben zwei Schritte direkt:

```
pyinstaller windows\lexono.spec --distpath dist --workpath build --noconfirm
ISCC windows\installer.iss
```

## Sicherung und produktive Datenbank

**Sicherung vor der Installation:** `C:\Users\<Benutzer>\Lexono_backup_20261028_pre_install_6542754\`

- Daten 920/920 Dateien, App 1013/1013 Dateien, EXE-Kopie = 3e6aa98.
- Die DB-Kopie ist bytegleich, `quick_check` ergibt ok, Schema `schritt3_037`.
- 2 Benutzer, 151 Akten, 201 Dokumente, 511 Chatnachrichten, 26 Entscheidungen.
- Installationslog `install_6542754.log` liegt in der Sicherung.

**Produktive DB** `C:\ProgramData\Lexono\data\kanzlei_ai.db`: SHA-256 `DFD2B763…FDAB4` vor der Sicherung, nach der
Installation, nach jedem der vier nativen Läufe und am Ende identisch. Letzte Änderung 26.10.2026 20:53:15, kein WAL und
kein Journal.

- Alle Testdaten lagen in getrennten Testablagen mit eigener SQLite-DB (`LEXONO_DATA_DIR` im Job-Ordner).
- Das Programm lief auf eigenem Port **8066**, der Produktivbetrieb nutzt 8062. Dadurch gibt es einen eigenen
  WebView2-Origin: Theme-Einstellung und Sitzung der produktiven App werden nicht berührt.
- **Beim nächsten Start der produktiven App** migriert 6542754 die produktive DB von `schritt3_037` auf
  `schritt3_038`. Bestehende Nachrichten erhalten `read_at` und gelten als gelesen (Posteingang). Das ist durch diese
  QA nicht ausgeführt worden; die Sicherung oben deckt den Stand davor ab.

## Testaufbau (nur synthetische Daten)

- **Konten:** Admin `qa-native@lexono.test` und Mitarbeiter `qa-mitarbeiter@lexono.test`, beide Wegwerfkonten in der
  Testablage.
- **Dokumente** (zur Laufzeit erzeugt, `QA-SYNTH-*`):
  - PDF mit 3 Seiten und 5 Rückfragen;
  - Mehrdokument-Fall: PDF, DOCX und Anlage-PDF;
  - Einzelfall: PDF mit 1 Rückfrage.
- **Cloud = kontrollierter Test-Double:** lokaler Aufzeichnungsserver `qa_recorder.py` (127.0.0.1:8765):
  - nutzt den im Code vorgesehenen Prüfweg `LEXONO_QA_LOOPBACK_CLOUD_ENDPOINT=1`;
  - ungültiger Testschlüssel `sk-ant-qa-invalid-…`, Gateway-Pfad aus, Proxy für alles andere tot.
  - **Kein echter Cloud-E2E-Test.** Produktive Zugangsdaten wurden nicht verwendet. Die Recorder-Rohdaten werden nicht
    eingecheckt.

## Größen und Skalierung

Der Bildschirm hat 1920×1080 Pixel bei 125 % Windows-Skalierung. Ein CSS-Viewport von 1920×1080 ist dort physikalisch
nicht erreichbar. Deshalb gibt es zwei Läufe je Phase:

| Ordner | Skalierung | Viewports (CSS-px) |
|---|---|---|
| `*-125prozent/` | echte Einstellung (DPR 1,25) | maximiert 1536×842 (= realer 1920×1080-Bildschirm), 1280×720, 960×600 |
| `*-dpr1/` | `--force-device-scale-factor=1` | 1920×1055 (größtes Fenster: Bildschirm minus Titelleiste), 1280×720; 960×600 nicht erreichbar, weil die Mindestfenstergröße bei DPR 1 1107×703 ergibt |

Jeweils hell und dunkel (Theme über die App-eigene Einstellung `lexono_theme`).

## Ergebnisse installierte EXE 6542754 (`nachher-dpr1/`, `nachher-125prozent/`, je 42 Aufnahmen)

**Abläufe**, in beiden Läufen identisch bestanden:

| Prüfung | Ergebnis |
|---|---|
| Neue Oberfläche | Eine Rückfrage (`sichtbare_fragekarten = 1` in allen Pop-up-Aufnahmen), Miniaturen, Kategorie-Legende, Fundkarte, Radio-Optionen, „Übernehmen und nächste Rückfrage“ |
| Eine / mehrere / letzte Rückfrage | `10-…` (1 von 1), `01`/`02` („Rückfrage 2 von 5“), `08` (3 Dokument-Tabs mit „5 offen“ / „1 offen“ / „✓ geprüft“), `28` (5 von 5, Bestätigen aktiv) |
| Alle drei Optionen | `20` Pseudonymisieren übernommen, `21` Nicht pseudonymisieren gewählt, `22` Unsicher gewählt |
| Navigation | `23` Vorherige (Auswahl „unsicher“ erhalten), `24` nächste offene, `25` Dokumentwechsel zur DOCX, `26`/`27` Original/Ergebnis |
| Abbrechen im Dokument-Pop-up | Entscheidungen/Freigaben/Nachrichten unverändert (0/0/11 → 0/0/11), 0 Recorder-Anfragen, Dialog zu |
| **Echte Chat-Rückfrage beim Senden** | Nachricht im nativen Fenster gesendet. Der Server lieferte das review-Ereignis, das Pop-up öffnete mit „Richter“ (`30`), 0 Recorder-Anfragen vor der Entscheidung. |
| Abbrechen der Chat-Rückfrage | Mit offener Rückfrage und nach Abbrechen identisch (0/0/12), 0 Recorder-Anfragen. „Rückfrage öffnen“ bleibt im Chat (`31`). |
| Chat-Rückfrage beantworten + bestätigen | Erneut geöffnet, „Pseudonymisieren“, Bestätigen. Entscheidungen 0 → 1, genau 1 Anfrage an `/v1/messages`, „Richter“ 0× im Payload, Antwort im Chat sichtbar (`32`, `33`). |
| **Dokument-Gate: alle Pflichtfragen + Bestätigen** | Beantwortet mit personal / not_personal / unsure / personal / personal (`40`). Danach Entscheidungen +5, Freigaben 0 → 1, genau 1 Recorder-Anfrage. **0 Lecks** für Max Bauer, Lindenweg 4, 50667 Koeln, Nordbauholding, max.bauer@example.org, Richter, Meister, Gärtner, Kellner. „Kaufmann“ erscheint 1× im Payload, wie entschieden (nicht pseudonymisieren). Auftrag fortgesetzt, Schriftsatz im Chat (`41`, `42`). |
| JavaScript-Fehler | keine |

**Serverseitige Validierung, fail-closed und Rolle** (HTTP gegen dieselbe installierte Instanz, `serverpruefungen`):

| Versuch | Antwort | DB / Recorder |
|---|---|---|
| Bestätigen mit offenen Rückfragen (`require_resolved=1`) | „Bitte zuerst alle offenen Rückfragen … beantworten.“ | unverändert / 0 |
| Veralteter Textstand | „Der Dokumenttext hat sich seit der Anzeige geändert …“ | unverändert / 0 |
| Entscheidung zu nicht offenem Begriff | „Mindestens eine Rückfrage ist nicht mehr aktuell …“ | unverändert / 0 |
| Fremdes Dokument | „Dieses Dokument gehört nicht zu dieser Unterhaltung.“ | unverändert / 0 |
| Konto mit Rolle **Mitarbeiter** | **HTTP 403** | unverändert |

Fail-closed bei einer fehlgeschlagenen Datenschutzprüfung ist durch Tests abgedeckt (z. B.
`test_s79_privacy_check_failure.py`, `test_privacy_cloud_boundary_e2e.py`), in der installierten EXE aber nicht
provoziert worden.

**Messwerte** in `nachher-*/native_nachher.json`; Vorher-Läufe der alten EXE 3e6aa98 in `vorher-*/`, je 23 Aufnahmen.
Die alte EXE zeigt das bisherige Pop-up mit der Liste aller Rückfragen, ihre Chat-Rückfrage lief ebenfalls über den
Test-Double.

## Befunde der nativen Prüfung

1. **„Unsicher“ teilweise unter dem Panelrand.** Betroffen ist die installierte EXE 6542754 in drei Fällen
   (`optionen_sichtbar = 2` bzw. `1` in den JSON-Dateien):
   - im maximierten Fenster bei 125 % (1536×842);
   - bei 1280×720 im Mehrdokument-Fall, einschließlich der Abläufe `20`–`28`;
   - bei 1107×703 (DPR 1).

   Bei 960×600 (125 %) sind alle drei Optionen sichtbar.
   - **Ursache:** Das native WebView2-Fenster zeigt Bildlaufleisten, die headless Browser-QA blendete sie aus. Für
     Fensterhöhen zwischen 761 und 900 px gab es außerdem keine Kompaktstufe.
   - **Erreichbarkeit:** Die Option ist über das Scrollen des Panels erreichbar.
   - **Im Quellstand behoben** (nach 6542754, eigener Commit):
     - Kompaktstufe bis 900 px Höhe;
     - Optionshinweise unter 900 px einzeilig;
     - schmal und niedrig: Ansichten in der Titelzeile, ohne Kontextüberschrift und Fußzeilenhinweis.
   - **Nachweis:** `quellstand-korrektur/`, headless **mit** Bildlaufleisten bei 1920×1055, 1536×842, 1280×720,
     1107×703 und 960×600, hell und dunkel. In 72/72 Pop-up-Zuständen sind alle drei Optionen sichtbar.
   - **Nicht in der installierten EXE.** Dafür sind ein neuer Build und eine neue Installation nötig.
2. **Kontext-Überschrift bei der Chat-Rückfrage.** Ist der Begriff zusätzlich in einem angehängten Dokument markiert,
   stand „Kontext im Dokument“, obwohl der Ausschnitt aus der Chat-Nachricht stammt (`30`). Im Quellstand behoben
   („Kontext in Ihrer Nachricht“, Test ergänzt), **nicht in der installierten EXE**.
3. **Beobachtung, kein Fehler:** Nach der Chat-Entscheidung „Richter → pseudonymisieren“ ging der Inhalt des
   angehängten Vermerks **pseudonymisiert** mit. Rohwerte waren 0× enthalten.
   - **Ursache:** Freigabemodus `auto_review`. Die Entscheidung gilt aktenweit, damit hat das Dokument keine offene
     Rückfrage mehr und gilt als `released_auto` (in der Testablage nachgeprüft).
   - Die alte EXE verhielt sich identisch.
   - Ob das fachlich so gewollt ist, entscheidet der Owner.

## Tests (Quellstand 6542754, `tests_6542754.log`)

- **165 Testdateien:** Pop-up, Chat, HITL, Freigabe, Datenschutz/Privacy, Gateway, Review, Web-UI.
- **Ergebnis:** 4675 bestanden, 7 xfailed, 10 übersprungen, **4 fehlgeschlagen**.
- **Die 4 Fehler** sind ausschließlich die bekannten Logo-Tests in `test_design_refresh.py`:
  - `test_full_logo_lockup_assets_have_a_real_alpha_channel`
  - `test_dark_logo_icon_is_byte_identical_to_the_light_logo_icon`
  - `test_dark_logo_has_identical_overall_geometry_to_light_logo`
  - `test_dark_logo_wordmark_is_recolored_to_white`

  Ursache: Die Logo-Dateien unter `assets/branding/` sind im Arbeitsbaum fremd gelöscht. Sie wurden bewusst nicht
  wiederhergestellt, und der Build verwendet sie nicht.
- **Nach den Quellstand-Korrekturen:** Pop-up, Chat, Privacy-Review, Design, Kanzleiwissen und Posteingang mit 284
  bestanden, 4 fehlgeschlagen (dieselben Logo-Tests).

## Offene Einschränkungen

- Die Korrekturen 1 und 2 sind nicht installiert.
- Kein echter Cloud-E2E: Antwortqualität, Latenz, Fehlerfälle und Gateway-Relay der echten Cloud sind nicht geprüft.
- Nur ein Monitor (1920×1080, 125 %); andere Skalierungen und Mehrmonitorbetrieb sind nicht geprüft.
- 1920×1080 als CSS-Viewport ist auf diesem Gerät nicht erreichbar (siehe „Größen und Skalierung“).
- Die Migration der produktiven DB auf `schritt3_038` erfolgt erst beim nächsten Start der App durch den Nutzer.
