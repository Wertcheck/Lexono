# „Standard-Prompts verwalten“ – Modal und Startseiten-Auswahl (28.10.2026)

**Referenzen** (vom Owner nachgereicht):

- `referenz-uebersicht-leerer-zustand.png`: Übersicht, rechter Bereich leer;
- `referenz-neuer-prompt-formular.png`: Formular „Neuen Prompt erstellen“.

**Aufnahmen** (`aufnahmen/`, Skript `prompt_shots.py`) stammen aus der laufenden Anwendung (`run.py serve --no-window`):

- Isolierte Instanz, per Migration angelegt. Sie übernimmt die vier bisherigen Startkarten.
- Dazu drei weitere **synthetische** Prompts, die über die echte Speicher-API angelegt wurden.
- Headless Edge, DPR 1. Messwerte in `aufnahmen/messwerte_nachher.json`.

## Umsetzung

**Datenmodell (wiederverwendet):** `PromptTemplate`, dieselbe Tabelle wie die bestehende Kanzlei-Prompt-Bibliothek. Neue
Spalten (Migration `schritt3_039`):

| Spalte | Bedeutung |
|---|---|
| `show_on_start` | Prompt auf der Startseite aktiviert |
| `sort_order` | Reihenfolge in der Verwaltung und damit auf der Startseite |
| `icon` | Icon aus einer festen Liste |
| `start_action` | `prefill` = Anweisung ins Eingabefeld; `prefill_attach` = zusätzlich Dateiauswahl; `open_matter` = Aktenauswahl |

**Übernahme ohne Datenverlust:**

- Die vier bisher fest im Template verdrahteten Startkarten („Dokument analysieren“, „Schriftsatz erstellen“,
  „Zusammenfassen“, „Akte öffnen“) werden als Prompts angelegt und aktiviert, mit wortgleichen Titeln, Beschreibungen
  und Anweisungen in bisheriger Reihenfolge.
- Gibt es bereits einen gleichnamigen Prompt, wird dieser aktiviert und sein Text bleibt unverändert.
- Vorhandene Bibliotheks-Prompts bleiben unverändert und inaktiv.

**Startseite:** zeigt **ausschließlich aktivierte** Prompts in gespeicherter Reihenfolge, höchstens vier, nicht
automatisch die obersten vier. Ohne aktivierte Prompts erscheint ein Hinweis.

**Modal** (`partials/prompt_manager.html`, `static/js/prompt_manager.js`, CSS unter `.prompt-manager`): Es öffnet sich
über „Standard-Prompts verwalten“ zentriert über dem abgedunkelten Chat, ohne Weiterleitung. Ohne JavaScript führt der
Link zur bisherigen Bibliotheksseite.

*Linke Spalte:*

- „Alle Prompts (x von y aktiv auf Startseite)“ mit Hinweis-Icon.
- Zeilen mit Ziehgriff, Icon, Titel, Beschreibung, Schalter („Startseite“ / „Nicht auf Startseite“) und Menü
  (Bearbeiten, Löschen).
- **Aktivierung** = grüner Schalter und getönte Zeile. **Bearbeitungsauswahl** = dunkle Kontur. Beides ist bewusst
  getrennt.
- Aktivierte Prompts stehen oben, Trennlinie, darunter die übrigen.
- **Sortieren:** per Drag-and-drop am Griff oder per Tastatur (Pfeil hoch/runter am Griff), jeweils innerhalb der
  Gruppe.
- „+ Neuen Prompt hinzufügen“; während des Anlegens als hervorgehobene Kachel wie in Referenz 1.

*Viererlimit:*

- Ein fünfter Schalter wird verhindert. Erklärung: „Bereits 4 von 4 Startplätzen belegt. Bitte zuerst einen anderen
  Prompt deaktivieren …“.
- Im Formular ist der Schalter dann gesperrt, mit dem Hinweis aus der Referenz.
- Deaktivieren löscht nichts.

*Rechte Spalte:*

- Beim Öffnen ist nichts ausgewählt; es erscheint der leere Zustand mit Hinweis und drei Merkmalen (Referenz 2).
- Auswahl lädt Titel, Beschreibung und Anweisung; „Löschen“ fragt vorher nach.
- Das Formular „Neuen Prompt erstellen“ (Referenz 1) enthält:
  - Icon (optional, Auswahlmenü);
  - Schalter „Auf der Chat-Startseite anzeigen“;
  - Titel* (0/100), Beschreibung* (0/200), Prompt-Anweisung* (0/2000), jeweils mit Zähler.
- Bei „Akte öffnen“ und Prompts mit Dateiauswahl erklärt ein Hinweis, was die Karte tut.

**Speichern:** Alle Änderungen (Anlegen, Bearbeiten, Löschen, Aktivieren, Sortieren) werden im Modal gesammelt.

- „Speichern“ übernimmt sie in **einem** Aufruf (`POST /dashboard/library/prompts/manage`).
- Der Server prüft vollständig, bevor er schreibt: Viererlimit, Pflichtfelder, Längen, Icons, veraltete IDs. Bei
  einem Fehler bleibt alles unverändert (HTTP 400 mit verständlicher Meldung; die betroffene Zeile und das Feld werden
  markiert).
- Bestätigung dezent: „Gespeichert“.
- „Abbrechen“, Schließen und Escape fragen bei ungespeicherten Änderungen nach („Ungespeicherte Änderungen
  verwerfen?“). Ein Wechsel zwischen Prompts verliert nichts, weil die Eingaben gesammelt bleiben.
- Nach dem Schließen lädt die Startseite neu und zeigt die gespeicherte Auswahl.
- Audit-Ereignisse für Anlegen, Ändern, Löschen und die Startseitenbelegung.

**Rechte:** wie die bestehende Bibliothek.

- Ändern dürfen Admin und Anwalt (serverseitig erzwungen, CSRF-geschützt).
- Mitarbeitende sehen die Liste nur lesend; das Speichern wird mit 403 abgelehnt.

**Hell und Dunkel:** über die bestehenden Tokens.

## Ergebnisse (`aufnahmen/`)

**Referenzzustände** bei 1920×1080, 1280×720 und 960×600, jeweils hell und dunkel:

- `*-01-uebersicht-leerer-zustand`: 4 von 7 aktiv, rechts der leere Zustand.
- `*-02-neuer-prompt-formular`: Formular. Bei 4/4 ist der Schalter gesperrt und erklärt.
- In allen Aufnahmen sind „Abbrechen“ und „Speichern“ sichtbar, es gibt kein Querscrollen und keine JS-Fehler.

**Funktionsablauf (1280×720 hell, `10`–`21`):**

| Schritt | Ergebnis |
|---|---|
| Startseite vorher | Dokument analysieren, Schriftsatz erstellen, Zusammenfassen, Akte öffnen (aus der Migration) |
| Prompt anklicken | Formular „Prompt bearbeiten“, Zeile mit dunkler Kontur (`11`) |
| 5. Aktivierung | verhindert, Hinweis sichtbar (`12`) |
| „Zusammenfassen“ aus, „Recherche“ an, Recherche per Tastatur nach oben, „Akte öffnen“ per Drag-and-drop nach oben | „Ungespeicherte Änderungen“ (`13`) |
| Abbrechen | Rückfrage „Ungespeicherte Änderungen verwerfen?“ (`14`) → weiter bearbeiten |
| Speichern | „Gespeichert“ (`15`) |
| Menü → Löschen | Bestätigung (`16`, `17`) |
| Neuer Prompt ohne Beschreibung | „Bitte eine Beschreibung eingeben.“, Feld markiert (`18`); danach gespeichert (`19`) |
| Startseite nachher | **Akte öffnen, Dokument analysieren, Recherche, Schriftsatz erstellen**: genau die aktivierten, in der gespeicherten Reihenfolge (`20`) |
| Neu laden | identische Startseite und Verwaltung (`21`), die Auswahl bleibt dauerhaft erhalten |

## Tests

- **Neu, `tests/test_prompt_start_management.py`** (20 Tests):
  - **Grundlagen:** einmalige Übernahme der Standardkarten; CRUD und Sortierung in einem Speichervorgang mit Audit.
  - **Startseite:** nur aktivierte statt der obersten vier; Startseite und Verwaltung nach erneutem Laden; Hinweis ohne
    aktivierte Prompts.
  - **Viererlimit:** Ablehnung ohne Änderung.
  - **Validierung:** Pflichtfelder und Längen (100/200/2000); unbekannte Icons und veraltete IDs.
  - **Datenerhalt:** bestehende längere Texte bleiben unangetastet; parallel angelegte Prompts bleiben erhalten.
  - **Web:** JSON-Laden und -Speichern; HTTP 400 bei Fehlern; Mitarbeiter-Rolle; CSRF; Markup beider
    Referenzzustände.
  - **Migration** auf einer Bestandsdatenbank: Übernahme, eigener Prompt unverändert, gleichnamiger Prompt aktiviert
    ohne Dublette.
- **Angepasst:** Die Startkarten-Tests (`test_web_chat_start_reference.py`, Farbtest in `test_web_chat.py`) legen die
  Standardkarten an, weil die Startkarten jetzt aus der Datenbank kommen.
- **Regression** (Web, Chat, Prompt, Pop-up, Design, Migrationen, Dokumentvorschau; 63 Dateien): **1433 bestanden**,
  1 xfailed, **4 fehlgeschlagen**. Die 4 Fehler sind ausschließlich die bekannten Logo-Tests in
  `test_design_refresh.py`: Die Logos sind im Arbeitsbaum fremd gelöscht und wurden bewusst nicht wiederhergestellt.

## Abweichungen und offene Punkte

- **Kanzleiweit statt Konto:** Die Referenz sagt „Die Prompts werden in Ihrem Konto gespeichert.“ Das bestehende
  Datenmodell ist aber **kanzleiweit**, die Startseite also für alle gleich. Der Text lautet daher wahrheitsgemäß „Die
  Prompts gelten für die ganze Kanzlei.“ Persönliche Startseiten wären eine fachliche Entscheidung mit
  Modelländerung.
- **Leerer Zustand:** Die Referenz titelt „Neuen Prompt erstellen“. Umgesetzt ist „Prompt auswählen oder erstellen“,
  damit er nicht mit dem Formular gleichen Titels verwechselt wird.
- **Beispiel-Prompts der Referenz** wie „Rechtliche Bewertung“ und „Recherche“ werden **nicht** automatisch angelegt;
  sie existieren nur in der QA-Instanz als synthetische Daten.
- **Sortieren** geschieht innerhalb der Gruppen. Zwischen den Gruppen wechselt man mit dem Schalter: Aktivieren hängt
  einen Prompt ans Ende der aktivierten an, Deaktivieren setzt ihn an den Anfang der übrigen.
- **Löschen** wird wie alle Änderungen erst mit „Speichern“ wirksam.
- **Aktion der Startkarte** („Anweisung ins Eingabefeld“, „mit Dateiauswahl“, „Aktenauswahl“) ist für die übernommenen
  Karten festgelegt. Neue Prompts füllen das Eingabefeld. Ein Umschalten dieser Aktion im Formular gibt es nicht: Die
  Referenz zeigt kein solches Feld.
- **Nicht installiert:** Die Desktop-App enthält die Änderung erst nach Neubau und Installation. Beim ersten Start der
  neuen Version migriert die produktive Datenbank auf `schritt3_039`.
