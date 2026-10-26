# Posteingang nach Referenzbild – Umsetzung und visuelle QA (26.10.2026)

Alle Aufnahmen stammen aus der **laufenden Anwendung** (Quellstand, `python run.py serve`).

- **Isolierte QA-Ablagen** im Job-Ordner mit ausschließlich **synthetischen** E-Mails (`*.example`, „QA-SYNTH“) und dem
  Konto `qa-inbox@lexono.test`.
- **Cloud-Endpunkt tot** (`127.0.0.1:9`), E-Mail-Abruf aus, Proxy auf einen toten Port: Es verlässt nichts den Rechner.
- Die produktive Datenbank wurde nicht verwendet.
- **Aufnahmewege:**
  - headless Edge per CDP mit exakten Viewports (DPR 1);
  - zusätzlich zwei Aufnahmen per `PrintWindow` aus dem **echten WebView2-Desktopfenster** (`nachher/webview2/`).

## Referenz

Im Auftrag war ein Referenzbild „beigefügt“, es kam aber nicht an. Weder im Chat noch im Repository noch in
Downloads, Desktop oder Bilder lag ein Posteingangsbild mit den beschriebenen Tabs. Grundlage waren deshalb:

1. die **Textvorgabe des Auftrags** (Seitenleiste, Kopfzeile, Kopfbereich, Tabs, Filterzeile, Liste, Lesebereich);
2. die nächstliegende Bildreferenz im Repository, `assets/ux-ui/04_posteingang_nachricht_detail.png` (als
   `referenz-04_posteingang_nachricht_detail.png` beigelegt);
3. der bestehende App-Rahmen (`assets/ux-ui/Lexono Kanzlei-Dashboard im Darkmode.png`).

**Sobald das eigentliche Referenzbild vorliegt, ist ein Feinabgleich (Abstände, Proportionen) offen.**

## Szenarien

| Datei (`vorher/` und `nachher/`) | Größe | Inhalt |
|---|---|---|
| `*-01-posteingang` | 1920×1080, 1280×720, 960×600 · hell/dunkel | Standardansicht, erste Nachricht im Lesebereich |
| `1920x1080-hell-02-nachricht-mit-anhang` | 1920×1080 | ausgewählte Nachricht mit Anhang |
| `1920x1080-hell-03-filter-nicht-zugeordnet` | 1920×1080 | Tab „Nicht zugeordnet“, Zuordnungskarten |
| `1920x1080-hell-04-suche-absender` | 1920×1080 | Suche nach Absender |
| `1920x1080-*-05-leerer-posteingang` | 1920×1080 · hell/dunkel | leerer Posteingang (eigene leere Instanz) |
| `nachher/1920x1080-hell-06-ungelesene-nachrichten` | 1920×1080 | 4 ungelesene Nachrichten (Punkt, fett), Zähler 4 in der Navigation |
| `nachher/1920x1080-hell-07-ausgewaehlte-nachricht-geoeffnet` | 1920×1080 | ungelesene Nachricht per Klick geöffnet → gelesen |
| `nachher/1920x1080-hell-08-auswahl-sammelaktion` | 1920×1080 | 2 Kästchen gewählt, „2 ausgewählt“, Sammelaktionen aktiv |
| `nachher/1920x1080-hell-09-tab-mit-anhang` | 1920×1080 | Tab „Mit Anhang“ (HTMX), 5 Zeilen = 5 in der DB |
| `nachher/1280x720-hell-10-liste-gescrollt` | 1280×720 | Liste gescrollt, Lesebereich und Seite nicht |
| `nachher/1920x1080-dunkel-11-umschalter-kopfzeile` | 1920×1080 | Dark Mode per Umschalter in der Kopfzeile |
| `nachher/1920x1080-hell-12-zugeordnet-mit-anhaengen-und-aktionen` | 1920×1080 | Lesebereich: Aktionsleiste, Absenderblock, 2 Anhänge, Zusammenfassen/Antworten |
| `nachher/1920x1080-hell-13-antwortfunktion-geoeffnet` | 1920×1080 | „Antworten“ → Chat-Entwurf; ohne Cloud ehrliche Meldung, nichts gesendet |
| `nachher/1920x1080-hell-14-neue-nachricht-entwurf-im-chat` | 1920×1080 | „Neue Nachricht“ → neuer Chat mit vorausgefülltem Entwurfsauftrag, nicht gesendet |
| `nachher/*-15-nachricht-mit-anhang` | 960×600 hell/dunkel, 1280×720 dunkel | Lesebereich bei kleinen Fenstern |
| `nachher/webview2/01/02` | natives Fenster maximiert, 1536×842 CSS bei 125 % | echtes WebView2-Fenster, hell/dunkel |

**Messwerte:**

- `vorher/messwerte_vorher.json`, `nachher/messwerte_nachher.json`: Viewport, Querscrollen, Breiten von Liste und
  Lesebereich, eigener Scroll je Bereich.
- Abschnitt `funktion`: Prüfungen gegen die QA-Datenbank, siehe unten.

## Vorher → nachher (Abweichungen von der Vorgabe und was geändert wurde)

| Bereich | vorher | nachher |
|---|---|---|
| Seitenleiste | „Neuen Chat starten“, kein Zähler am Posteingang | grüne Hauptaktion **„Neue Nachricht“**, darunter weiterhin „Neuen Chat starten“; Zähler **ungelesener** Nachrichten am Posteingang; KI-Status unverändert |
| Kopfzeile | Suche, Umschlag, Glocke, Abmelden | zusätzlich **Dark-Mode-Umschalter** (Mond/Sonne) |
| Kopfbereich | schmal zentriert (max. 1280 px), gegen die Liste versetzt; nur „Nachrichten insgesamt/ohne Aktenzuordnung“ | volle Breite; Icon, Titel, Beschreibung; Statistik **E-Mails / ungelesen / nicht zugeordnet / mit Anhang**; **Abrufstatus**; „E-Mail-Konto verwalten“ |
| Tabs | unterstrichene Links | Tabs mit grüner Aktiv-Linie |
| Filterzeile | Felder ohne Ausrichtung, Suche schmal; bei 960 px zweizeilig | Mandant, Akte, Zeitraum, Suche (füllt die Zeile, Lupe im Feld), Sortierung und „Zurücksetzen“ in **einer** Zeile, auch bei 960 px |
| Liste | Absender als „Name &lt;adresse&gt;“, Mono-Zeitstempel in UTC „26.10. 21:36“, Tags unter der Vorschau | **Auswahlkästchen**, größerer Avatar, nur Absendername, Zeit wie in der Referenz (heute Uhrzeit, „Gestern“, sonst Datum, **Ortszeit**), Vorschau mit Anhangsymbol und Tags in einer Zeile, **Lesestatus** (Punkt, fett); Listenkopf mit „alle auswählen“, Zähler, Sammelaktionen |
| Lesebereich | Tags, Betreff, „Von:/An:“-Zeilen; „ANHÄNGE (2)“ als Mono-Label; Karten untereinander; „Aktion“ als Mono-Label | **Aktionsleiste** (Tags; Akte öffnen; als gelesen/ungelesen markieren), Betreff, **Absenderblock** mit Avatar, Name, Adresse, „an:“, Akte, Datum rechts; Text; „2 Anhänge“ mit **Kartenraster**; **Aktionskarte** mit Zusammenfassen/Antworten und Hinweis „Lexono versendet nichts selbst“ |
| 960×600 | Kopf und Filter nahmen 300 px ein | kompakter Kopf, Liste beginnt bei ca. 230 px |

## Funktion (Prüfungen gegen die QA-Datenbank, `nachher/messwerte_nachher.json` → `funktion`)

- **Ungelesen:** 4 in der Datenbank = 4 markierte Zeilen = Zähler „4“ in der Navigation.
- **Öffnen:** Klick öffnet die Nachricht per HTMX im Lesebereich. Die Zeile verliert „ungelesen“, in der Datenbank wird
  `read_at` gesetzt, die URL lautet `/dashboard/inbox/<id>`.
- **Automatische Vorauswahl:** Die beim Laden vorausgewählte erste Nachricht wird bewusst **nicht** als gelesen markiert
  (Test).
- **Auswahl:**
  - Ein Klick auf ein Kästchen öffnet die Nachricht nicht.
  - Der Zähler zeigt „2 ausgewählt“, die Sammelaktionen werden aktiv.
  - „Als ungelesen markieren“ setzt die eingehende Nachricht in der Datenbank zurück. Für ausgehende Nachrichten wird
    kein Lesestatus geführt.
- **Tab „Mit Anhang“:** 5 Zeilen = 5 Nachrichten mit Anhang in der Datenbank. Die Verlaufs-URL ist jetzt die volle Seite
  (`/dashboard/inbox?...`), vorher das nackte Listenfragment.
- **Scrollen:** Die Liste scrollt unabhängig (370 px), Lesebereich und Seite bleiben bei 0.
- **Dark-Mode-Umschalter:** wirkt sofort (`data-theme`, localStorage, native Titelleiste). Für Admins wird der Wert
  über die bestehende Route der Einstellungen gespeichert; die `.env` der QA-Ablage stand danach auf `UI_THEME="dark"`
  und nach dem Zurückschalten auf `"light"`.
- **Antworten:** erzeugt einen Chat zur Akte (`/dashboard/chat/<id>`) über den bestehenden, geprüften Weg. Ohne Cloud
  erscheint die ehrliche Fehlermeldung, nichts wurde gesendet.
- **Neue Nachricht:** öffnet `/dashboard/chat?new=1&compose=email`. Das Eingabefeld ist mit „Bitte entwirf eine neue
  E-Mail an “ vorausgefüllt, es wird nichts automatisch gesendet.
- **Querscrollen:** bei keiner Aufnahme.

## Nicht vorhanden bzw. bewusst nicht gebaut (keine Fake-Steuerelemente)

- **Archivieren / Löschen:** existieren im Projekt nicht (kein Datenmodell, keine Route). Sie werden nicht angeboten.
  Das endgültige Löschen von E-Mails wäre zudem eine fachliche Entscheidung (Aufbewahrungspflichten).
- **E-Mail-Versand / echtes Verfassen:** Lexono versendet nichts selbst (Grundregel), der Postausgang ist eine
  Warteschlange. „Neue Nachricht“ und „Antworten“ erzeugen deshalb prüfbare **Entwürfe** im Chat.
- **„Alle Konten“, beA:** nur ein IMAP-Postfach, kein Mehrkonten-Modell; beA ist nicht angebunden.
- **„In Akte speichern“** (Referenz 04): entspricht der bestehenden Zuordnung (Vorschlagskarte/„Zuordnen“) bei nicht
  zugeordneten Nachrichten. Zugeordnete Nachrichten zeigen stattdessen „Akte öffnen“.
- **„Weitere Filter“:** vorhanden sind Sortierung und „Zurücksetzen“. Weitere Filterkriterien gibt das Datenmodell nicht
  her.

## Bekannte Abweichungen / offene Punkte

- **Das eigentliche Referenzbild fehlt** (siehe oben). Ein pixelgenauer Abgleich ist erst nach dessen Übergabe möglich.
- **Seitenleiste:** „Neuen Chat starten“ bleibt unter „Neue Nachricht“ sichtbar. Grund ist die ausdrückliche
  Owner-Korrektur vom 26.09., dass dieser Einstieg auf jeder Seite bleibt (Test `test_web_inbox.py`).
- **Unzugeordnete Nachrichten:** zeigen bewusst **keine** Zusammenfassen-/Antworten-Buttons, nur einen Hinweis
  (Aktenisolation, bestehender Test).
- **Lesestatus:**
  - gilt kanzleiweit, nicht pro Benutzer;
  - beim Update gelten **bestehende** Nachrichten als gelesen (Migration `schritt3_038`), sonst wäre der ganze Bestand
    plötzlich ungelesen;
  - fachlich anpassbar, falls ein Lesestatus pro Benutzer gewünscht ist.
- **„Letzter Abruf“:** zeigt den letzten Lauf **dieser** Instanz (Prozessspeicher); nach einem Neustart steht dort ehrlich
  „Noch kein Abruf seit dem Start“. Ohne Postfach: „Kein E-Mail-Postfach eingerichtet“.
- **Darstellung:**
  - Das Tag „ausgehend“ hatte im Dark Mode global einen fest hellen Hintergrund. Behoben nur im Posteingang, andere
    Seiten unverändert.
  - Die Avatarfarbe „grün“ ist im hellen Design Navy, weil dort das bestehende Token `--seal-green` = `#101828` ist
    (unverändert übernommen).
- **QA-Hinweis Entwicklungsmodus:** Ohne `SESSION_SECRET_KEY` erzeugt Lexono im Entwicklungsmodus bei jeder
  Einstellungsänderung ein neues Sitzungsgeheimnis und meldet dadurch ab. Das gilt auch für die Einstellungsseite und
  ist vorbestehend; die QA-Ablage nutzt deshalb einen Wegwerf-Schlüssel. In Produktion ist der Schlüssel Pflicht.
- **Tests:** `test_web_inbox.py` + neu `test_web_inbox_reference_layout.py` 117/117 bestanden. Regression über 66 Dateien (Web/Chat/UI, Migrationen, Mail, Design/Theme, Auth/CSRF, Akten/Mandanten/Aufgaben/Einstellungen) in 6 Gruppen: **1604 bestanden**, 1 übersprungen, 1 xfailed, **4 fehlgeschlagen** – ausschließlich die bekannten Logo-Tests in `test_design_refresh.py` (fremdgelöschte Logos unter `assets/branding/`, vorbestehend, nicht verändert). Ein erster Gesamtlauf wurde vom System bei 71 % wegen Speichermangels beendet und deshalb in Gruppen wiederholt.
- **Installierte EXE:** wurde in diesem Auftrag nicht neu gebaut und nicht installiert. Die Prüfung erfolgte am
  Quellstand, auch im WebView2-Fenster.
