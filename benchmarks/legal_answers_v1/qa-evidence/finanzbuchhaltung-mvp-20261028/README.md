# Finanzbuchhaltung – MVP nach Referenzbild (28.10.2026)

Ein mandantenbezogenes Buchhaltungsmodul für Steuerberater, in Lexono integriert. Die verbindliche Designvorlage ist
`referenz-finanzbuchhaltung.png`. Die Aufnahmen in `aufnahmen/` stammen aus der laufenden Anwendung:

- Start über `run.py serve --no-window` als isolierte Instanz, per Migration angelegt;
- Headless Edge, DPR 1;
- Skript `fibu_shots.py`, Messwerte in `aufnahmen/messwerte.json`.

**Nur synthetische Daten:** zwei QA-Mandanten, Buchungen, Belege (PDF und PNG) und Aufgaben, alle über die echten
Routen der App angelegt. **Keine Freigabe für echte Mandantendaten.** Ebenso keine steuerliche, GoBD- oder
DATEV-Freigabe.

## Umsetzung

**Integration statt Parallelsystem:**

- Mandanten: bestehende Mandantenverwaltung (`clients`, nur aktive Mandanten zur Auswahl).
- Rollen und Rechte: `PERMISSION_MATRIX`, `require_role` mit CSRF-Prüfung.
- Audit: `AuditEvent`, append-only.
- Navigation, Design-Tokens, Hell- und Dunkelmodus sowie das Logo sind die bestehenden.
- Migrationskette: Alembic `schritt3_040`.

**Neue Tabellen** (ausschließlich neu; bestehende Tabellen und Daten bleiben unverändert):

| Tabelle | Inhalt |
|---|---|
| `fibu_accounts` | Konten je Mandant (Nummer eindeutig je Mandant, Kontoart, aktiv/deaktiviert) |
| `fibu_bookings` | Buchungen: Datum, Beleg, Beschreibung, Einnahme/Ausgabe, Brutto/Netto/Steuer in Cent, Steuersatz, Konto, Gegenkonto, Beleg-Datei, Status, Storno-Grund, Ersteller/Bearbeiter |
| `fibu_receipts` | Belege: Originalname, Typ, Größe, SHA-256, Ablagepfad, Uploader |
| `fibu_tasks` | Aufgaben je Mandant |
| `fibu_client_profiles` | Steuerliche Stammdaten (Rechtsform, StNr., USt-IdNr.); die Mandantentabelle selbst bleibt unverändert |

Belege bekommen bewusst eine **eigene** Tabelle und einen eigenen Ordner (`data/fibu_receipts/<Mandant>/<uuid>.<ext>`),
nicht `documents`. Begründung: Akten-Dokumente laufen durch Datenschutzprüfung, KI-Pipeline und Chat-Kontext. Belege
werden dagegen nur lokal gespeichert, nicht ausgelesen und nicht an eine KI übergeben.

**Code:**

| Datei | Inhalt |
|---|---|
| `app/models/fibu.py` | Modelle |
| `migrations/versions/schritt3_040_fibu_mvp.py` | Migration |
| `app/fibu/service.py` | Fachlogik |
| `app/web/fibu_router.py` | Routen unter `/dashboard/fibu` |
| `app/web/templates/fibu.html` | Seite |
| `app.css` | Block „Finanzbuchhaltung“ |

Außerdem: neue Icons in `_icons.html`, Navigationseintrag mit „NEU“-Kennzeichnung in `base.html`, Berechtigung
`PERM_FIBU_MANAGE` und die Einstellung `fibu_receipt_storage_dir`.

### Funktionen

| Bereich (Reiter) | Funktion |
|---|---|
| Kopf | Mandant und Buchungsjahr (Vor-, aktuelles und Folgejahr sowie Jahre mit Buchungen); ein Wechsel lädt alle Daten und Kennzahlen neu. „Belege hochladen“ als geteilter Knopf mit Menü (Neue Buchung, Belege, Aufgabe, DATEV-Entwurf) |
| Übersicht | 4 Kennzahlen aus gespeicherten Buchungen: Umsatz brutto, Kosten brutto, vorläufiges Ergebnis netto, USt-Zahllast des Quartals mit Fälligkeit, jeweils mit Vorjahresvergleich (ohne Vorjahresdaten: „kein Vorjahreswert“). „Letzte Buchungen“ mit Suche, Kontofilter, Seiten und Einträgen pro Seite; Zeilenmenü (Beleg ansehen, Details und Verlauf, Bearbeiten, Buchen, Stornieren). Belege per Ablegen oder Klick. Offene Aufgaben (abhaken, neu anlegen; USt-Voranmeldung als berechnete Frist). Schnellaktionen: USt-Voranmeldung, OP-Liste, Kontenauszug (Kontenblatt), DATEV-Export. Mandanteninformation, aktuelle Auswertungen |
| Buchen | Einnahme oder Ausgabe erfassen („als offen speichern“ bzw. „speichern und buchen“), offene Buchungen bearbeiten, buchen, stornieren (mit Pflichtgrund). Verlauf aus dem Audit-Log mit Änderungen „alt → neu“. Buchungsjournal |
| Belege | Upload mehrerer PDF-, JPG- und PNG-Dateien bis 20 MB; Prüfung von Dateiendung **und** Dateianfang; Anzeigen, Herunterladen, Zuordnung zu Buchungen, Status Neu/Zugeordnet, „Buchung erfassen“ mit vorausgewähltem Beleg |
| Konten | Konto anlegen, aktivieren und deaktivieren; Soll, Haben und Saldo je Konto; optional „Startkonten“ aus der Designvorlage (ausdrücklich **kein** geprüfter Kontenrahmen) |
| USt-Voranmeldung | vorbereitende Quartalsübersicht: Bemessungsgrundlagen und Steuer je Satz (19/7/0 %), Vorsteuer, Zahllast, Fälligkeit |
| Auswertungen | BWA-ähnliche Übersicht (netto, je Konto und Monat), Summen- und Saldenliste (Soll = Haben), Kontenblatt mit laufendem Saldo, OP-Liste (vereinfacht), DATEV-CSV-Entwurf |
| Einstellungen | Rechtsform, Steuernummer, USt-IdNr. (Formatprüfung) |
| Jahresabschluss | sichtbar als „nicht verfügbar – nicht Teil des MVP“, kein Link |

### Regeln der Buchungslogik

- **Beträge:** Erfasst wird brutto, gespeichert in ganzen Cent. Netto und Steuer werden kaufmännisch gerundet,
  Netto + Steuer = Brutto.
- **Plausibilität:**
  - Pflichtfelder: Datum, Belegnummer, Beschreibung, Betrag größer 0, Steuersatz 0/7/19 %.
  - Kontenzuordnung: Einnahme = Erlöskonto an Bank/Forderung/Sonstiges; Ausgabe = Aufwandskonto an
    Bank/Verbindlichkeit/Sonstiges; Konto ≠ Gegenkonto; keine deaktivierten Konten.
  - Konto, Gegenkonto und Beleg müssen zum **selben Mandanten** gehören.
- **Status:**
  - *Offen*: änderbar.
  - *Gebucht*: nicht mehr änderbar, nur noch stornierbar.
  - *Storniert*: bleibt sichtbar, zählt in keiner Summe.
  - Es wird nichts gelöscht.
- **Doppelte Buchführung in den Auswertungen:**
  - Einnahme: Gegenkonto (Soll, brutto) an Erlöskonto (Haben, netto) und Umsatzsteuer.
  - Ausgabe: Aufwandskonto (Soll, netto) und Vorsteuer an Gegenkonto (Haben, brutto).
  - Steuerzeilen sind berechnet, keine eigenen Konten.
- **Kennzahlen und Auswertungen** enthalten offene und gebuchte Buchungen; die Seite weist das aus. Der
  DATEV-Entwurf enthält nur gebuchte.
- **Nachvollziehbarkeit:** Jede Änderung erzeugt ein Audit-Ereignis: Buchung (erfasst, geändert mit alt → neu,
  gebucht, storniert), Beleg, Konto, Aufgabe, Stammdaten, Startkonten, DATEV-Export.

### Rechte und Mandantentrennung (serverseitig)

**Lesen** dürfen alle angemeldeten Rollen. **Ändern** dürfen nur Admin und Anwalt (`PERM_FIBU_MANAGE`, CSRF-geprüft).
Gemeint sind Buchungen, Belege, Konten, Aufgaben, Stammdaten und der DATEV-Export.

- **Mitarbeiter** sehen Zahlen und Listen, aber keine Formulare oder Upload-Knöpfe. Jeder Schreibversuch ergibt
  HTTP 403.
- **Jede Abfrage** filtert nach Datensatz-ID **und** Mandant. Eine fremde ID verhält sich wie eine nicht vorhandene
  (HTTP 404).
- **Archivierte Mandanten** sind nur lesbar.
- **Beleg-Download** liefert nur Dateien innerhalb des Belegordners und nur zum angegebenen Mandanten.
- **Originaldateiname:** Er landet nie im Speicherpfad (Schutz vor Path Traversal).
- **DATEV-CSV:** Textfelder, die mit `=`, `+`, `-` oder `@` beginnen, werden entschärft (Schutz vor
  Formel-Injection).

## Nachweise (`aufnahmen/`)

**Größen:** 1920×1080, 1280×720 und 960×600, jeweils hell und dunkel:

- `01` Übersicht, im Fenster;
- `01b` Übersicht als ganze Seite, ebenfalls hell und dunkel;
- `02` Buchen.

**Weitere Zustände** bei 1280×720 hell:

| Datei | Inhalt |
|---|---|
| `03` | Belege |
| `04` | Konten |
| `05` | USt |
| `06` | BWA |
| `07` | Summen- und Saldenliste |
| `08` | Kontenblatt |
| `09` | OP-Liste |
| `10` | DATEV-Entwurf |
| `11` | Einstellungen |
| `12` | Buchung bearbeiten mit Verlauf |
| `13` | stornierte Buchung mit Grund und Verlauf |
| `14` | Mandantenwechsel per Auswahlfeld |
| `15` | Jahreswechsel auf 2025, Mandant ohne Buchungen: alle Kennzahlen 0,00 |
| `16` | Zeilenmenü |
| `17` | Menü des Primärknopfs |
| `18` | Mitarbeiter, nur lesend |

**Messung (32 Zustände):** kein Querscrollen, kein Textüberlauf in Kennzahlen, Karten, Reitern und Knöpfen, keine
JS-Fehler; „Finanzbuchhaltung“ ist aktiv und mit „NEU“ gekennzeichnet.

**Funktionsprüfung über HTTP** gegen die laufende Instanz (`messwerte.json` → `funktion`):

| Prüfung | Ergebnis |
|---|---|
| Kennzahlen Mandant A | 125.430,50 € / −87.214,20 € / 28.552,93 € / 642,77 € (USt Q4, fällig 10.01.2027) |
| Kennzahlen Mandant B | 4.760,00 € / −1.190,00 € / 3.000,00 € / −190,00 €; keine Buchung von A sichtbar |
| USt Q4 nachgerechnet | Erlöse 19 % brutto 4.630,00 → netto 3.890,76 / USt 739,24; Vorsteuer 96,47 (Storno nicht enthalten); Zahllast 642,77 |
| Summen- und Saldenliste | Soll 212.644,70 = Haben 212.644,70 |
| Fremde Buchung lesen / stornieren, fremder Beleg (über Mandant B) | 404 / 404 / 404 |
| Konto von B in Buchung von A | 400 „Bitte ein Konto dieses Mandanten wählen.“ |
| Pflichtfeld fehlt | 400 „Bitte eine Belegnummer eingeben.“, Eingaben bleiben erhalten |
| `.exe` als Beleg, `.pdf` ohne PDF-Inhalt | 400 mit verständlicher Meldung, nichts gespeichert |
| Mitarbeiter: buchen / DATEV-Export | 403 / 403 |
| Ohne CSRF-Feld | 422 (abgelehnt); falsches Token: 403 (Unit-Test) |
| DATEV-Entwurf | 16 gebuchte Zeilen, ohne offene und stornierte Buchungen; z. B. `1250,00;S;EUR;1000;8400;;2710;RE-2026-105;Beratung Projekt XY;19 %` |

**Abgleich mit der Referenz:** Aufbau, Reihenfolge und Gestaltung folgen dem Bild:

- Brotkrumen; Kopf mit Mandant, Jahr und grünem geteiltem Knopf; acht Reiter;
- vier Kennzahlkarten mit getönten Icons;
- Buchungstabelle mit farbigen Beträgen und Status-Pillen, Seitenleiste mit Seitengröße;
- rechte Spalte mit Belegen, Aufgaben und Schnellaktionen; darunter Mandanteninformation und Auswertungen.

Bewusste Abweichungen:

- **Ergebnis netto:** Die Referenz rechnet das Ergebnis als Brutto-Differenz (125.430,50 − 89.240,15). Lexono weist
  es netto aus und beschriftet es so.
- **Schnellaktion „Kontenauszug“:** Die Referenz zeigt „Kontenauszug importieren“. Ein Bankimport existiert nicht,
  deshalb führt die Kachel zum Kontenblatt. Keine Attrappe.
- **Kopfzeile:** Hilfe-Icon und Avatar oben rechts sowie die Navigationspunkte „Auswertungen“ und „Einstellungen“
  der Referenz wurden nicht übernommen. Die bestehende Lexono-Kopfzeile und die Hauptnavigation bleiben
  unverändert; ergänzt wurde nur „Finanzbuchhaltung“.
- **Breakpoints:** Bei 1280 stehen die rechte Spalte und die unteren Karten unter der Tabelle. Bei 960 gibt es
  2×2 Kennzahlen; die Reiter scrollen waagerecht.

## Tests

- **Neu, `tests/test_fibu.py`** (37 Tests):
  - **Berechnungen:** Betragseingabe (deutsch und technisch, ungültige Werte), Netto/Steuer-Aufteilung (Summe stimmt
    für alle Beträge), Fälligkeit.
  - **Auswertungen:** Kennzahlen inklusive Vorjahr und Storno, Summen- und Saldenliste (Soll = Haben), BWA,
    USt-Quartal, DATEV-Entwurf (nur gebuchte, CSV-Injection entschärft).
  - **Buchungen:** Plausibilitätsprüfungen (8 Fälle), Kontenzuordnung je Richtung, Lebenszyklus
    erfassen → ändern → buchen → stornieren mit Audit-Verlauf, archivierter Mandant.
  - **Mandantentrennung:** Service (5 Zugriffswege, fremde Konten und Belege).
  - **Belege:** Typprüfung, Größe, Path Traversal.
  - **Web:** Seite, Navigation, Reiter, Mandantenwechsel, keine Demo-Zahlen; Anlegen, Storno und Upload per HTTP;
    fremde IDs 404; Mitarbeiter 403; CSRF; Anmeldung erforderlich.
  - **Migration:** nur neue Tabellen, bestehende Tabellen-DDL unverändert, nichts vorbelegt, Downgrade entfernt alles.
- **Regression:** 175 Testdateien (Web/UI, Chat, Datenschutz/Privacy, Pop-up, Migrationen, Rechte, Update und
  Installation; Liste wie bei der Installation von 64e1300 plus FiBu, Migrationen und Rechte):
  - **5 022 bestanden**, 7 xfailed, 10 übersprungen, **4 fehlgeschlagen**.
  - Die 4 Fehler sind ausschließlich die bekannten Logo-Tests in `test_design_refresh.py`. Die Logos sind im
    Arbeitsbaum fremd gelöscht und wurden bewusst nicht wiederhergestellt.
  - **Ablauf:** Der Lauf in Blöcken zu je 12 Dateien wurde nach 12 von 15 Blöcken von Claude Code wegen
    Speichermangels beendet. Die übrigen 31 Dateien liefen nach Rückfrage im Vordergrund nach.
  - **Instabiler Test:** `test_chat_service.py::test_send_message_traces_routing_and_downstream_steps_under_one_trace_id`
    schlug in einem von drei Durchläufen des Blocks 1 fehl. Einzeln besteht er 3 von 3, mit seiner Datei 113 von 113.
    Er liegt im Chat-Sendepfad, den diese Änderung nicht berührt. Das deutet auf ein zeitabhängiges Verhalten hin,
    nicht auf einen FiBu-Fehler. Nicht behoben (fremder Bereich).

## Grenzen des MVP und offene Anforderungen

Das MVP ist eine **digitale Erfassungs- und Verwaltungsoberfläche**. Ausdrücklich **nicht** geprüft oder umgesetzt:

1. **GoBD:**
   - Keine Festschreibung im Rechtssinn: „Gebucht“ sperrt nur die Bearbeitung in der Oberfläche; die SQLite-DB
     selbst ist änderbar.
   - Keine Unveränderbarkeit der Belege (Hash ist gespeichert, wird aber nicht laufend geprüft).
   - Keine Verfahrensdokumentation, keine Aufbewahrungs- und Löschfristen.
2. **Kontenrahmen:** Die Startkonten stammen aus der Designvorlage, kein SKR03/SKR04. Ein geprüfter Kontenrahmen,
   Automatikkonten und Steuerschlüssel fehlen.
3. **Steuer:**
   - Nur 19/7/0 % und vierteljährliche Voranmeldung.
   - Keine Dauerfristverlängerung, keine Wochenend- oder Feiertagsverschiebung.
   - Kein Reverse Charge, keine innergemeinschaftlichen Umsätze, keine Ist- oder Soll-Versteuerung-Einstellung.
   - Keine ELSTER-Übermittlung. Alle Werte sind vorbereitend.
4. **DATEV:** Nur ein CSV-Entwurf mit Kernspalten. Es fehlen der EXTF-Header, BU-Schlüssel und Pflichtfelder; ein
   Import ist nicht validiert.
5. **OP-Liste:** Ein Zahlungsausgleich ist nicht abgebildet; die Liste zeigt alle Buchungen gegen Forderungs- und
   Verbindlichkeitskonten.
6. **Nicht vorhanden:** Bankimport (Kontoauszüge), Belegerkennung (OCR/KI), Jahresabschluss, Mehrwährungen,
   Kostenstellen.
7. **Fachliche Entscheidung offen:** Dürfen Mitarbeiter (z. B. Buchhaltungskräfte) Buchungen erfassen? Derzeit nur
   lesend.
8. **Nicht installiert:** Die Desktop-App enthält das Modul erst nach Neubau und Installation. Beim ersten Start
   migriert die produktive DB auf `schritt3_040` (nur neue, leere Tabellen).
