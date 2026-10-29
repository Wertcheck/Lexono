# Pseudonymisierungs-Pop-up nach Referenzbild (27.10.2026)

Alle Aufnahmen stammen aus der **laufenden Anwendung im Quellstand**, nicht aus einer Demo und nicht aus der installierten EXE:

- **Start:** `python run.py serve --no-window`.
- **Instanz:** isoliert, angelegt mit `pseudo_seed.py`:
  - eigene Ablage und Datenbank;
  - Wegwerf-Admin;
  - Cloud-Endpunkt `127.0.0.1:9` (tot), Proxy ebenfalls tot.
- **Dokumente:** ausschließlich **synthetisch** und zur Laufzeit erzeugt (PDF/DOCX), siehe `pseudo_shots.py`.
- **Aufnahme:** headless Edge per CDP, exakte Viewports, DPR 1.
- **Messwerte:** aus dem DOM, abgelegt in `*/messwerte_*.json`.

**Referenz:** `referenz-pseudonymisierung-pruefen.png` (vom Owner nachgereicht).

## Inhalt

| Ordner | Inhalt |
|---|---|
| `vorher/` | 60 Aufnahmen des bisherigen Pop-ups und `messwerte_vorher.json` |
| `nachher/` | 63 Aufnahmen des neuen Pop-ups und `messwerte_nachher.json` |
| `zwischenstand/` | korrigierte Zwischenstände (siehe unten) |

**Größen:** 1920×1080, 1280×720 und 960×600, jeweils **hell und dunkel** (Dateinamen `<Größe>-<hell|dunkel>-<Nr>-…`).

**Zustände:**

- `01` erste Rückfrage
- `02` „Rückfrage 2 von 5“
- `03` / `04` Ansicht „Original“ / „Pseudonymisiertes Ergebnis“
- `04b` zurück in der markierten Vorschau
- `05` letzte Rückfrage
- `05b` „Vorherige“: bereits beantwortete Rückfrage mit erhaltener Auswahl
- `06` alle beantwortet: Bestätigen aktiv
- `07` abgebrochen: zurück im Chat
- `08` mehrere Dokumente
- `09` Dokumentwechsel zur DOCX
- `10` einzelne Rückfrage
- `11` / `11b` / `12` Datenschutz-Rückfrage beim Senden im selben Pop-up, beantwortet, abgebrochen (nur 1280×720 hell)

`03`, `04`, `04b`, `05b`, `07` und `09` wurden bei 1920×1080 und 1280×720 aufgenommen, die übrigen Zustände bei allen drei Größen.

## Vorher (Befund)

- Das Panel zeigte die aktive Rückfrage ausführlich und **alle weiteren als Liste** darunter. Bei 5 Rückfragen standen 5
  Karten gleichzeitig im Panel (`sichtbare_fragekarten: 5`).
- Miniaturen gab es nur nach Klick auf einen Schalter (`miniaturen_sichtbar: 0` in allen Aufnahmen).
- **Legende nach Zustand statt nach Kategorie:** Alle Funde waren gleich grün.
- Erklärungen der Optionen unter 760 px Fensterhöhe ausgeblendet.

## Umsetzung

Geändert wurden nur das Template, das Browser-Skript, das CSS und der Layout-Test.

**Kopf:**

- Schild-Icon, Titel „Pseudonymisierung prüfen“;
- Erklärung im Wortlaut der Referenz, ergänzt um „– bis dahin wird nichts gesendet“;
- Ansichtsumschaltung als Segment mit grün unterstrichener aktiver Ansicht;
- quadratischer Schließen-Button (= Abbrechen).

**Dokumentleiste:**

- Datei-Icon nach Typ, Dateiname und „PDF · 3 Seiten · 9 Funde“;
- umrandete Seitennavigation „‹ Seite 1 / 3 ›“, Zoom „− 100 % +“ (Klick auf den Wert setzt auf 100 % zurück) und Vollbild;
- kompakte Legende: **Person, Organisation, Adresse, Datum, Sonstiges**, dazu „Rückfrage offen“ (gestrichelt) und
  „bleibt lesbar“ (gepunktet).
- **Dokument-Tabs** mit Typ und Status („5 offen“ / „✓ geprüft“ / „gesperrt“) nur bei mehreren Dokumenten; die
  Auswahlliste ab 5 Dokumenten bleibt.

**Vorschau:**

- dauerhafte schmale Seitenleiste mit Miniaturen: aktive Seite grün umrandet, Seitenzahl darunter, Zähler offener
  Rückfragen;
- große Seitenansicht mit Markierungen **in der Farbe ihrer Kategorie**;
- Rückfragen gestrichelt, nach der Entscheidung in Kategoriefarbe bzw. gepunktet („Nicht pseudonymisieren“);
- die aktuelle Rückfrage mit grünem Ring.

**Prüfpanel: immer genau EINE Rückfrage.** Keine Liste, kein zweites modales Fenster.

- „Rückfrage 2 von 5“ mit Balken (Anteil beantworteter Fragen) und den Buttons ‹ (Vorherige) / › (Nächste offene
  Rückfrage).
- Fundkarte, getönt nach Kategorie: „Erkannte Person“, Begriff, „Seite 3“, „Fundstelle 1 von 2“. Ein Klick auf die
  Fundstelle springt zur nächsten Fundstelle. Bei mehreren Dokumenten erscheint zusätzlich der Dokumentname.
- „Kontext im Dokument“ mit hervorgehobenem Begriff. Der Vorkontext ist gekürzt, damit der Begriff vorn steht.
- „Was möchten Sie mit diesem Begriff tun?“ mit der kurzen Frage „Soll „X“ vor der Übergabe pseudonymisiert werden?“.
- Drei Radiokarten mit Icon und Erklärung (Auswahl grün umrandet mit gefülltem Radio):
  - „Pseudonymisieren – Wird durch einen Platzhalter ersetzt.“
  - „Nicht pseudonymisieren – Der Begriff bleibt im Text lesbar.“
  - „Unsicher – Wird vorsichtshalber pseudonymisiert.“
- Funde, Namensschutz, Schnellaktionen und Darstellungshinweise bleiben **zugeklappt unter der Rückfrage**. Das ist
  bestehende Funktion, nichts wurde entfernt.

**Fußleiste (immer sichtbar):**

- links Funde, offene Rückfragen und Hinweis;
- rechts unter dem Panel „Abbrechen“ und die Weiter-Aktion:
  - solange Rückfragen offen sind: **„Übernehmen und nächste Rückfrage ›“**, aktiv erst nach einer Auswahl;
  - an der letzten offenen Rückfrage, solange keine Option gewählt ist: „Option wählen, dann bestätigen“;
  - sind alle beantwortet: **„Pseudonymisierung bestätigen und fortfahren“** (bestehender Button und Pfad).

**Verhalten:**

- Eine Option zu wählen speichert die Auswahl nur im Dialog. Weiter geht es bewusst über „Übernehmen“; vorher sprang
  der Dialog sofort weiter.
- Entscheidungen bleiben über Fragen-, Dokument- und Ansichtswechsel erhalten (`05b`, `04b`, `09`).
- Beim Wählen werden nur die Klassen der Markierungen aktualisiert. Die Seitenbilder werden nicht neu geladen, die
  Scrollposition bleibt.

## Unverändert (Datenschutz und Logik)

- Server-Endpunkte, serverseitige Revalidierung (`require_resolved=1`, Textstände), Dokumentzuordnung, fail-closed
  Verhalten und Rollenprüfung sind unverändert. Bestätigen ist weiterhin gesperrt, solange Rückfragen offen sind, ein
  Dokument blockiert ist, Dokumente laden oder die Rolle fehlt.
- **Abbrechen** verwirft die Auswahl und sendet nichts. Gemessen nach jedem Abbruch in allen 6 Größe/Theme-Kombinationen
  sowie im Rückfrage-beim-Senden-Fall: `privacy_decisions`, `document_releases` und `chat_messages` sind unverändert.
  Im Rückfrage-Fall wurde nur `onCancel` aufgerufen, danach war kein Dialog mehr offen.
- Dokumentinhalte werden weiterhin nur per `textContent` eingefügt; Icons entstehen per `createElementNS`.

## Ergebnis (`nachher/messwerte_nachher.json`)

58 prüfbare Aufnahmen; ausgenommen sind die Chat-Ansicht nach dem Abbruch und `12`. In **allen 58** gilt:

- keine JS-Fehler;
- genau 1 sichtbare Fragekarte;
- alle 3 Optionen vollständig im sichtbaren Panelbereich;
- Fußzeile, „Abbrechen“ und Weiter/Bestätigen sichtbar;
- keine überlaufenden Bereiche, kein Querscrollen der Seite.

| Größe (hell) | Vorschau vorher → nachher (B×H) | Panel nachher |
|---|---|---|
| 1920×1080 | 1094×805 → 1062×817 (+ Miniaturleiste) | 440 px |
| 1280×720 | 902×480 → 754×513 (+ Miniaturleiste) | 380 px |
| 960×600 | 606×365 → 504×411 (+ Miniaturleiste) | 330 px |

## Zwischenstände (`zwischenstand/`, alle korrigiert)

- `v1-1920x1080-button-umbruch-zusatzbereiche`: Der Weiter-Button brach zweizeilig um. Behoben: Die Aktionen sind
  mindestens panelbreit, ohne Umbruch.
- `v1-1280x720-optionen-abgeschnitten`: Bei mehreren Dokumenten war nur eine Option sichtbar. Behoben:
  - Legende einzeilig;
  - kompaktere Fundkarte und Optionen;
  - Kontext auf 2 Zeilen;
  - unter 760 px Höhe trägt die Frage „Soll „X“ …?“ die Überschrift.
- `v3-960x600-unsicher-abgeschnitten`: „Unsicher“ war nicht sichtbar. Behoben: Kompaktstufe unter 640 px Höhe.
- `v5-960x600-kontext-ohne-begriff`: Der einzeilige Kontext zeigte den Begriff nicht. Behoben: Der Vorkontext ist auf
  ca. 40 Zeichen an einer Wortgrenze gekürzt.

## Bekannte Abweichungen von der Referenz

- **Dateigröße („482 KB“) fehlt:** Das Datenmodell speichert keine Größe. Statt einer erfundenen Angabe zeigt die
  Leiste Typ, Seiten und Funde.
- **„Unsicher“:** Die Referenz sagt „Zur weiteren Prüfung markieren“. Tatsächlich wird der Begriff serverseitig
  vorsichtshalber pseudonymisiert (`app/privacy/decisions.py`). Der Text beschreibt das echte Verhalten.
- **Kein Platzhalterbeispiel** („[PERSON_1]“): Welcher Platzhalter entsteht, steht erst nach der serverseitigen Prüfung
  fest.
- **Kategorie einer Rückfrage** wird nur angezeigt, wenn die lokale Prüfung sie zugeordnet hat; sonst steht dort
  „Unsicherer Begriff“ (bernsteinfarben). Im synthetischen Testdokument ordnet die Maskierung die Begriffe als Person
  zu.
- **Zusätzliche Bereiche:** Fußleistenstatus und die zugeklappten Zusatzbereiche gibt es in der Referenz nicht; sie
  tragen bestehende Funktionen.
- **Niedrige Fenster** (unter 760 bzw. 640 px Höhe):
  - Überschrift „Was möchten Sie …“ ausgeblendet; die Frage selbst bleibt.
  - Kontext auf 2 bzw. 1 Zeile gekürzt, vollständig als Tooltip.
  - „x von n beantwortet“ nur als Balken und Zähler in der Fußleiste.
  - Erklärungen der Optionen bei 960×600 einzeilig.
- **Unter 1180 px Breite:** Die Legende steht in einer zweiten Zeile, die Ansichtsumschaltung bei Höhen über 640 px
  ebenfalls.
- **Rückfrage beim Senden (`11`–`12`):** Die isolierte Instanz hat keine Cloud-KI, deshalb liefert der Server kein
  review-Ereignis. Das Pop-up wurde über dieselbe Browser-Schnittstelle geöffnet, die `chat.html` beim review-Ereignis
  nutzt (`LexonoPseudonymizationCenter.openReview`), mit einer synthetischen Rückfrage. Der Serverpfad ist durch
  `tests/test_web_privacy_review.py` abgedeckt.
- **Nicht ausgeführt:**
  - das tatsächliche Bestätigen mit Wiederaufnahme des Auftrags; Server- und JS-Pfad sind unverändert und durch die
    bestehenden Tests abgedeckt;
  - die Prüfung in der **installierten EXE** bzw. im WebView2-Fenster. Ohne ausdrückliche Freigabe wurde nichts
    installiert; die installierte EXE enthält dieses Pop-up noch **nicht**.

## Tests

- `tests/test_pseudonymization_center_layout.py`, angepasst und erweitert:
  - genau eine Rückfrage ohne Liste;
  - Optionstexte entsprechen dem Serververhalten;
  - Auswahl springt nicht weiter, „Übernehmen“ wechselt nur zur nächsten offenen Rückfrage, Bestätigen ist bis dahin
    verborgen;
  - Kategoriefarben und Legende;
  - dauerhafte Miniaturen.
- Zusammen mit `test_pseudonymization_center.py`: **38 bestanden**.
- **Chat, Datenschutz-Review, Freigabe, HITL, Namensschutz, Vorschau, DOCX-Seiten, No-Store:** 363 bestanden,
  1 xfailed.
- **Design, Privacy-Review, Fristen-HITL, Posteingang, Kanzleiwissen, Akten, Mandanten, Aufgaben, Einstellungen u. a.:**
  551 bestanden, 4 fehlgeschlagen. Das sind ausschließlich die bekannten, vorbestehenden Logo-Tests in
  `test_design_refresh.py`; die Logos im Arbeitsbaum wurden fremd gelöscht.

## Reproduktion

```
set LEXONO_QA_WORKDIR=<isolierter Ordner mit pseudo_seed.py>
python pseudo_shots.py <ausgabeordner> nachher
```

Die Instanz entsteht in `<LEXONO_QA_WORKDIR>/pseudo_qa`. Der Pfad muss „jobs“ enthalten, sonst verweigert das
Seed-Skript die Ablage (Schutz vor der Produktivablage).
