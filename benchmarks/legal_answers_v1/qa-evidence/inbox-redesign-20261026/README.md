# Posteingang nach Referenzbild – Umsetzung und visuelle QA (26./27.10.2026)

Alle Aufnahmen stammen aus der **laufenden Anwendung** (Quellstand, `python run.py serve`):

- **Testumgebung:** isolierte QA-Ablagen im Job-Ordner, ausschließlich **synthetische** E-Mails (`*.example`,
  „QA-SYNTH“), Konto `qa-inbox@lexono.test`.
- **Keine Cloud:** Cloud-Endpunkt tot (`127.0.0.1:9`), E-Mail-Abruf aus, Proxy auf einen toten Port. Es verlässt nichts
  den Rechner. Die produktive Datenbank wurde nicht verwendet.
- **Aufnahmewege:**
  - headless Edge per CDP mit exakten Viewports (DPR 1);
  - zusätzlich per `PrintWindow` aus dem **echten WebView2-Desktopfenster** (`nachher/webview2/`, maximiert,
    1536×842 CSS-Pixel bei 125 %).

## Referenz

- **`referenz-posteingang-verbindlich.png`:** das vom Owner am 26.10. nachgereichte, **verbindliche** Referenzbild.
  Danach wurde abgeglichen.
- **`nachher-textvorgabe/`:** die erste Fassung (Commit `221ed86`), entstanden vor Eingang des Bildes nach der
  Textvorgabe und `assets/ux-ui/04_posteingang_nachricht_detail.png` (`referenz-04_…png`). Sie bleibt zum Vergleich
  erhalten.
- **`vorher/`:** der Stand vor dem Auftrag.
- **`nachher/`:** der Stand nach dem Abgleich mit dem verbindlichen Bild.

## Abgleich mit dem verbindlichen Referenzbild (`nachher/`)

| Element im Referenzbild | Umsetzung | Status |
|---|---|---|
| Seitenleiste: Logo, grüne Schaltfläche „Neue Nachricht“ | wie Referenz; öffnet einen Chat mit vorausgefülltem E-Mail-Entwurfsauftrag (Lexono versendet nichts) | ✔ |
| Navigation mit Zählern (Posteingang 12, Aufgaben 3) | Posteingang = echte Zahl ungelesener E-Mails, Aufgaben wie bisher | ✔ (Badge-Farbe: bestehendes graues Token statt Grün) |
| KI-Status lokal/Cloud | bestehende Statuszeilen mit echtem Zustand | ✔ (ohne Modellnamen-Unterzeile) |
| Kopfzeile: Suche, Glocke, Dark Mode, Profil-Avatar | Suche, Glocke, **Dark-Mode-Umschalter (neu)**; Umschlag und Abmelden bleiben | ⚠ siehe Abweichungen |
| Titel „Posteingang“ + Beschreibung | wie Referenz | ✔ |
| Rechts: „42 E-Mails (12 ungelesen)“ + „Letzter Abruf: heute, 08:12 Uhr“ | echte Zahlen; Abrufzeile = echter Zustand dieser Instanz | ✔ |
| „E-Mail-Konto verwalten“ | Link auf den bestehenden IMAP-Bereich (Admin) | ✔ |
| Tabs mit Zahlen, aktiver Tab grün hinterlegt | sechs Tabs, Zahlen aus denselben Bedingungen wie die Filter | ✔ |
| Filterzeile „Mandant / Akte / Zeitraum“ mit Beschriftung, Suche rechts, Filter-Symbol | wie Referenz; Filter-Symbol öffnet „Weitere Filter“ (Sortierung, Zurücksetzen) | ✔ |
| Liste: Kästchen, Avatar, Absender, Betreff, Vorschau, Tags, Anhang, Zeit, grüner Lesepunkt | ab 1600 px vierspaltig wie Referenz; Tags = Rechtsgebiet der Akte + „Akte: Aktenzeichen“ (nur echte Felder) | ✔ |
| Aktive Zeile grün hinterlegt mit grüner Kante | wie Referenz | ✔ |
| Lesebereich: Icon-Leiste (Antworten, Archiv, Löschen, Etikett, …) | Antworten, Akte öffnen, gelesen/ungelesen | ⚠ Archiv/Löschen/Etikett fehlen im Projekt |
| Betreff groß, „Name &lt;Adresse&gt;“, „An:“, Datum rechts | wie Referenz; zusätzlich „Akte:“-Link | ✔ (kein „Cc:“, siehe Abweichungen) |
| Text, „1 Anhang“, Anhangkarte mit Download | wie Referenz | ✔ |
| „Zusammenfassen“ / „Antworten“ | wie Referenz; „Namen schützen“ (Datenschutz) darüber | ✔ (ohne Dropdown-Pfeil am Antworten-Button) |

## Szenarien und Größen (`nachher/`, gleiche Dateinamen auch in `vorher/` bzw. `nachher-textvorgabe/`)

| Datei | Größe | Inhalt |
|---|---|---|
| `*-01-posteingang` | 1920×1080, 1280×720, 960×600 · hell/dunkel | Standardansicht |
| `1920x1080-hell-02-nachricht-mit-anhang` | 1920×1080 | ausgewählte Nachricht mit Anhang |
| `1920x1080-hell-03-filter-nicht-zugeordnet` | 1920×1080 | Tab „Nicht zugeordnet“, Zuordnungskarten |
| `1920x1080-hell-04-suche-absender` | 1920×1080 | Absendersuche |
| `1920x1080-*-05-leerer-posteingang` | 1920×1080 · hell/dunkel | leerer Posteingang |
| `1920x1080-hell-06-ungelesene-nachrichten` | 1920×1080 | 4 ungelesen (grüner Punkt, fett), Zähler 4 |
| `1920x1080-hell-07-ausgewaehlte-nachricht-geoeffnet` | 1920×1080 | ungelesene Nachricht geöffnet → gelesen |
| `1920x1080-hell-08-auswahl-sammelaktion` | 1920×1080 | 2 ausgewählt, Sammelaktionen aktiv |
| `1920x1080-hell-09-tab-mit-anhang` | 1920×1080 | Tab „Mit Anhang“ |
| `1920x1080-hell-16-weitere-filter` | 1920×1080 | Panel „Weitere Filter“ geöffnet |
| `1280x720-hell-10-liste-gescrollt` | 1280×720 | Liste gescrollt, Lesebereich und Seite nicht |
| `1920x1080-dunkel-11-umschalter-kopfzeile` | 1920×1080 | Dark Mode über den Umschalter |
| `1920x1080-hell-12-zugeordnet-mit-anhaengen-und-aktionen` | 1920×1080 | Lesebereich vollständig |
| `1920x1080-hell-13-antwortfunktion-geoeffnet` | 1920×1080 | „Antworten“ → Chat-Entwurf; ohne Cloud ehrliche Meldung, nichts gesendet |
| `1920x1080-hell-14-neue-nachricht-entwurf-im-chat` | 1920×1080 | „Neue Nachricht“ → Chat mit vorausgefülltem Auftrag |
| `*-15-nachricht-mit-anhang` | 960×600 hell/dunkel, 1280×720 dunkel | kleine Fenster |
| `webview2/01/02` | natives Fenster, maximiert | hell/dunkel |

**Responsiv:**

- **ab 1600 px:** vierspaltige Zeilen wie in der Referenz.
- **darunter:** zweizeilig (Absender über dem Betreff), Filterzeile einzeilig.
- **unter 1100 px:** Feldbeschriftungen und Unterzeilen ausgeblendet.
- **unter 1000 px:** kompakter Kopf, Tags ausgeblendet.

## Funktion (gegen die QA-Datenbank geprüft, `nachher/messwerte_nachher.json` → `funktion`)

- **Ungelesen:** 4 in der Datenbank = 4 markierte Zeilen = Zähler 4.
- **Öffnen:** Klick öffnet die Nachricht per HTMX. Danach ist `read_at` gesetzt, die Zeile nicht mehr ungelesen, die URL
  `/dashboard/inbox/<id>`.
- **Automatische Vorauswahl:** zählt nicht als gelesen (Test).
- **Auswahl:** Ein Klick aufs Kästchen öffnet nichts; der Zähler zeigt „2 ausgewählt“. „Als ungelesen markieren“ wirkt in
  der Datenbank, nur bei eingehenden Nachrichten.
- **Tabs:** Die Tab-Zahl entspricht der Trefferzahl (Test über alle sechs Tabs). Nach einem Tabwechsel ist die
  Verlaufs-URL die volle Seite.
- **Sortierung:** schaltet in beide Richtungen um. Vorher schickte der Umschalter nach dem ersten Klick denselben Wert
  erneut (behoben).
- **Scrollen:** Die Liste scrollt unabhängig (400 px), Lesebereich und Seite bleiben bei 0.
- **Dark Mode:** wirkt sofort; Admins speichern ihn über die bestehende Einstellungsroute (QA-`.env`: `UI_THEME="dark"` →
  `"light"`).
- **Antworten / Neue Nachricht:** erzeugen nur Entwürfe im Chat, ohne Versand.
- **Querscrollen:** bei keiner Aufnahme.

## Tests

- **Posteingang:** `test_web_inbox.py` + neu `test_web_inbox_reference_layout.py`: **119/119** bestanden.
- **Regression:** 66 Dateien (Web/Chat/UI, Migrationen, Mail, Design/Theme, Auth/CSRF, Akten/Mandanten/Aufgaben/
  Einstellungen) in 6 Gruppen:
  - **1606 bestanden**, 1 übersprungen, 1 xfailed;
  - **4 fehlgeschlagen**, ausschließlich die bekannten Logo-Tests in `test_design_refresh.py` (fremdgelöschte Logos,
    vorbestehend, nicht verändert).
- **Angepasste bestehende Tests:**
  - „E-Mail-Konto verwalten“ (Einzahl laut Auftrag, es gibt genau ein Postfach).
  - Der Test „Neuen Chat starten auf jeder Seite“ prüft jetzt eine Nicht-Posteingangsseite, weil das verbindliche Bild
    im Posteingang nur „Neue Nachricht“ zeigt.

## Nicht vorhanden bzw. bewusst nicht gebaut (keine Fake-Steuerelemente)

- **Archivieren, Löschen, Etiketten** (Icon-Leiste der Referenz): kein Datenmodell, keine Route. Das endgültige Löschen
  von E-Mails wäre zudem eine fachliche Entscheidung (Aufbewahrungspflichten).
- **Versand / echtes Verfassen:** Lexono versendet nichts selbst (Grundregel). „Neue Nachricht“ und „Antworten“ erzeugen
  prüfbare Entwürfe.
- **„Cc:“:** Das Datenmodell speichert keinen Cc-Header. Ein „Cc: —“ würde fälschlich „kein Cc“ behaupten, deshalb
  weggelassen.
- **Dropdown am „Antworten“-Button** (Allen antworten / Weiterleiten): Diese Varianten existieren nicht.
- **Mehrere Konten, beA:** nicht vorhanden.

## Bekannte Abweichungen

- **Kopfzeile:** Das Posteingangsbild zeigt Glocke, Mond und Profil-Avatar. Das ebenfalls vom Owner stammende
  Dashboard-Bild (`assets/ux-ui/Lexono Kanzlei-Dashboard im Darkmode.png`) zeigt dagegen Umschlag, Glocke und Abmelden.
  Die Kopfzeile ist seitenübergreifend, deshalb blieb sie bis auf den neuen Mond wie sie ist (**Owner-Entscheidung
  offen**).
- **Seitenleiste:**
  - Badge-Farbe und Form des aktiven Punkts folgen dem bestehenden, seitenübergreifenden Stil (grau bzw. volle Breite
    statt der grünen Pille der Referenz).
  - Der Profilbereich zeigt Name, Rolle und Menü statt Kanzleiname und Zahnrad.
- **„Neuen Chat starten“:** fehlt im Posteingang (Referenz); auf allen anderen Seiten bleibt er (Owner-Korrektur
  26.09.).
- **Unzugeordnete Nachrichten:** zeigen bewusst keine Zusammenfassen-/Antworten-Buttons (Aktenisolation, bestehender
  Test).
- **Lesestatus:**
  - gilt kanzleiweit, nicht pro Benutzer;
  - beim Update gelten bestehende Nachrichten als gelesen (Migration `schritt3_038`).
- **„Letzter Abruf“:** zeigt den letzten Lauf **dieser** Instanz; nach einem Neustart steht dort ehrlich „Noch kein Abruf
  seit dem Start“.
- **Darstellung:**
  - Die Avatarfarbe „grün“ ist im hellen Design Navy (bestehendes Token `--seal-green`).
  - Pastell-Avatare wie in der Referenz wären eine neue Farbsprache und wurden deshalb nicht eingeführt.
- **QA-Hinweis Entwicklungsmodus:** Ohne `SESSION_SECRET_KEY` meldet jede Einstellungsänderung ab (vorbestehend). Die
  QA-Ablage nutzt deshalb einen Wegwerf-Schlüssel.
- **Installierte EXE:** in diesem Auftrag weder neu gebaut noch installiert; geprüft wurde der Quellstand, auch im
  WebView2-Fenster.
