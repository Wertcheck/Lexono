# Pseudonymisierungs-Erstversuch, Ladezustand und Lizenzseite (29.10.2026)

**Status:** Quellstand geprüft.

- **Prüfumfang:**
  - Browser-QA mit echten Mausklicks: headless Edge und ein **echtes WebView2-Fenster** (pywebview/edgechromium, dieselbe
    Engine wie die Desktop-App);
  - automatisierte Tests.
- **Kein Build, keine Installation:** Die gebaute EXE ist nicht geprüft.
- **NICHT freigegeben:** keine Datenschutz-, P0-08-, C-14- oder Pilotfreigabe.
- **Daten:** nur synthetisch; das Schreibmodell ist ein lokaler, verzögerter Fake (`qa_serve_fake_writer.py`) ohne
  Netz.
- **Unberührt:** produktive Datenbank, Installation und Lizenzschlüssel.

## 1. Fehlerbild und Ursache (reproduziert vor jeder Änderung)

Ablauf: Dokument anhängen, „Erstelle einen Schriftsatz“ tippen, Senden. Aufnahmen in `vorher/`, Messwerte in
`vorher/vorher-messwerte.json`.

| Schritt | Server-Zustand | Dialog |
|---|---|---|
| 1. Versuch | nur `upload`, **kein Auftrag**, 0 KI-Aufrufe | 3 Optionen `disabled`, Bestätigen ausgeblendet; Klick wirkungslos |
| Abbrechen | unverändert | Text steht wieder im Eingabefeld |
| 2. Versuch (Senden) | `user` + `assistant:blocked` (Dokument-Gate) | bedienbar |
| Bestätigen | 1 KI-Aufruf | Dialog bleibt ~20 s (Fake-Verzögerung) offen, nur graue Fußzeile, kein Ladezustand |

**Ursache „nicht anklickbar“:** Das ist kein Problem von CSS, z-index, Overlay oder Event-Bindung.

- **Ausgangslage:** Mit Dateien ist eine Anfrage nach §57 ausschließlich ein Upload. Der mitgetippte Text bleibt
  bewusst im Browser (`sessionStorage`).
- **Dialogmodus:** Der danach automatisch geöffnete Dialog fragt `pseudonymization-documents`. Dort gibt es keinen
  offenen Auftrag, also gilt `mode = "view"`.
- **Gesperrte Optionen:** `renderQuestions` setzt in diesem Modus `disabled` auf jede Option, und `renderFooter`
  blendet „Bestätigen“ aus. Der vorhandene Hinweis „reine Prüfansicht“ stand nur im Kopf des Prüfpanels.

**Ursache „zweiter Versuch funktioniert“:** Das ist deterministisch, nicht zufällig.

- Erst das zweite Senden (nur Text) legt den Auftrag an.
- Das Dokument-Gate sperrt ihn, die Seite lädt neu, und der Dialog öffnet im Modus `release` mit aktiven Optionen.

**Ursache „eine Minute ohne Rückmeldung“:**

- Der Dialog blieb nach dem Bestätigen offen und gesperrt.
- `handleStream` ignorierte die vorhandenen, inhaltsfreien `status`-Ereignisse des Servers.
- Im Chat-Pfad ersetzte die Blase „Rückfrage offen“ den Ladezustand und blieb bis zum Neuladen stehen.

## 2. Korrektur (§57 bleibt gültig: vor einer bewussten Bestätigung wird nichts gesendet)

| Datei | Änderung |
|---|---|
| `app/web/chat_router.py` | `confirm-release-and-generate` mit optionalem `order`. Ohne offenen Auftrag und nur, wenn die jüngste Nachricht der Upload ist, legt `_record_upload_order` den Auftrag genau einmal an; parallele Klicks: nur der erste bleibt. Scheitern Entscheidungen oder Freigabe, entfernt `_drop_unprocessed_order` den nie verarbeiteten Auftrag (Zustand wie vor dem Klick). `start`-Ereignis mit inhaltsfreiem `drafting`. |
| `app/web/static/js/pseudonymization_center.js` | Liegt ein lokal gehaltener Auftrag vor, öffnet der Dialog im Freigabemodus statt in der Prüfansicht, mit passendem Hinweis und dem Auftrag in der Fußzeile. Bestätigen sendet `order`. Danach schließt der Dialog, und der Chat zeigt die Ladeanzeige. Phasen kommen aus `status`-Ereignissen; Fehler erscheinen als Meldung statt einer Animation. Ohne Server-Ereignis endet die Anzeige nach 5 Minuten (`readWithIdleLimit`). Die URL wechselt nach `start` von der Upload-Ansicht zur Unterhaltung. Reine Prüfansicht: Hinweis direkt an der Frage. |
| `app/web/templates/chat.html` | `window.LexonoChatProgress`: Statusblase mit tatsächlicher Phase und Sekunden, keine Prozente. Sie sperrt das Eingabefeld bis Ergebnis oder Fehler und zeigt Fehler als Meldung. Die Auftragsblase erscheint sofort. Der Chat-Rückfragepfad zeigt nach der Entscheidung wieder einen Ladezustand und verarbeitet `status`/Fehler. |
| `app/web/static/css/app.css` | Hinweis an gesperrten Fragen; Lizenzseite (siehe 4.) |

**Verhalten des Ladezustands:**

| Zeitpunkt | Anzeige |
|---|---|
| Sofort nach Bestätigen | „Pseudonymisierung bestätigt – Dokumente werden freigegeben und erneut geprüft …“ |
| Ab `start` | „Schriftsatz wird erstellt …“ (bei Analyse: „Auftrag wird verarbeitet …“) |
| Je `status` | Serverphase, z. B. „Schriftsatz wird erstellt – Anfrage wird an Claude gesendet… (12 s)“ |
| Erfolg | „Ergebnis wird aufbereitet …“, dann Neuladen; der Ergebnisblock mit offenen Fragen ist unverändert |
| Fehler | rote Meldung in der Blase bzw. gespeicherte Fehlermeldung nach dem Neuladen; Eingabefeld wieder frei |
| Timeout | Meldung „Keine Rückmeldung vom Server seit … – Anzeige beendet …“; kein zweiter KI-Aufruf |

## 3. Nachweise Pseudonymisierung (Browser, echte Klicks, je Szenario frische Instanz)

Aufnahmen und Messwerte (`*-messwerte.json`) liegen in `nachher/`. „KI“ bedeutet Aufrufe des Fake-Schreibmodells.

| Szenario | Ergebnis |
|---|---|
| Erstversuch | Dialog beim 1. Versuch bedienbar (Optionen aktiv, `elementFromPoint` trifft sie). Vor dem Bestätigen: kein Auftrag, 0 KI. Danach: Dialog zu, Ladeanzeige sofort sichtbar, Eingabe gesperrt. Ergebnis: **1 KI, 1 Nutzernachricht, 1 Entwurf**; Ergebnisblock mit „Offene Fragen“; Ladeanzeige entfernt |
| Optionen | „Pseudonymisieren“, „Nicht pseudonymisieren“ und „Unsicher“ einzeln wählbar. Gespeichert wurden genau die gewählten Entscheidungen: `Richter → not_personal`, `Kaufmann → unsure` |
| Abbrechen | Dialog zu, kein Auftrag, keine Entscheidung, 0 KI (auch 3 s später); Eingabe bleibt erhalten. Bewusst erneut senden: genau 1 Auftrag. Danach Bestätigen: **genau 1 KI, 1 Entwurf** |
| Doppelklick | 3 Klicks auf Bestätigen und 2 Klicks auf Senden während der Verarbeitung ergeben **1 KI, 1 Nutzernachricht, 1 Entwurf** |
| Fehler (Provider) | Ladeanzeige endet, Fehlermeldung sichtbar, Eingabe wieder frei, 0 Entwürfe |
| Timeout (Testlimit 6 s) | Meldung sichtbar, keine endlose Animation, Eingabe frei, kein zweiter KI-Aufruf. Nach dem Neuladen: Unterhaltung statt veralteter Prüfansicht |

**Automatisiert:** `tests/test_upload_order_first_attempt.py` (12 Tests, inkl. Namensmarkierung und abgebrochenem Stream) prüft:

- Upload allein sendet nichts;
- Erstversuch pseudonymisiert genau einmal;
- erneutes Bestätigen erzeugt keinen zweiten Auftrag;
- ein vorhandener Auftrag hat Vorrang;
- ein veralteter Textstand oder eine offene Rückfrage entfernt den neuen Auftrag (Neuversuch genau einmal);
- in eine beantwortete Unterhaltung wird kein Auftrag nachgeschoben;
- der parallele Race-Fall;
- das `drafting`-Kennzeichen;
- die statische Verdrahtung.


### 3a. Nachweis im echten WebView2-Fenster (Nachtrag 29.10.2026, `webview2/`)

Alle sechs Szenarien liefen zusätzlich in einem nativen WebView2-Fenster: `webview_launcher.py` startet ein
pywebview-Fenster mit Backend edgechromium auf die isolierte Quellstand-Instanz, und die Steuerung läuft per CDP über
`WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS`. Die Engine-Kennung des Fensters lautet `Chrome/156 … Edg/156` (nicht
„Headless“); das Skript bricht ab, wenn es nicht am WebView2-Fenster hängt.

| Szenario | WebView2-Ergebnis |
|---|---|
| Erstversuch | bedienbar beim 1. Versuch, Ladeanzeige sofort, **1 KI, 1 Entwurf**, offene Fragen im Ergebnis |
| Optionen | alle drei wählbar; gespeichert `Richter → not_personal`, `Kaufmann → unsure` |
| Abbrechen | kein Auftrag, 0 KI, Eingabe erhalten; erneut senden: genau 1 Auftrag, 1 KI, 1 Entwurf |
| Doppelklick | 1 KI, 1 Nutzernachricht, 1 Entwurf |
| Fehler | Meldung sichtbar, keine endlose Ladeanzeige, Eingabe frei |
| Timeout | Meldung, kein zweiter KI-Aufruf; nach dem Neuladen Hinweis „keine Antwort … nichts automatisch erneut gesendet“, Auftrag wieder im Eingabefeld |

**Eigener QA-Fehler, offen benannt:** Die headless-Edge-Läufe in Abschnitt 3 hingen am CDP-Port 9364 einer
liegengebliebenen headless-Edge-Instanz aus einer früheren eigenen QA-Sitzung (Profil `edge_profile_marks_vorher`),
nicht an einer frisch gestarteten. Als Chromium-Browserprüfung bleiben sie gültig. Die Instanz wurde gezielt beendet;
die WebView2-Läufe nutzen Port 9374 mit Engine-Prüfung.


### 3b. Gebaute Pilot-EXE, nativ im eigenen WebView2-Fenster (nicht installiert, `gebaute-exe/`)

**Rahmen:**

- **Build:** PyInstaller-Build aus Stand `d3d657f` mit Profil `kanzlei-pilot`, ohne Installer, ins Job-Verzeichnis.
  `Lexono.exe` SHA-256 `E70BDB0D69F869E5…328FC436`.
- **Start:** isoliertes Datenverzeichnis, synthetisches PDF, toter Cloud-Endpunkt, `LEXONO_BUILD_PROFILE=full` in der
  Umgebung (darf die Pilot-EXE nicht erweitern). Steuerung per CDP über `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS`.
- **Nicht installiert:** keine Installation, produktive Datenbank unberührt.

| Prüfung | Ergebnis |
|---|---|
| Natives WebView2-Fenster (Engine `Chrome/156 … Edg/156`, nicht headless) | ja |
| Erstversuch: Dialog bedienbar | alle 3 Optionen aktiv und (ins Bild gescrollt) per Klickpunkt erreichbar |
| Vor dem Bestätigen | nur `upload`, kein Auftrag |
| Ladeanzeige nach dem Bestätigen (Protokoll über das Neuladen hinweg) | „Pseudonymisierung bestätigt – Dokumente werden freigegeben und erneut geprüft …“ → „Schriftsatz wird erstellt …“ → „Ergebnis wird aufbereitet …“ |
| Ende ohne Schreibmodell | verständliche, gespeicherte Meldung „Die Cloud-KI ist nicht konfiguriert …“; keine hängende Anzeige |
| Auftrag | genau 1 Nutzernachricht |
| Lizenz-Tab | 3 Produkte (Verfügbar / In Entwicklung / In Entwicklung), 1× „Aktiv genutzt“, Modul nur `kanzlei`, kein Querscroll |
| Tracebacks im EXE-Log | 0 |

**Grenze:** In die EXE lässt sich kein Fake-Schreibmodell einsetzen. Der Erfolgspfad mit fertigem Schriftsatz ist
deshalb im Quellstand belegt (headless Edge und WebView2-Fenster, Abschnitte 3/3a). In der EXE ist es der
Fehlerpfad.

**Erster Messfehler, offen benannt:** Der erste EXE-Lauf meldete zwei Fehlschläge („nicht bedienbar“, „keine
Ladeanzeige“). Beides waren Messartefakte; die Messung ist korrigiert und wiederholt:

- Die dritte Option lag im scrollbaren Panel außerhalb des sichtbaren Bereichs. Die Treffer-Prüfung ohne Scrollen
  schlug deshalb fehl.
- Ohne API-Schlüssel lädt die Seite nach 0,8 s neu, und dabei ging das JS-Protokoll verloren.

## 4. Lizenzseite (Einstellungen → Tab „Lizenz“) – nach Referenzbild

Der Owner hat das Referenzbild am 29.10.2026 nachgereicht (`lizenz/00-referenzbild.png`). Die zuvor nach Textvorgabe
gebaute Seite ist danach angeglichen. Gegenüberstellung: `lizenz/01-vergleich-referenz-umsetzung.png`.

**Umgesetzt wie in der Referenz:**

- **Kopf:** „Lexono Produkte“ (Navy, linksbündig) mit dem Einleitungstext aus dem Bild.
- **Drei Karten** (je max. 400 px, zentriert, keine Grafiken), von oben nach unten:
  - Statusbadge (Lexono „Verfügbar“ grün, Finance und Finance & Tax „In Entwicklung“ grau);
  - zweizeiliger Name („Lexono“ über dem Zusatz) und Kurzbeschreibung;
  - Trennlinie, Preis 99 / 149 / 249 € mit „/ Monat pro Benutzer“ und „(inkl. MwSt.)“;
  - Statusleiste „Aktiv genutzt“ (grün, aktuelles Paket) bzw. „Bald verfügbar“ (grau).
  - Die aktuelle Karte hat einen grünen Rahmen.
- **„Ihre Vorteile im Überblick“:** Einleitungstext aus dem Bild und eine umrandete Tabelle.
  - Spaltenköpfe mit Name, „(in Entwicklung)“ und Preis.
  - Vier Funktionszeilen mit Unterzeilen, gefüllte grüne Haken, „–“ für nicht enthalten.
  - Zeilen: KI-Chat und Kanzlei-Workflows; Mandanten- und Aktenverwaltung; Digitale Belegverwaltung (ab Finance);
    Steuererklärungen & Steuer-Workflows (nur Finance & Tax).

**Bewusste Abweichungen bzw. Ergänzungen:**

- **Einstellungs-Kopf:** Zentrierter Kopf „Einstellungen“ und die Tabs bleiben wie bisher (Vorgabe: bestehende Kopfzeile
  und Tabs beibehalten). Die Referenz zeigt ihn linksbündig mit großem Zahnrad.
- **Statusleisten:** „Aktiv genutzt“ und „Bald verfügbar“ sind reine Statusanzeigen ohne Klickfunktion. Es gibt
  keinen Shop, und nichts wird freigeschaltet (keine Fake-Funktion).
- **Aktuelles Paket:** „Aktiv genutzt“ folgt nur den tatsächlich freigeschalteten Modulen (Lizenz ∩ Build-Profil).
- **Preiszeile:** Unter den Karten steht eine kleine Zeile, dass die Preise vorläufige Planung sind und es noch keinen
  Kauf über einen Shop gibt.
- **Lizenzprüfung:** Lizenzstatus und Lizenzimport bleiben unverändert unter dem Vergleich.
- **Unterzeilen = Zielumfang:** Die Unterzeilen nennen den Zielumfang der Stufen, z. B. „Bankimport, DATEV-Export“ und
  „ELSTER-/ERiC-Übermittlung“. Diese Funktionen sind heute nicht implementiert. Die betroffenen Spalten sind als
  „(in Entwicklung)“ gekennzeichnet.

**Visuelle Prüfung** (`lizenz/`, Pilotprofil, headless Edge):

- **Größen:** 1672×941 (Referenzgröße) und 1280×720 je hell und dunkel, dazu 1920×1080 und 960×600 hell.
- **Ergebnis:** In allen Größen kein Querscroll (weder Seite noch Container noch Tabelle), nichts abgeschnitten, keine
  JS-Fehler (`kanzlei-pilot-messwerte.json`).
- **Beim Abgleich gefunden und behoben:** Bei 960 px verbreiterte die Mindestbreite der Vergleichstabelle den Tab um
  18 px.
  - Das Panel ist jetzt begrenzt (`min-width: 0`), die Tabellen-Mindestbreite ist kleiner.
  - Unter 1000 px stehen die Karten untereinander.

**Tests:** `tests/test_license_page.py` (2) prüft:

- Produkte, Preise, Status, die vier Vergleichszeilen und „(in Entwicklung)“;
- keine Buttons, Links oder Formulare auf den Karten;
- „Aktiv genutzt“ nur gemäß Lizenz.

## 5. Tests

- **Gezielt:**
  - Upload-Erstversuch, Pseudonymisierungszentrum, Release-Fortsetzung, Web-Chat: 211 passed
  - Module/Build-Profil/Rechte/Settings: 171 passed (davon 1 Fehler durch das Wort „Online-Kauf“, behoben)
  - Lizenzseite: 2 passed
  - Nachtrag nach Hinweis/Tests: Upload-Erstversuch, Pseudonymisierungszentrum, Release-Fortsetzung, Web-Chat und
    Lizenzseite: 215 passed
- **Volle Regression:** alle 329 Testdateien, seriell im Vordergrund, in Blöcken zu 12 Dateien.
  - **Gesamt:** 7129 passed, 8 xfailed, 10 skipped, 5 failed. Die 5 Fehler sind ausschließlich bekannte bzw. nicht
    verursachte:
    - **4 bekannte Logo-Tests** in `tests/test_design_refresh.py`: Die Logo-Dateien wurden fremd gelöscht und werden
      gemäß Vorgabe nicht wiederhergestellt.
      - `test_full_logo_lockup_assets_have_a_real_alpha_channel`
      - `test_dark_logo_icon_is_byte_identical_to_the_light_logo_icon`
      - `test_dark_logo_has_identical_overall_geometry_to_light_logo`
      - `test_dark_logo_wordmark_is_recolored_to_white`
    - **1 lastabhängiger Test:** `test_s81_benchmark_hardening.py::test_crash_before_the_call_resumes_normally`
      scheiterte nur im Block unter Last (gestern schon so beobachtet). Die Datei allein läuft mit 56/56 grün;
      Datei und `scripts/legal_bench` sind unverändert. Der Test ist nicht behoben, aber nicht durch diese Änderung
      verursacht.
  - **Neu verursachte Fehler:** keine.
- **Finale Regression nach allen Nachträgen** (Stand `d3d657f`, 329 Dateien): **7132 passed**, 8 xfailed, 10 skipped,
  **4 failed = nur die bekannten Logo-Tests**. `test_s81` lief diesmal grün.

## 6. Offene Punkte und Risiken

1. **Keine Installation:** Geprüft sind der Quellstand (headless Edge und WebView2-Fenster) sowie die **gebaute**
   Pilot-EXE im eigenen WebView2-Fenster (Abschnitt 3b). Eine **Installation** auf dem gemeinsam genutzten System ist
   nicht erfolgt, weil sie laut Vorgabe eine Freigabe braucht. Offen sind damit nur Installer (Inno Setup),
   Installation und Update-Pfad.
2. **Referenzbild:** nachgereicht und umgesetzt (Abschnitt 4); die Abweichungen sind dort begründet.
3. **Timeout-Folge, geklärt:**
   - Ein Verbindungsabbruch beendet auch die Server-Verarbeitung; der Server gibt die Sperre frei, es entsteht kein
     Ergebnis.
   - Neu: Nach dem Neuladen erscheint der Hinweis „keine Antwort … nichts automatisch erneut gesendet“, und der
     Auftrag steht wieder im Eingabefeld.
   - Nachweise: Browser (headless Edge und WebView2) sowie
     `test_abandoned_stream_leaves_no_result_and_no_running_claim`. Ob der TestClient wirklich trennt, ist nicht
     sicher; der Test prüft die Endzustände.
4. **Ohne Browser-Speicher:** Ist kein `sessionStorage` verfügbar, liest der Dialog den Auftrag nur bei
   `text_not_sent=1` aus dem Eingabefeld. Sonst öffnet er wie bisher als Prüfansicht, jetzt mit Hinweis an der Frage.
5. **Namensmarkierung im Eingabefeld, geklärt:** Im Eingabefeld markierte Namen speichert schon der Upload für die
   Unterhaltung (`_record_manual_terms` im Upload-Pfad). Der aus dem Dialog gesendete Auftrag ist damit geschützt;
   `test_name_marked_in_the_composer_at_upload_also_protects_the_dialog_order` belegt, dass der Name nicht in der
   Cloud-Anfrage steht.
6. **Preise:** Die Anzeige ist Planung. Die Benutzerzahl wird in der Lizenz technisch nicht geprüft.
