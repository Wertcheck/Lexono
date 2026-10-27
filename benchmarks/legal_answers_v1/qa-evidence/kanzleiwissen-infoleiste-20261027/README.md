# Kanzleiwissen – rechte Informationsleiste ohne eigenes Scrollen (27.10.2026)

Alle Aufnahmen stammen aus der **laufenden Anwendung**:

- **Quelle:** Quellstand, `python run.py serve --no-window`.
- **Testumgebung:** isolierte QA-Ablage mit ausschließlich synthetischen Inhalten, ohne Netzwerkverbindungen.
- **Aufnahme:** headless Edge per CDP mit exakten Viewports (DPR 1).
- **Messwerte:** Die Messung der Karte (`karte` in `*/messwerte_*.json`) stammt aus dem DOM:
  - `scrollHeight` gegenüber `clientHeight`;
  - `overflow-y`;
  - Unterkante der Karte gegenüber der Unterkante der Tabelle und dem Viewport.

## Ausgangslage

**Referenz:** Der im Auftrag erwähnte Screenshot ist nicht angekommen; in den Uploads und unter `assets/` gab es keine
neue Bilddatei. Grundlage waren deshalb die Textvorgabe und die bestehende Kanzleiwissen-Referenz
`assets/ux-ui/43_Kanzleiwissen_Gesetze.png`.

**Inhalt der Leiste:** Die rechte Leiste ist die Info-Karte „Aktuelles Recht. Lokal verfügbar.“ mit:

- Illustration;
- Überschrift;
- Beschreibung;
- den drei Merkmalen „Rechtssicher“, „Flexibel“ und „Immer aktuell“.

**Keine Teamdaten:** Informationen über das Kanzlei-Team oder Kontakte enthält die Leiste nicht, und es gibt dafür auch
keine Datenquelle auf dieser Seite. Sie wurden **nicht erfunden**, gemäß der Regel „keine statischen Demo-Inhalte“.

**Vorher gemessen (`vorher/messwerte_vorher.json`):**

| Größe | Inhalt / sichtbare Höhe der Karte | Ergebnis |
|---|---|---|
| 1920×1080 | 712 / 692 px | **interne Scrollleiste** |
| 1280×720 | 631 / 392 px | **interne Scrollleiste**, ~40 % des Inhalts nur per Scrollen erreichbar |
| 960×600 | – | **Karte ausgeblendet** (`display: none`) |

## Änderungen (nur CSS unter `.knowledge-page` sowie ein Leerzeichen im Kartentitel)

- **Kein internes Scrollen:** `max-height: none` und `overflow: visible` statt `max-height: 100%` und
  `overflow-y: auto`.
- **Höhenabhängig kompakt:**
  - Illustration, Abstände und Schriftgrößen skalieren fließend mit der Fensterhöhe (`clamp(…vh…)`);
  - Merkmale kompakt mit Titel und Text direkt untereinander;
  - unter 800 px Höhe zusätzlich engere Ränder und eine flachere Illustration;
  - der Text wird nie gekürzt (die frühere 2-Zeilen-Ellipse ist entfernt).
- **Schmale Fenster (≤ 1100 px):** Statt die Karte auszublenden, steht sie als **Band unter der Tabelle**:
  - Illustration links, Überschrift und Beschreibung in der Mitte, die drei Merkmale rechts;
  - die Seite scrollt, die Karte selbst nie;
  - die Tabelle behält ihren eigenen Scrollbereich (max. 340 px).
- **Titel:** Ein Leerzeichen vor dem Zeilenumbruch, damit der einzeilige Titel im Band „Aktuelles Recht. Lokal
  verfügbar.“ lautet (`zwischenstand/…titel-ohne-leerzeichen`).
- **Nebenbefund behoben:** Zwischen 1501 und 1600 px Breite (u. a. das maximierte Desktopfenster, 1536 px bei 125 %)
  stieß der Tabellenstatus an den Schalter, und „Lokal verfügbar“ war abgeschnitten. Dafür gilt jetzt dieselbe
  Spaltenaufteilung wie unter 1500 px.

## Ergebnis (`nachher/messwerte_nachher.json`)

| Größe | Inhalt / Höhe | intern scrollend | Karte endet bei / Tabelle endet bei | vollständig sichtbar |
|---|---|---|---|---|
| 1920×1080 | 516 / 516 | nein | 880 / 1056 | ja |
| 1536×842 | 435 / 435 | nein | 799 / 818 | ja |
| 1366×768 | 382 / 382 | nein | 687 / 744 | ja |
| 1280×720 | 380 / 380 | nein | 684 / 696 | ja |
| 960×600 | 180 / 180 (Band) | nein | Band unter der Tabelle | ja, nach Seitenscroll (`…-01b-infoleiste-unten`) |

Gleiche Werte im Dark Mode. Querscrollen tritt in keiner Aufnahme auf. Die Aufnahmen wurden einzeln gesichtet.

**Zwischenstand (`zwischenstand/`), korrigiert:**

- Bei 1280×720 ragte die Karte zunächst 20 px unter die Tabelle.
- Im Band fehlte das Leerzeichen im Titel, und zwischen Titel und Text entstand eine Lücke.

## Tests

- **Kanzleiwissen:** `tests/test_web_knowledge.py` + `tests/test_web_knowledge_reference_layout.py` (3 neue Tests):
  **52/52** bestanden. Die neuen Tests prüfen:
  - kein internes Scrollen und keine Rückkehr von `overflow-y: auto` / `max-height: 100%`;
  - nie mehr `display: none` für die Karte, Band-Layout vorhanden;
  - Leerzeichen im Titel.
- **Regression:** 51 Dateien (Web/UI, Design/Theme, Gesetze/Wissen, globale Suche):
  - **1185 bestanden**, 1 xfailed;
  - **4 fehlgeschlagen**, ausschließlich die bekannten, vorbestehenden Logo-Tests in `test_design_refresh.py`
    (fremdgelöschte Logos).

## Verbleibende Einschränkungen

- **Unter 1100 px Breite:** Die Leiste steht unter der Tabelle. Sie ist dort vollständig sichtbar, aber erst nach
  Scrollen der Seite, nicht gleichzeitig mit dem oberen Seitenbereich. Bei 960×600 reicht der Platz nicht für Tabelle
  und Leiste nebeneinander.
- **Sehr niedrige Fenster:** Unterhalb von etwa 700 px Höhe bei voller Breite kann die Karte länger als die Tabelle
  werden. Sie scrollt dann nicht selbst; die Seite scrollt.
- **Fehlende Referenz:** Ein Abgleich mit dem angekündigten Screenshot war nicht möglich.
- **Team- und Kontaktinformationen:** Die Leiste enthält keine, weil es dafür keine Datenquelle gibt.
