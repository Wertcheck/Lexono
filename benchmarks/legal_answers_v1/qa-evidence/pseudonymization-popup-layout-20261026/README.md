# Pseudonymisierungs-Pop-up – Dokumentdarstellung, Seitenumbruch, Rückfragen-Panel (26.10.2026)

Geprüft wurde die Entwicklungsinstanz (uvicorn) mit dem Arbeitsstand dieses Commits. Die installierte EXE ist nicht
aktualisiert. Das ist kein Cloud-E2E-Test.

Ein neuer Screenshot lag dem Auftrag nicht bei. Grundlage waren das beschriebene Symptom („erste Seite nahezu leer,
Text erst auf der Folgeseite“) und eigene Screenshots der laufenden Anwendung.

## Tatsächliche Fenstermaße der Anwendung

- `run.py` öffnet das App-Fenster mit 1400×900, Mindestgröße 900×600.
- Der Bildschirm dieses Rechners hat 1536×864 logische Pixel (Windows-Skalierung 125 %).
- Der CSS-Viewport der installierten App liegt damit bei etwa 1400×830.
- Geprüft wurden 1400×830, 1920×1080, 1600×1000, 1280×720 und 960×600. Größen aus einem verkleinerten
  Handy-Screenshot wurden nicht übernommen.

## Seitenumbruch: Ursache und Korrektur

Reproduziert mit synthetischen DOCX-Varianten. Die Bilder liegen in `seitenumbruch-reproduktion/` (vorher/nachher).

**Ursache (Lexonos lokaler DOCX-Seitensatz, `app/documents/docx_layout.py`):** `pymupdf.Story` teilt eine
Tabellen**zeile** nicht über Seiten.

| Variante | vorher | nachher |
|---|---|---|
| Briefvorlage, ganzer Inhalt in einer einzelligen Layouttabelle | Seite 1 nur die Überschrift (Text bis y=100 von 792), alles auf Seite 2 geschoben; **15 von 32 Absätzen fehlten** in der Seitenansicht | Seite 1 gefüllt (bis y=716), 3 Seiten, 0 Absätze fehlen |
| Text, danach eine hohe zweispaltige Tabellenzeile | Seite 1 halb leer (bis y=480), Zeile komplett auf Seite 2 | Seite 1 gefüllt (bis y=707), 0 fehlen; Hinweis „untereinander dargestellt“ |
| Manueller Seitenumbruch (`w:br type=page`) | ignoriert (1 Seite) | neue Seite (2 Seiten), Kopfzeile auch auf Seite 2 |
| Weicher Zeilenumbruch (Umschalt+Enter, Anschriftblock) | Zeilen zusammengezogen | Zeilen erhalten |
| Lange Tabelle (45 Zeilen) | korrekt (Umbruch zwischen Zeilen) | unverändert korrekt |

**Korrekturen:**

- Zeilen, die höher sind als ein Teil des Satzspiegels, werden als umrandete Blöcke gesetzt. Die Schwelle liegt bei
  25 % für einzellige und 50 % für mehrspaltige Zeilen. Blöcke teilt der Satz verlustfrei über Seiten.
  - Mehrspaltige Zeilen stehen dann untereinander. Das meldet die Prüfansicht unter „Hinweise zur Darstellung“.
  - Kurze Tabellen bleiben nebeneinander, mit Rahmen.
- Manuelle Seitenumbrüche, „Seitenumbruch oberhalb“ und Abschnittswechsel beginnen eine neue Seite.
- Weiche Zeilenumbrüche und Tabulatoren bleiben erhalten.

**Zweiter Befund: Ligaturen.** Der Satz setzt „ff“ als Ligatur („Betreﬀ“, „Hoﬀmann“).

- Vorher fand die Phrasensuche „Steffen Hoffmann“ 0-mal; der Name blieb in der Vorschau unmarkiert. Nachher: 1-mal.
- Wort- und Phrasensuche lösen Ligaturen jetzt auf (`pseudonymization_review._text_flags`). Das gilt auch für PDFs.
- Im Brief-Screenshot sind 00d–00f markiert: „Steffen Hoffmann“ auf Seite 2 und „Richter“ auf Seite 1.

Die Datenschutzprüfung selbst ist nicht betroffen, weil sie auf dem extrahierten Text arbeitet. Die Fehler betrafen
nur Seitenansicht und Markierung. Gateway, Revalidierung und Fail-closed sind unverändert.

**PDF:** Das Seitenbild ist das Original, Umbrüche sind korrekt. Der Eindruck „Seite 1 fast leer“ entstand dort durch
die Startposition: Die Markierung wurde zentriert, sichtbar war also vor allem der leere untere Teil der Vorseite.
Jetzt beginnt die Ansicht am Anfang der Seite mit der Fundstelle. In den Screenshots 02/12 steht oben Seite 2, die
Seite mit „Richter“.

## Weitere Änderungen an der Oberfläche

- **Vorschau größer:**
  - Die Seitenbreite folgt der verfügbaren Vorschaubreite, statt bei 900 px fest zu enden. Gemessen 950/982 px bei
    1400×830 und 1062/1094 px bei 1600×1000.
  - Maximiert sind es bis 1100 px × Zoom.
  - Die Auflösung passt sich der angezeigten Breite und der Bildschirmskalierung an (96–240 dpi).
  - Geringere Leerflächen; nach einer Größenänderung des Fensters wird neu bemessen.
- **Rückfragen-Panel:**
  - Nur die aktive Frage ist ausführlich, mit getrennten Abschnitten: **Fund** (Begriff, Kategorie,
    Dokument/Seite) → **Frage** und Antworten (untereinander, mit Erklärung) → **Kontext** (aufklappbar).
  - Alle anderen Fragen sind kompakte Zeilen mit Begriff und Status; ein Klick öffnet sie und springt zur Fundstelle.
  - Navigation über „Vorherige“ und „Nächste offene“.
- **Niedrige Fenster:**
  - Bis 760 px Höhe stehen die Erklärungen der Antworten im Tooltip.
  - Bis 640 px Höhe (App-Minimum 600) entfallen Einleitung, Hinweiszeile der Fußleiste und Panel-Überschrift; diese
    Hinweise stehen im Tooltip von „Bestätigen“. Die Ansichten rücken in die Titelzeile.
  - In allen 30 Pop-up-Zuständen sind die Antworten der aktiven Frage vollständig sichtbar
    (`antworten_sichtbar` in `ux_redesign.json`), auch bei 960×600 und 1280×720.

## Ergebnis der visuellen Prüfung (30 Pop-up-Zustände)

- Nirgends horizontale Scrollbar oder überlaufende Bereiche; Fußleiste, Abbrechen und Bestätigen immer sichtbar.
- **Entscheidungen bleiben erhalten:**
  - nach Dokumentwechsel und Ansichtswechsel: „Richter“ steht weiter auf „pseudonymisiert“;
  - nach Wechsel über die Auswahlliste: 3 von 9 bleiben entschieden.
- **Abbrechen und Schnellaktion:** keine DB-Änderung (15 Entscheidungen / 0 Freigaben vorher und nachher; der Bestand
  stammt aus früheren Läufen derselben Test-DB).
- **Bestätigen:**
  - am Dokument: Entscheidungen 15 → 17;
  - Chat-Rückfrage: 17 → 18.
  - Danach läuft jeweils die Wiederaufnahme bis zum toten Cloud-Endpunkt.
- Serverprotokoll: 0 ERROR-Zeilen, kein Traceback.

| Nr. | Fenster | Inhalt |
|---|---|---|
| 00d–00f | 1400×830 (App-Fenster) | DOCX-Briefvorlage: Seite 1 gefüllt, Seitenübersicht, Anlage nach manuellem Umbruch auf Seite 3 |
| 01–11 | 1920×1080 | PDF + DOCX, Seitenübersicht, Antwort → nächste Frage, Dokumentwechsel (DOCX-Tabelle), Original, Ergebnis, maximiert, Sperre, alle beantwortet, Abbrechen |
| 12/13 | 1280×720 | Pop-up, Panel unabhängig gescrollt |
| 14–18 | 1600×1000 / 1280×720 | 6 Dokumente, 9 Rückfragen, Fortschritt, „Nächste offene“, Auswahlliste |
| 19–22 | 1280×720 | 70 Seiten: Sprung zu Seite 65, Miniaturen, Seite 3, Zoom 140 % |
| 23/24 | 1600×1000 | Prüfansicht ohne Auftrag: Namensschutz, Schnellaktionen (nur Vorbelegung) |
| 24b/24c | 1600×1000 | Rückfrage beim Senden im selben Pop-up, Abbrechen |
| 25 | 960×600 | Mindestfenster: aktive Frage vollständig sichtbar |
| 26 | 1600×1000 | Dark Mode |
| 27–31 | 1280×720 / 1600×1000 | Bestätigen am Dokument und bei Chat-Rückfrage |

## Tests

- `tests/test_docx_layout_pagination.py` (6, neu): Layouttabelle auf Seite 1 ohne Textverlust, hohe Zeile, kurze
  Tabelle bleibt nebeneinander, manueller Umbruch, weiche Zeilenumbrüche, Ligatur-Markierung.
- `tests/test_pseudonymization_center_layout.py`: zwei neue Tests (Kartenaufbau, Vorschaugröße/Startposition).
- In den Gruppen web/chat/document/rendering/pseudo bestanden 990 Tests. Die einzigen Fehler waren die 4 bekannten
  `test_design_refresh`-Fehler (Logos außerhalb dieser Änderung gelöscht) und der danach korrigierte neue
  Kartentest; nach der Korrektur bestehen die betroffenen Dateien (19 Tests).

## Einschränkungen

- Der lokale DOCX-Satz bleibt eine Näherung: Schriftarten, Bilder, Textfelder und Fußnoten fehlen, Umbrüche können
  von Word abweichen. Hohe mehrspaltige Zeilen werden untereinander gesetzt; das wird gemeldet.
- Nur die Entwicklungsinstanz wurde geprüft; die installierte EXE muss neu gebaut werden.
- Keine Datenschutz-, Pilot-, P0-08- oder C-14-Freigabe. Die Leckfreiheit ist nicht belegt.
