# Markierungen in der Dokumentvorschau – Annotationsdesign (28.10.2026)

**Gegenstand:** die Markierungen personenbezogener Daten im Pseudonymisierungs-Pop-up.

**Aufnahme:**

- Alle Aufnahmen stammen aus der laufenden Anwendung (`run.py serve --no-window`), mit headless Edge per CDP, DPR 1 und
  **sichtbaren** Bildlaufleisten.
- Isolierte Instanz, Cloud-Endpunkt tot, ausschließlich synthetische Dokumente.
- **Vorher:** sauberer Worktree auf `50a557c`. **Nachher:** Arbeitsstand. Beide mit denselben Daten und Schritten.
- Skript: `mark_shots.py`; Messwerte (DOM-Klassen, Boxen, Tooltips, Synchronisierung) in `*/messwerte_*.json`.

**Synthetisches Testdokument** (PDF):

- mehrteilige Namen;
- mehrere Funde in einer Zeile (Name, Anschrift);
- Organisation, E-Mail;
- zwei Rückfragen, davon eine mit zwei Fundstellen;
- ein Name über einen Zeilenumbruch („Sabine“ / „Hartmann“).

Dazu eine DOCX mit Tabelle, in der „Thomas“ und „Berger“ als zwei Teilfunde kommen.

## Bestandsaufnahme (vorher)

**Rendering:** absolut positionierte Overlays über dem Seitenbild. Die Rechtecke liefert der Server; daran ändert sich
nichts:

- Wortrahmen der PDF-Textebene, bei Mehrwortwerten die Phrasensuche je Zeile;
- Position normiert auf die Seite, dadurch folgt sie jeder Zoomstufe.

**Befunde:**

1. **Kästen statt Annotationen:** 1-px-Rahmen in Kategoriefarbe plus Tönung, dabei glyphengenau ohne Luft. Offene
   Rückfragen hatten einen **2-px-Strichrahmen**, dazu kam der 3-px-Ring der aktiven Rückfrage. Das wirkte unruhig
   (`vorher/*-02-zoom100-nah.png`).
2. **Doppelte Tönung:** Varianten innerhalb eines längeren Funds („Sabine“ in „Sabine Hartmann“) wurden zusätzlich
   übereinander gezeichnet (18 statt 15 Overlays).
3. **Mehrteilige Namen in Tabellen** erschienen als getrennte Kästen „Thomas“ | „Berger“ (`vorher/*-09-docx-tabelle-nah.png`).
4. **Synchronisierungsfehler:** Ein Klick auf die **zweite** Fundstelle von „Richter“ zeigte im Panel „Fundstelle 1
   von 2“ (`vorher/1280x720-hell-05-…`, Messwert `fundstelle`). Welche Fundstelle gerade ausgewählt ist, war im Dokument
   nicht erkennbar.
5. **Tooltips** waren technisch („[PERSON_01] (Person)“, „Rückfrage offen: …“), Fundstellen nicht per Tastatur
   erreichbar.

## Umsetzung

Gezielt im bestehenden Rendering (`static/js/pseudonymization_center.js`) und CSS (`static/css/app.css`). Es gibt
**kein** paralleles System und keine Änderung an Server, API, Erkennung, Pseudonymisierung, Rollen- oder Serverprüfung.

**Gestaltung:**

- Dezente Tönung (Alpha höchstens 0,12) mit 3-px-Rundung und **feiner 1,5-px-Unterstreichung** in der Kategoriefarbe.
- Farben: Person rot, Organisation blau, Adresse orange, Datum grün, Sonstiges violett.
- Kein Rahmen, keine Animation.

**Unterscheidung nicht nur über Farbe, sondern über den Linienstil:**

| Linie | Bedeutung |
|---|---|
| durchgezogen | Fund wird ersetzt |
| **gestrichelt** | Rückfrage offen (prüfpflichtig) |
| **gepunktet ohne Tönung** | bleibt lesbar |

Die Legende verwendet dieselben Muster.

**Hierarchie:**

1. normal: dezent;
2. Hover oder Tastaturfokus: etwas kräftigere Tönung und feine Kontur;
3. alle Fundstellen der angezeigten Rückfrage: feine grüne Kontur;
4. **aktuelle Fundstelle:** grüne Kontur mit weichem Halo.

**Präzision:**

- Die Tönung ist oben und unten leicht eingezogen und seitlich um 1,5 px erweitert. Sie umschließt den Text ruhig, ohne
  Zeichen zu verdecken oder die Nachbarzeile zu berühren.
- Positionen bleiben prozentual und folgen jeder Zoomstufe (geprüft bei 80, 100 und 160 %).

**Darstellungsliste je Seite** (`drawableMarks`, nur Anzeige):

- Vollständig enthaltene maskierte Funde entfallen, damit sich die Tönung nicht verdoppelt.
- Benachbarte Teile **derselben Kategorie in derselben Zeile** werden zu einer Markierung verbunden („Thomas Berger“ als
  eine Fundstelle, Tooltip mit beiden Platzhaltern).
- Zeilenübergreifende Funde bleiben je Zeile, aber einheitlich gestaltet.
- Offene Rückfragen bleiben einzeln und liegen oben.

**Synchronisierung in beide Richtungen:**

- Jede Rückfrage-Fundstelle trägt ihre Nummer `data-occ`, gezählt wie „Fundstelle i von n“ (dokumentübergreifend).
- Klick oder Enter/Leertaste auf eine Fundstelle öffnet die Rückfrage **mit genau dieser Fundstelle** (`selectOccurrence`).
- Panel-Navigation und der Wechsel der Fundstelle markieren die aktuelle Fundstelle im Dokument (`markCurrent`).

**Bedienung und Tooltips:**

- Rückfrage-Fundstellen sind per Tab erreichbar (`role="button"`, `aria-label`).
- Tooltips sind verständlich formuliert, z. B. „Thomas Berger – Person, wird ersetzt durch [PERSON_02] [PERSON_03]“
  oder „Rückfrage offen: „Richter“ – zum Prüfen anklicken“.

**Seitenbilder bleiben weißes Papier**, auch im Dunkelmodus (`*-dunkel-*`).

## Ergebnisse (nachher)

| Prüfung | Ergebnis |
|---|---|
| Klick auf 2. Fundstelle „Richter“ | Panel „Fundstelle **2 von 2**“, aktuelle Markierung = diese Fundstelle (`05`) |
| Klick auf Fundstelle „Kaufmann“ | Panel wechselt zu „Kaufmann“, Fundstelle 1 von 1, im Dokument hervorgehoben (`06`) |
| Panel „nächste Rückfrage“ | Dokument hebt „Richter“ (Fundstelle 1) hervor (`07`) |
| Entscheidung „Nicht pseudonymisieren“ | Markierung wechselt zu gepunktet ohne Tönung (`08`) |
| Overlays Brief / DOCX | 15 statt 18 / 4 statt 5 (Duplikate entfernt, „Thomas Berger“ verbunden) |
| JavaScript-Fehler | keine |
| Größen | 1920×1080 und 1280×720, hell/dunkel; Nahaufnahmen bei 80 / 100 / 160 % |

## Tests

- **Neu in `tests/test_pseudonymization_center_layout.py`:**
  - Tönung und Unterstreichung statt Rahmen, Alphagrenze je Kategorie;
  - Linienstile für offen und bleibt lesbar;
  - keine Animation;
  - Abstufung aktiv vs. aktuell;
  - Geometrie in Prozent mit Einzug;
  - Zusammenführung und Entfernen enthaltener Duplikate;
  - Synchronisierung über `data-occ`, `selectOccurrence` und `markCurrent`, Tastaturbedienung.
- **Ergebnis:** Pop-up, Chat, Privacy-Review, Dokumentvorschau, Freigabe, DOCX-Seiten, Namensschutz, HITL und Design
  mit **357 bestanden**, 1 xfailed. Die Pseudonymisierungs-Suiten (Pseudonymizer, Review, Platzhalter, Benchmark)
  mit **150 bestanden**.
- **4 fehlgeschlagen:** ausschließlich die bekannten Logo-Tests in `test_design_refresh.py`. Die Logos sind im
  Arbeitsbaum fremd gelöscht und wurden bewusst nicht wiederhergestellt.
- Eine JS-Laufzeit für Unit-Tests gibt es im Projekt nicht. Das Verhalten ist durch die Browser-QA oben belegt, die
  Regeln sind statisch getestet.

## Offene Punkte

- Die installierte EXE (6542754) enthält diese Änderung **nicht**; dafür sind Neubau und Installation nötig.
- Bei echten Scans ohne Textebene gibt es weiterhin keine positionsgenauen Markierungen (Hinweis im Panel, unverändert).
- Ein über zwei Zeilen laufender Name erscheint als zwei gleich gestaltete Markierungen. Sie sind nicht verbunden, weil
  der Server je Zeile ein Rechteck liefert.
- Die Zusammenführung greift nur bei gleicher Kategorie und kleinem Abstand. Unterschiedliche Kategorien direkt
  nebeneinander bleiben bewusst getrennt.
