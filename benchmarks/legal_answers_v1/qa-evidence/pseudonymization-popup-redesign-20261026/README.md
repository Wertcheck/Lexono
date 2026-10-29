# Pseudonymisierungs-Pop-up – UX-Überarbeitung, visuelle Prüfung (26.10.2026)

Geprüft wurde die laufende Entwicklungsinstanz (uvicorn) mit dem Arbeitsstand dieses Commits. Die installierte EXE ist
damit noch **nicht** aktualisiert. Das ist kein Cloud-E2E-Test.

**Referenzbild:** Im Auftrag war ein Referenzbild genannt, es lag aber nicht vor. Gesucht wurde im Anhang, unter
`assets/ux-ui/` und in allen Nutzerordnern; gefunden wurden nur Dashboard-, Login- und Website-Vorlagen. Auf Rückfrage
hat der Nutzer festgelegt: „Dann mach einen eigenen Screenshot von der Anwendung.“ Als Referenz dienen deshalb
Screenshots der laufenden Lexono-Oberfläche:

- `00a-referenz-anwendung-chat-start.png`
- `00b-referenz-anwendung-chat-mit-dokumenten.png`

Dazu kommen die gemessenen Stilwerte (computed styles) in `referenz_styles.json`, zum Vergleich mit
`00c-popup-im-vergleich.png`.

## Abgleich mit der Referenz (laufende App) und Korrekturen

| Merkmal | App (Referenz) | Pop-up vorher | Korrektur |
|---|---|---|---|
| Schrift | Inter überall | Antwort-Buttons, Dokument- und Ansichts-Tabs in **Arial** (Buttons erbten die Schrift nicht) | `font-family: var(--font-body)` für Buttons, Auswahl und Eingabe im Pop-up |
| Radius Buttons | `--radius-sm` 6 px | 7–8 px | `var(--radius-sm)` |
| Radius Karten/Dialog | `--radius-md` 10 px | Dialog 14 px | `var(--radius-md)` |
| Schatten | `--shadow-sm` | eigener Wert | `var(--shadow-sm)` |
| Aktiver Eintrag | Sidebar: grüne Tonfläche, dunkelgrüne Schrift | Tab weiß mit grünem Rand | aktiver Dokument-Tab wie der aktive Sidebar-Eintrag |
| Primär-/Sekundär-Button | grün `#249d74` / weiß mit Rand, 12,5 px, 600 | identisch (`.btn`, `.btn--ghost`) | keine |

Nach der Korrektur messen alle Pop-up-Elemente Inter und die App-Radien (`referenz_styles.json`, Abschnitt `popup`).
Danach lief die vollständige Prüfung erneut; die Screenshots unten stammen aus diesem letzten Lauf.

## Testinstanz

- Port 8063, `LEXONO_DATA_DIR` im temporären Job-Ordner.
- Kopie einer früheren QA-Datenbank mit synthetischem Admin `qa-install@lexono.test`, Modus `auto_review`.
- Cloud-Endpunkt `127.0.0.1:9` (tot), lokale KI aus.
- Headless Edge über CDP.
- Im Serverprotokoll: 0 ERROR-Zeilen, kein Traceback.
- Nur synthetische Dokumente (`QA-SYNTH-*`). Die produktive Datenbank wurde nicht berührt.

Skript: `ux_redesign.py`, nur im temporären Job-Ordner und bewusst nicht eingecheckt, weil es die Zugangsdaten des
Wegwerf-Testkontos enthält. Die Messwerte je Screenshot stehen in `ux_redesign.json`, unter anderem:

- horizontale Scrollbar;
- überlaufende Bereiche;
- Sichtbarkeit von Fußleiste, Bestätigen und Abbrechen;
- Fragen, Fortschritt und Seite.

## Ergebnis

In **allen** Pop-up-Screenshots gilt:

- keine horizontale Scrollbar, weder auf der Seite noch im Dialog;
- kein überlaufender Bereich;
- Fußleiste, „Abbrechen“ und „Bestätigen“ liegen vollständig im Fenster, auch bei 1280×720 und 960×600.

| Nr. | Fenster | Prüfung | Ergebnis |
|---|---|---|---|
| 01 | 1920×1080 | Chat mit Sperrhinweis | Chat bleibt; Button „Pseudonymisierung prüfen“ |
| 02 | 1920×1080 | Pop-up, 2 Dokumente (PDF + DOCX) | Tabs mit Typ und offenen Rückfragen; erste offene Frage aktiv; Markierung an der Fundstelle |
| 03 | 1920×1080 | Seitenübersicht | 3 Miniaturen, aktuelle Seite hervorgehoben, Badge für offene Rückfragen |
| 04/05 | 1920×1080 | Antwort → nächste offene Frage im DOCX | automatischer Dokumentwechsel; Tabelle mit Rahmen; Fortschritt 1 von 2 (50 %) |
| 06/07 | 1920×1080 | Original / Pseudonymisiertes Ergebnis | Ansichtswechsel ohne Verlust der Entscheidungen |
| 08 | 1920×1080 | Vorschau maximiert | Prüfpanel ausgeblendet, Fußleiste bleibt |
| 09/10 | 1920×1080 | Bestätigungssperre | gesperrt bei 1 offenen Frage; aktiv nach 2 von 2 (100 %) |
| 11 | 1920×1080 | Abbrechen | zurück im Chat; Entscheidungen und Freigaben in der DB unverändert (6 Entscheidungen / 0 Freigaben vorher und nachher; die 6 stammen aus früheren Läufen derselben Test-DB) |
| 12/13 | 1280×720 | Pop-up + Panel unabhängig gescrollt | vollständig bedienbar, Vorschau bleibt stehen |
| 14/15 | 1600×1000, 1280×720 | 6 Dokumente, 9 Rückfragen | Tabs scrollbar + Auswahlliste; Karten kompakt („in 4 Dokumenten“) |
| 16/17 | 1280×720 | Fortschritt 3 von 9 (33 %), „Nächste offene“ | Sprung zur nächsten offenen Frage samt Fundstelle |
| 18 | 1280×720 | Auswahlliste → Anlage 4 | Dokumentwechsel; 3 Entscheidungen bleiben erhalten |
| 19–22 | 1280×720 | 70 Seiten | in ca. 11 s geprüft; Sprung zu Seite 65; Miniaturen (70); Seite 65 bleibt beim Einblenden erhalten; Miniatur → Seite 3; Zoom 140 % |
| 23/24 | 1600×1000 | Prüfansicht ohne Auftrag | Namensschutz und Schnellaktionen; Schnellaktion belegt nur das Eingabefeld vor, DB unverändert (0 neue Nachrichten) |
| 24b/24c | 1600×1000 | Rückfrage beim Senden („Ruecksicht“) | im selben Pop-up, „aus Ihrer Nachricht im Chat“, Kontext aufgeklappt; Abbrechen → Blase „Rückfrage öffnen“, DB unverändert |
| 25 | 960×600 | wenig Platz | Ansichten in eigener Zeile, Fußleistentext in eigener Zeile, kein Abschneiden |
| 26 | 1600×1000 | Dark Mode | lesbar, Seiten weiß |
| 27 | 1280×720 | alle beantwortet, Button „Pseudonymisierung bestätigen und fortfahren“ aktiv | Hinweis links: „Danach: Schriftsatz erstellen.“ |
| 28 | 1280×720 | Bestätigung läuft | Abbrechen, Schließen und Bestätigen gesperrt; „der gesamte Inhalt wird erneut geprüft“ |
| 29 | 1280×720 | nach Bestätigung | zurück im Chat; Entscheidungen gespeichert (6 → 8); Wiederaufnahme bis zum Cloud-Aufruf, der am toten Endpunkt endet („technische Gründe, keine Datenschutz-Blockierung“) – **kein E2E** |
| 30/31 | 1600×1000 | Chat-Rückfrage über „Rückfrage öffnen“ wieder geöffnet, beantwortet, bestätigt | Entscheidung gespeichert (+1), Nachricht fortgesetzt bis zum toten Endpunkt |

## Bekannte Einschränkungen

- Der Vergleich mit dem Referenzbild fehlt, siehe oben.
- Geprüft wurde nur die Entwicklungsinstanz; Build und Installation der EXE sind nicht Teil dieser Prüfung.
- „Ruecksicht“ als Rückfrage ist ein bekannter Fehlalarm der Erkennung und hier nur Testauslöser.
- In der Prüfansicht wurde „Rücksprache“ als Person maskiert, ebenfalls ein Erkennungsverhalten. Die
  Datenschutzlogik ist unverändert und nicht Gegenstand dieser Überarbeitung.
- Unter 900 px Breite wird die Legende ausgeblendet; die Markierungen tragen weiterhin Tooltips.

Diese Prüfung ist keine Datenschutz-, Pilot-, P0-08- oder C-14-Freigabe. Die Leckfreiheit ist nicht belegt.
