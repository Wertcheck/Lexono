# Pseudonymisierungs-Erstversuch, Ladezustand und Lizenzseite (29.10.2026)

**Status:** Quellstand geprüft.

- **Prüfumfang:** Browser-QA mit echten Mausklicks (headless Edge per CDP) und automatisierte Tests.
- **Nicht geprüft:** Es gibt keinen Build und keine Installation. Eine native WebView2-Prüfung steht deshalb noch aus.
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

**Automatisiert:** `tests/test_upload_order_first_attempt.py` (10 Tests) prüft:

- Upload allein sendet nichts;
- Erstversuch pseudonymisiert genau einmal;
- erneutes Bestätigen erzeugt keinen zweiten Auftrag;
- ein vorhandener Auftrag hat Vorrang;
- ein veralteter Textstand oder eine offene Rückfrage entfernt den neuen Auftrag (Neuversuch genau einmal);
- in eine beantwortete Unterhaltung wird kein Auftrag nachgeschoben;
- der parallele Race-Fall;
- das `drafting`-Kennzeichen;
- die statische Verdrahtung.

## 4. Lizenzseite (Einstellungen → Tab „Lizenz“)

**Kein Referenzbild gefunden:** Ein Lizenz-Referenzbild lag weder im Repository noch unter Downloads, Desktop oder
Bilder. Umgesetzt ist deshalb die schriftliche Vorgabe; ein Abgleich mit dem Bild steht aus.

- **Rahmen:** Bestehende Kopfzeile, Original-Logo, Navigation und Einstellungs-Tabs bleiben, „Lizenz“ ist aktiv. Es
  gibt keinen neuen Menüpunkt.
- **Produktkarten:** drei kompakte Karten (je max. 236 px), zentriert, ohne Grafiken. „Lexono“ steht über dem Zusatz.
  - Lexono: 99 €, **Verfügbar**, mit „Ihr Paket“
  - Lexono Finance: 149 €, **In Entwicklung**
  - Lexono Finance & Tax: 249 €, **In Entwicklung**
  - Alle Preise gelten pro Benutzer und Monat, inkl. MwSt.
- **Vergleich „Ihre Vorteile im Überblick“:**
  - Kanzlei-Funktionen in allen drei Produkten;
  - Finanzbuchhaltung und Belegverwaltung ab Finance;
  - Steuern nur in Finance & Tax;
  - keine Legende.
- **Keine Wirkung auf die Freischaltung:** Die Karten haben keinen Kauf- oder Freischalt-Button. „Ihr Paket“ folgt nur
  den tatsächlich freigeschalteten Modulen (Lizenz ∩ Build-Profil). Lizenzstatus und Import bleiben unverändert
  darunter.
- **Preismodell:** `app/modules/plans.py` mit Produktstufen ersetzt das Addierungsmodell vom 28.10.;
  MODULARCHITEKTUR §9 ist aktualisiert.
- **Visuelle Prüfung** (`lizenz/`, Pilotprofil): 1920×1080, 1280×720 und 960×600 hell, 1280×720 dunkel. Ergebnis:
  kein Seiten- oder Tabellen-Querscroll, nichts abgeschnitten, keine JS-Fehler (`kanzlei-pilot-messwerte.json`).
- **Tests:** `tests/test_license_page.py` (2) prüft Produkte, Preise, Status, Vergleich, fehlende Kauf-Buttons und
  „Ihr Paket“ gemäß Lizenz.

## 5. Tests

- **Gezielt:**
  - Upload-Erstversuch, Pseudonymisierungszentrum, Release-Fortsetzung, Web-Chat: 211 passed
  - Module/Build-Profil/Rechte/Settings: 171 passed (davon 1 Fehler durch das Wort „Online-Kauf“, behoben)
  - Lizenzseite: 2 passed
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

## 6. Offene Punkte und Risiken

1. **Kein Build, keine Installation:** Die WebView2-Desktopprüfung steht aus. Die Ursache war Anwendungslogik, nicht
   browserspezifisch; trotzdem ist das nicht nativ nachgewiesen.
2. **Kein Referenzbild:** Die Lizenzseite ist nicht mit einem Referenzbild abgeglichen (Bild nicht auffindbar).
3. **Timeout-Folge:** Bricht die Anzeige nach 5 Minuten ohne Server-Ereignis ab, beendet der Verbindungsabbruch auch die
   Server-Verarbeitung; es entsteht kein Entwurf. Nach dem Neuladen steht die Nutzernachricht ohne Antwort da. Ob
   dann der bestehende Hinweis „Verarbeitung unterbrochen“ erscheint, ist nicht abschließend geprüft (die Abfrage
   wurde in dieser Sitzung nicht ausgeführt). Ein erneutes Senden ist bewusst manuell.
4. **Ohne Browser-Speicher:** Ist kein `sessionStorage` verfügbar, liest der Dialog den Auftrag nur bei
   `text_not_sent=1` aus dem Eingabefeld. Sonst öffnet er wie bisher als Prüfansicht, jetzt mit Hinweis an der Frage.
5. **Namensmarkierung im Eingabefeld:** Im Eingabefeld markierte Namen (`protected_terms`) gehen beim Senden aus dem
   Dialog nicht mit. Die zentrale Pseudonymisierung und die Rückfrage zum Auftragstext laufen unverändert serverseitig.
6. **Preise:** Die Anzeige ist Planung. Die Benutzerzahl wird in der Lizenz technisch nicht geprüft.
