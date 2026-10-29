# Native Desktopprüfung der installierten Lexono.exe (26.10.2026)

Geprüft wurde die **installierte** `%LOCALAPPDATA%\Lexono\Lexono.exe` (SHA-256 `97f8deb6…`, Quellstand `6e4db30`) mit
ihrem **nativen WebView2-Fenster**, nicht in einem separaten Browser.

**Aufnahme:**
- Jeder Screenshot ist ein Bild des App-Fensters selbst, mit nativer Titelleiste und Fensterknöpfen.
- Erzeugt per Win32 `PrintWindow(PW_RENDERFULLCONTENT)` in echten Pixeln; Windows-Skalierung 125 %, Bildschirm
  1920×1080 physisch.

**Bedienung:** über die DevTools-Schnittstelle **desselben** WebView2 (`--remote-debugging-port`): Login, Navigation,
Klicks im App-Fenster. Die Fenstergröße wurde per `SetWindowPos` so gesetzt, dass der Inhaltsbereich exakt
1400×830, 1280×720 und 960×600 (CSS-Pixel) misst.

**Isolation:**
- eigene Datenablage im Job-Ordner (`LEXONO_DATA_DIR`), synthetisches Wegwerf-Konto;
- Cloud-Endpunkt tot (`127.0.0.1:9`);
- nur synthetische Dokumente;
- die produktive Datenbank war vor und nach jedem Lauf byte-gleich (SHA-256 `b9b34d30…`);
- Serverprotokoll: 0 ERROR-Zeilen.

Skript: `native_qa.py`, im Job-Ordner und nicht eingecheckt, weil es die Zugangsdaten des Wegwerf-Kontos enthält.
Die Messwerte stehen in `native_qa.json`.

## Ergebnisse der installierten EXE (20 native Screenshots)

| Nr. | Inhaltsbereich | Prüfung | Ergebnis |
|---|---|---|---|
| 00 | 1386×844 (Standardfenster) | Chat mit Sperrhinweis | Login und Chat im nativen Fenster |
| 1400x830-01 | 1400×830 | PDF, markierte Vorschau | Markierungen exakt auf Berlin, 12/345/67890, Nordbauholding GmbH (NBH), Thomas Berger, Anschrift; Seitenbreite 940/972 px |
| -02 / -03 | 1400×830 | Seitenübersicht, Zoom 140 % | Miniaturen, Zoom ohne Überlauf |
| -04 | 1400×830 | HITL-Antwort → Wechsel ins DOCX mit Tabelle | Tabelle mit Rahmen, Markierungen in den Zellen; „Richter“ bleibt „pseudonymisiert“ |
| -05 / -06 | 1400×830 | Original, pseudonymisiertes Ergebnis | Entscheidungen bleiben beim Ansichtswechsel erhalten |
| -07 / -08 | 1400×830 | alle beantwortet, Abbrechen | Bestätigen aktiv; nach Abbrechen DB unverändert (3/0/35 vorher und nachher) |
| -09…-11 | 1400×830 | DOCX-Briefvorlage (große Tabellenzelle) | Seite 1 gefüllt, Absatz 18 und „Steffen Hoffmann“ auf Seite 2 markiert, Anlage nach manuellem Umbruch auf Seite 3; alle 18 Rufnummern markiert |
| -12 / -13 | 1400×830 | 5 Dokumente, 9 Rückfragen, „Nächste offene“ | Tabs scrollbar, Auswahlliste, Fortschritt 1 von 9, kompakte Zeilen |
| 1280x720-01/-02 | 1280×720 | PDF, HITL nach Antwort | Fußleiste und Antworten vollständig sichtbar |
| 960x600-01/-02 | 960×600 | App-Mindestfenster | Ansichten in der Titelzeile, aktive Rückfrage vollständig |
| -14 / -15 | 1400×830 | Bestätigen | Entscheidungen 3 → 5, Rückkehr in den Chat; danach toter Endpunkt |

In allen Pop-up-Zuständen: kein horizontaler Überlauf in Dialog oder Seite, Fußleiste im Fenster, Antworten der
aktiven Rückfrage sichtbar (`native_qa.json`).

## Neue Befunde nur im nativen Fenster (im Browser-Lauf nicht sichtbar) – korrigiert im Quellstand

Der frühere Browser-Lauf blendete Scrollleisten aus (`--hide-scrollbars`) und nutzte keine echte Fenstergröße. Im
nativen Fenster fielen drei Punkte auf:

1. **Horizontale Scrollleiste auf jeder Chatseite** (auch im Screenshot des Nutzers vom 25.10. sichtbar).
   - Ursache: Der Hover-Tooltip „Datenschutz & Pseudonymisierung“ des Datenschutz-Buttons unten rechts war zentriert
     und ragte – unsichtbar, aber mitgelayoutet – 57 px über den rechten Rand von `div.main`.
   - Korrektur: In der Ecke ist der Tooltip rechtsbündig (`app.css`). Gemessen nachher: Überstand 0 px bei 1400×830,
     1280×720 und 960×600.
2. **Schwarzer Fokusrahmen um „Markierte Vorschau“** nach dem Öffnen.
   - Ursache: `showModal()` fokussiert den ersten Button.
   - Korrektur: Startfokus auf dem Dialogtitel (`autofocus`, `tabindex=-1`); Tastaturfokus im Lexono-Grün.
3. **Standardfenster größer als der Bildschirm.**
   - `run.py` öffnet 1400×900. Gemessen auf diesem Rechner (1536×864 logisch): Das Fenster maß 1750×1102 physische
     Pixel bei 1080 Pixel Bildschirmhöhe. Die unteren ~40 px samt Teil der Pop-up-Fußleiste lagen unter dem Rand.
   - Korrektur: Passt die Startgröße nicht in den Arbeitsbereich, startet das Fenster maximiert
     (`_should_start_maximized`, `_work_area_logical`).

**Nachprüfung im nativen Fenster aus dem Quellstand** (`python run.py serve`, echtes WebView2-Fenster;
`quellstand-korrekturen/`, `native_verify.json`):

- Fenster maximiert, Inhaltsbereich 1536×842, vollständig im Arbeitsbereich;
- Überstand `div.main` 0 px, keine Scrollleiste;
- Fokus nach dem Öffnen auf `#pseudo-center-title`, Fußleiste im Fenster.

Diese drei Korrekturen sind **nicht** in der installierten EXE enthalten; dafür ist ein neuer Build nötig.

## Einschränkungen

- Die Bedienung erfolgte programmatisch im nativen Fenster, nicht per Maus und Tastatur.
- Getestet wurde nur auf diesem Rechner (ein Bildschirm, 125 %).
- Kein Cloud-E2E; siehe `../e2e-loopback-installed-6e4db30-20261026/`.
- Keine Datenschutz-, Pilot-, P0-08- oder C-14-Freigabe.
