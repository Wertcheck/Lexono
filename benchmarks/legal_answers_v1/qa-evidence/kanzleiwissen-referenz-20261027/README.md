# Kanzleiwissen nach Referenzbild – Umsetzung und visuelle QA (27.10.2026)

Alle Aufnahmen stammen aus der **laufenden Anwendung** (Quellstand, `python run.py serve --no-window`):

- **Testumgebung:** isolierte QA-Ablagen im Job-Ordner, Konto `qa-wissen@lexono.test`, ausschließlich **synthetische**
  Inhalte („QA-SYNTH“).
- **Gesetze:** nur Katalogtitel mit synthetischen Platzhalter-Paragraphen.
- **Keine Netzwerkverbindungen:** Cloud-Endpunkt tot, Gesetzes-Updateprüfung aus, Proxy auf einem toten Port.
- **Aufnahme:** headless Edge per CDP mit exakten Viewports (DPR 1). Messwerte in `*/messwerte_*.json`.

## Referenz

**Verbindlich:** `referenz-43_Kanzleiwissen_Gesetze.png` (= `assets/ux-ui/43_Kanzleiwissen_Gesetze.png`). Gründe:

- Das Repository enthält zusätzlich die ältere Übersicht `42_Kanzleiwissen.png` (Dokumentenliste, „Alle Dokumente“,
  „Favoriten“, Paginierung, „Neues Dokument“).
- Laut `knowledge_router.py` und `.agentic/DECISIONS.md` (26.09.) hat der Owner `43` als maßgebliche Referenzansicht
  festgelegt.
- In Folgerunden hat er die Kacheln „Favoriten“ (kein Datenmodell) und „Alle Inhalte“ (überflüssige Zwischenebene)
  ausdrücklich entfernen lassen.
- Diese Entscheidungen bleiben bestehen.

Die Illustration der Info-Karte ist das vom Owner bereitgestellte `assets/ux-ui/Kanzleiwissen_Panellbackground.png`
(unverändert).

## Abweichungen vorher → nachher (Abgleich mit Referenz 43)

| Bereich | vorher (`vorher/`) | nachher (`nachher/`) |
|---|---|---|
| Seitenkopf | zentrierte `.topbar` (max. 1280 px) – bei 1920 px gegen Kacheln und Tabelle versetzt; Titel 24 px | eigener, bündiger Kopf; Buch-Icon 44 px, Titel 32 px, Untertitel 17 px grau, Beschreibung 14 px (wie Referenz) |
| Ränder | Kacheln und Tabelle klebten ohne Innenabstand an der Seitenleiste | 30 px Seitenrand, Kopf, Kacheln und Tabelle fluchten |
| Kacheln | Zahl fett 15 px, Bezeichnung fett | Icon 26 px, Bezeichnung 13,5 px, Zahl dezent grau; aktive Kachel grün (Text, Zahl, Icon) |
| Tabelle | Kürzel, Version, Größe in Monospace; Namen fett und unterstrichen; Status 11 px fett | Satzschrift wie Referenz, Namen normal ohne Unterstreichung, Status „● Lokal verfügbar“ grün bzw. „● Nicht verfügbar“ grau in normaler Schrift, Schalter 44×24 |
| Erste Spalte | – | Zwischenstand (`zwischenstand/`): versetzte Zeilenlinien durch `display:flex` an der Zelle – behoben |
| Info-Karte | 300 px, Illustration max. 240 px, Titel 16 px, „Immer aktuell“ mit Uhr-Icon; bei Fensterhöhe ≤ 800 px Text mit Ellipse abgeschnitten | 340 px (≤ 1500 px Breite: 280 px), Illustration randfüllend, Titel 24 px, Kreispfeil-Icon wie Referenz; kein abgeschnittener Text mehr (Karte scrollt bei sehr niedrigen Fenstern) |
| Übrige Kategorien | Spaltenköpfe Monospace in Versalien | Satzschrift wie die Gesetzestabelle; Datumsfeld im Erfassungsformular gestaltet wie die übrigen Felder |
| 1280×720 | „Lokal verfügbar“ abgeschnitten, Status unter dem Schalter | Spaltenaufteilung angepasst; alle Spalten lesbar |
| 960×600 | Tabelle unbrauchbar gequetscht (Spalten überlappen) | Info-Karte ausgeblendet (rein erläuternd), Tabelle mit allen Spalten lesbar |
| Dark Mode | aktive Kachel grün auf grünem Grund kaum lesbar | heller Akzent auf dunkelgrünem Grund |

## Testfälle und Größen

| Datei | Größe | Inhalt |
|---|---|---|
| `*-01-gesetze-normen` | 1920×1080, 1280×720, 960×600 · hell und dunkel | Standardansicht (Gesetze & Normen, Info-Karte) |
| `1920x1080-hell-02-rechtsprechung` … `-05-interne-dokumente` | 1920×1080 | übrige Kategorien |
| `1920x1080-hell-06-suche-treffer` | 1920×1080 | Suche „Zivil“ → 1 Treffer |
| `1920x1080-hell-07-suche-ohne-treffer` | 1920×1080 | leeres Suchergebnis |
| `1920x1080-hell-08-gesetz-geoeffnet` | 1920×1080 | geöffnetes Gesetz (bestehende Gesetzesansicht, unverändert) |
| `1920x1080-hell-09-erfassen-rechtsprechung` | 1920×1080 | Erfassungsformular geöffnet |
| `1920x1080-hell-10-leer-gesetze`, `-11-leer-rechtsprechung` | 1920×1080 | leere Instanz |

**Funktion (gegen die laufende App geprüft):**

- **Kategoriewechsel per Kachelklick (HTMX):** URL `?category=expertise`; aktive Kachel, Untertitel und Tabellentitel
  wechseln mit.
- **Suche:** filtert serverseitig.
- **Gesetzeslink:** führt auf die bestehende Gesetzesansicht `/dashboard/laws/BDSG`.
- **Querscrollen:** in keiner Aufnahme.
- **Bei 960 px:** Die Messung meldet einen langen Gesetzestitel, dessen Inline-Text in der Zelle 5 px über den Rand
  reicht. Er wird per Ellipse abgeschnitten und ist nicht sichtbar.

## Tests

- **Kanzleiwissen:** `tests/test_web_knowledge.py` (45) + neu `tests/test_web_knowledge_reference_layout.py` (4):
  **49/49** bestanden.
- **Neue Regressionstests:**
  - eigener Kopf statt `.topbar`;
  - Untertitel und Beschreibung bleiben OOB-Swap-Ziele des Kategoriewechsels;
  - Kreispfeil-Icon in der Info-Karte;
  - alle neuen CSS-Regeln sind auf `.knowledge-head`/`.knowledge-page` begrenzt (andere Seiten unverändert).
- **Regression:** 73 Testdateien (Web/Chat/UI, Gesetze/Wissen, Migrationen, Mail, Design/Theme, Auth/CSRF,
  Akten/Mandanten/Aufgaben/Einstellungen) in Gruppen:
  - **1688 bestanden**, 1 übersprungen, 1 xfailed;
  - **4 fehlgeschlagen**, ausschließlich die bekannten, vorbestehenden Logo-Tests in `test_design_refresh.py`
    (fremdgelöschte Logos unter `assets/branding/`, nicht verändert).

## Verbleibende Abweichungen von Referenz 43

- **Kacheln:** fünf statt sechs, ohne „Alle Inhalte“ (Owner-Entscheidung 26.09., siehe oben).
- **Suchfeld:** steht direkt an der Liste („Suchen …“ neben „Gesetze & Normen“) statt oben rechts im Seitenkopf
  (Owner-Entscheidung 26.09.: Suche gehört an die Liste, keine konkurrierenden Suchfelder).
- **Status „Wird geladen … 66 %“:** erscheint nur während eines echten Downloads (vorhandene Fortschrittsanzeige) und
  wird nicht vorgetäuscht.
- **Zusätzliche Elemente:** Prüfknopf für Aktualisierungen neben installierten Gesetzen (bestehende Funktion, nicht in
  der Referenz) bleibt.
- **Gesetzesbestand:** Der Katalog zeigt alle 36 verfügbaren Gesetze statt der 10 der Referenz (echte Daten).
- **Kopfzeile und Seitenleiste:** global; nicht Teil dieses Auftrags und unverändert. Die Referenz zeigt dort den
  Zustand vor späteren Owner-Korrekturen, etwa ohne „Neuen Chat starten“.
- **Info-Karte bei kleinen Fenstern:**
  - unter 1100 px Breite ausgeblendet; sie ist rein erläuternd, ohne Funktion;
  - bei Fensterhöhen um 720 px scrollt sie intern, statt Text abzuschneiden.
- **Illustration:** Das Owner-Bild hat einen weißen Hintergrund. Im Dark Mode bleibt es deshalb eine helle Fläche
  (Bild unverändert übernommen).
- **Statuswerte in der Fachwissen-Tabelle:** zeigen weiterhin den internen Wert „pending“ statt „ausstehend“
  (vorbestehend, nicht Teil des Referenzbilds).
