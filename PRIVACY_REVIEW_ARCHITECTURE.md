# Datenschutz-Rückfrage (Human in the Loop), Pre-Cloud-Gate und datensparsames Lernen

Stand: 12.10.2026, Ausgangscommit `5680602`. Ergänzt ARCHITECTURE.md (Privacy-Gateway, §63/§70) – ersetzt nichts.

## 1. Root-Cause-Analyse (Ist-Zustand)

**Datenfluss Chat** (synchron im HTTP-Request, Fortschritt per SSE `start/status/delta/done`):
Upload → Textextraktion/OCR (`ChatService.attach_document`, vor dem Stream) → Nutzernachricht gespeichert →
`ChatService.send_message_stream` → `DraftingService._prepare_and_gate` → lokale Kontextaufbereitung
(`RuleBasedLocalAIProvider`: Dokumentauszüge, `known_entities` aus Mandant/Beteiligten) →
**`ClaudePrivacyGateway.prepare_request`** (Pseudonymisierung: Regex-Detektoren, bekannte Entitäten, Titel-Namen,
Presidio/spaCy-NER + Umlaut-Wiederherstellung, PROPN-Zweitsignal, Modell-Probing; Filter; Restrisiko-Scan;
Final Payload Gate) → optional lokale KI (Ollama) → Cloud-Aufruf (`write`/`write_stream`) → Antwort-Leak-Check →
lokale Rekonstruktion → Persistenz.

**Cloud-Einstiegspunkte mit Inhalt** (vollständig erfasst): `AnthropicWritingProvider.write/write_stream`,
`AnthropicReviewProvider.review` (Dev-Direktweg), `GatewayRelayClient` (Produktion, über Writing- und Review-Provider).
Aufrufer: `DraftingService` (Chat, Schriftsatz-Generator, Entwürfe, anwaltliche Anweisungen, Editor-KI),
`ReviewEngine.review_draft`, `DraftGenerationOrchestrator` (exportiert, ungenutzt). Alle rufen vorher `prepare_request`.

**Befunde**
1. *Restlecks (synthetisch, 1100 + 440 Fälle):* 3,8 % mit Akte, 6,8 % ohne Akte. Ursachenklassen: (a) Gattungswort als
   Nachname ohne unabhängiges Signal (Richter, Fuerst – ca. 75 % der Restlecks mit Akte); (b) seltene Namen, vom Tagger
   als Substantiv/Adjektiv/Adverb gelesen, in Tabellen/Überschriften/nach Umbruch. Beide Klassen sind mit weiteren
   automatischen Regeln nicht fehlalarmarm lösbar (gemessen: „bares Substantiv als Subjekt" trifft 141/153 Fachbegriffe).
   **Grundsätzliche Verbesserung:** statt weiterer Sonderregeln eine dritte Entscheidungsstufe „unsicher" einführen,
   die der Mensch klärt – mit fail-closed Default (maskieren), wo keine Rückfrage möglich ist.
2. *Gate nicht strukturell erzwungen:* `ClaudeRequestPayload` ist ein gewöhnliches Pydantic-Modell; Provider prüfen nur
   per Typ-Hinweis. Ein Codepfad könnte ungeprüften Text in eine Payload füllen. Zudem wird die Payload nach dem Gate
   verändert (Anhängen der lokalen KI-Zusammenfassung per `model_copy`, nur mit Platzhalter-Prüfung).
3. *Keine Persistenz von Datenschutz-Entscheidungen*: Korrekturen des Anwalts gehen verloren; `known_entities` entstehen
   nur aus Mandant/Beteiligten.

## 2. Maßnahmenplan (umgesetzt in dieser Reihenfolge)

| # | Maßnahme | Sicherheitswirkung |
|---|---|---|
| M1 | **Unsicherheits-Kandidaten** (`app/privacy/review.py`): nach der Pseudonymisierung im verbleibenden, vom Anwalt/Dokument stammenden Text: großgeschriebenes, nicht erkanntes Wort ohne Artikel, in Namensrolle (Subjekt/vor finitem Verb, „von/durch X", Feldwert „Label: X", Tabellenzelle, nach Rollenwort wie „Zeuge"), Modell-Probing ≥ 1 von 5 | erfasst ~60 % der Restlecks; 4 Rückfragen in 18 synthetischen Dokumenten (max. 1 je Dokument), 0/17 Anweisungen |
| M2 | **Gateway `review_mode`**: `"mask"` (Standard für alle nicht-interaktiven Pfade): unsichere Kandidaten werden vorsichtshalber pseudonymisiert; `"ask"` (interaktiver Chat): **kein Cloud-Aufruf**, `GatewayResult.review_required` mit Kandidaten | fail-closed in beiden Modi; kein Pfad sendet ungeklärte Stellen |
| M3 | **Entscheidungen** `PrivacyDecisions(personal, not_personal)`: „personenbezogen"/„unsicher" → als bekannte Entität pseudonymisiert; „kein personenbezogenes Datum" hebt **nur** die Rückfrage für genau diesen Begriff auf – nie eine automatische Erkennung, nie den Restrisiko-Scan, Leak-Check oder Final Payload Gate | manuelle Entscheidung kann keine technische Sperre umgehen |
| M4 | **Versiegelte Payload**: nur der Gateway erzeugt gültig versiegelte `ClaudeRequestPayload`s (Siegel über den Inhalt); alle Cloud-Provider verweigern unversiegelte/veränderte Payloads; Anhängen der lokalen Zusammenfassung über eine Gateway-Methode mit erneuter Prüfung und Neuversiegelung | Gate serverseitig erzwungen, Prüfung gilt für exakt den ausgehenden Inhalt |
| M5 | **Persistenz** `privacy_decisions` (Akte, optional Unterhaltung, Begriff, Entscheidung, Nutzer): „personenbezogen"/„unsicher" gelten aktenweit (sichere Richtung), „kein personenbezogenes Datum" nur in der Unterhaltung; Widerspruch → „personenbezogen" gewinnt. Löschung mit Unterhaltung/Akte | Akten-/Mandantentrennung, Datensparsamkeit |
| M6 | **Globales Lernen** `privacy_feedback_stats`: nur HMAC des Begriffs (lokaler Schlüssel), Entscheidung, Zähler, Merkmale (Wortart-Rolle, Probing-Score) – kein Klartext, kein Kontext, kein Dokument; Auswertung per Skript → synthetische Tests; **keine** automatische Regeländerung, kein Modelltraining | keine zentrale Sammlung von Namen |
| M7 | **UI**: kompakte Rückfrage-Karte im Chat (Begriff, minimaler Kontext, drei Optionen, Schrittanzeige); nach Antwort serverseitige Neuprüfung des **gesamten** Inhalts und Fortsetzung | Freigabe nur nach erneutem vollständigen Gate-Durchlauf |

Nicht umgesetzt (bewusst): Modelltraining, mandantenübergreifende Regeln, automatisches Freischalten von Begriffen,
nebenläufige lokale Verarbeitung während der Rückfrage (die lokale Pipeline vor dem Gate ist zu dem Zeitpunkt bereits
abgeschlossen; die lokale KI-Vorabanalyse läuft erst nach der Freigabe auf dem pseudonymisierten Text).

## 3. Messwerte (identische synthetische Fälle, tatsächliche Payloads)

| Messung | vorher (`5680602`, Modus `off`) | nachher: nicht-interaktiv (`mask`) | nachher: Chat (`ask`) |
|---|---|---|---|
| Klartext-Namen mit Akte (1100 Fälle) | 3,8 % | **2,2 %** | 2,2 % + 18 Rückfragen (1,6 %) statt Versand |
| Klartext-Namen ohne Akte (440 Fälle) | 6,8 % | **1,6 %** | – |
| Rechtswörter fälschlich maskiert/blockiert (132) | 1 („Partei“) | 1 | 1 |
| Anweisungen/Chat-Fragen verändert (17) | 1 | 1 | 1 |
| Fachbegriffe betroffen, Namensrollen-Schablonen ohne Akte (100) | 19 | 24 | 22 von 153 lösen eine Rückfrage aus (künstliche Worst-Case-Kontexte) |
| Rückfragen in 18 synthetischen Dokumenten | – | – | **3** (je höchstens 1: „Bedarf“, „Maengelanspruesche“, „Regen“) |
| Laufzeit Gateway je Dokument (kalt) | 0,95 s | – | 1,05 s (+0,1 s) |
| Blocks mit Akte | 0,1 % | 0,1 % | 1,7 % (davon 1,6 % Rückfragen) |

**Sicherheitsprüfungen (Tests `tests/test_privacy_review.py`, `tests/test_web_privacy_review.py`):** keine Payload und kein
Provider-Aufruf bei offener Rückfrage; Fehler der Kandidatensuche blockiert; „kein personenbezogenes Datum“ hebt keine
automatische Erkennung auf; unversiegelte oder nach dem Gate veränderte Payloads werden von allen vier Cloud-Providern
abgelehnt; Rückfrage nur für die offene letzte Nachricht der eigenen Unterhaltung; Aktenisolation, Widerspruch →
„personenbezogen“, Löschung mit der Unterhaltung; Statistik und Audit ohne Klartext-Begriff.

**Installierter Build** (aus `f612478`, sauberer Baum; der QA-Datenbank wurde beim Start die Migration `schritt3_028`
angewandt – Nachweis, dass der Build den neuen Stand enthält): Rückfrage-Karte erscheint vor jedem Cloud-Aufruf (0 Payloads
bis zur Auswahl); „Als Person behandeln“ → `[PERSON_01]` im Payload, Entscheidung aktenweit gespeichert, Statistik nur als HMAC;
„Kein personenbezogenes Datum“ → Begriff lesbar, übrige Namen weiter maskiert, Betrag „12.400,00 EUR“ erhalten;
„Unsicher“ → pseudonymisiert. Hell- und Dunkelmodus geprüft.

## 4. Offene Punkte / NICHT VERIFIZIERT

* Vollständige Leckfreiheit ist nicht belegt (synthetisch 2,2 % mit Akte, 1,6 % ohne Akte in nicht-interaktiven Pfaden).
* Fundstelle im Dokument-Vorschaufenster (rechte Spalte des Referenzbilds) wird nicht hervorgehoben – die Karte zeigt den Kontextausschnitt.
* Lädt der Anwalt die Seite während einer offenen Rückfrage neu, verschwindet die Karte; die Nachricht bleibt unbeantwortet und muss erneut gesendet werden (es wurde nichts an die Cloud gesendet).
* Nicht-interaktive Pfade (Schriftsatz-Generator, Entwurfsanweisungen, Review) maskieren unsichere Begriffe vorsichtshalber und listen sie als lokalen Prüfpunkt – dort sind Fachwort-Fehlalarme möglich (Worst-Case-Schablonen: 5 zusätzliche von 100).
* Kandidaten- und Probing-Schwellen sind an synthetischen Namen kalibriert und hängen von `de_core_news_lg` ab.
* Globale Auswertung (`scripts/privacy_feedback_report.py`) ist rein beratend; eine automatische Regelaktivierung ist bewusst nicht vorgesehen.

## 5. Härtung (12.10.2026, `1bd3ce3`–`fb3e9f5`)

| Offener Punkt | Befund / Ursache (belegt) | Maßnahme | Status |
|---|---|---|---|
| Qualitätsprüfung lehnte Antworten nach „Kein personenbezogenes Datum“/„Unsicher“ ab | `scripts/diagnose_review_quality_check.py`, identische Payload, echtes Ollama: fester Fang-Server-Text **0/9** (alle drei Entscheidungen, auch „Als Person“), plausible Antwort **9/9**. Ursache ist der QA-Fang-Server, der die Anfrage nicht beantwortet – nicht die Entscheidung, nicht die Qualitätsprüfung | Qualitätsprüfung unverändert; QA-Fang-Server um sachbezogenen Antwortmodus ergänzt | PASS (im Build mit sachbezogener Antwort bestanden, alle drei Entscheidungen) |
| Rückfrage verschwand beim Neuladen | Nichts war gespeichert; bei einer neuen Unterhaltung stand die Adresse zudem noch auf `/dashboard/chat` | `GET /dashboard/chat/{id}/pending-review`: rein lokale Neuermittlung über denselben Prüfpfad (kein Cloud-Aufruf, keine gespeicherten Kandidaten); Adresse wird beim Rückfrage-Ereignis auf die Unterhaltung gesetzt; In-Flight-Sperre je Nachricht verhindert Doppelversand (zweiter Tab, Doppelklick, Neuladen während der Verarbeitung) | PASS |
| Fundstelle nicht hervorgehoben | Dokumentansicht markierte nur automatische Treffer | Link „Fundstelle im Dokument zeigen“ nur bei stabiler Zuordnung (Begriff als ganzes Wort im extrahierten Text eines Dokuments dieser Unterhaltung); Markierung `pii-highlight--review`; sonst bleibt der Kontextausschnitt | PASS |
| Restliche Klartext-Namen | Feldgrenze fehlte im Kandidaten-Scan („Akte: Beispielakte“ verschmolz mit „Richter hat angerufen“); formgleiche Namen als Plural getaggt trotz Singular-Verb | Kandidaten-Scan auf dem Text mit Feldmarkern; Plural-Tag gilt bei Subjekt eines Singular-Verbs als widerlegt | Lecks mit Akte 2,2 % → **1,8 %**, ohne Akte 1,6 % → **1,4 %**; Fehlalarme unverändert (1/132, 1/17, 24/100) |
| Rückfrage-Pause als „unknown_block_reason“ protokolliert | Kategorisierung kannte den Grund nicht | Kategorien `privacy_review_required`, `privacy_review_check_failed` | PASS |

Installierter Build aus `fb3e9f5` (sauberer Baum): Rückfrage → Neuladen → wiederhergestellt; Fundstellen-Link → Dokument mit
markiertem Begriff und wiederhergestellter Rückfrage; „Als Person“ und „Unsicher“ → Begriff pseudonymisiert; „Kein personenbezogenes
Datum“ → Begriff lesbar, übrige Namen maskiert, Betrag erhalten; erneutes Absenden → „Diese Rückfrage ist nicht mehr offen“, keine
zweite Cloud-Anfrage. Vollständige Suite: 3322 bestanden, 1 übersprungen, 2 erwartete Fehlschläge, 1 unerwartet bestanden.

Weiter offen (NOT VERIFIED behoben): Namen nach „durch“ + Zeilenumbruch, die das Modell als Plural liest (Stuckenschneider, Lange),
Namen in Überschriften („Zeugenaussage Richter“) und in Aufzählungen nach einem bereits ersetzten Namen; die In-Flight-Sperre
gilt pro Prozess (Lexono läuft als ein lokaler Prozess).

## 6. Restlecks und prozessübergreifende Doppelversand-Sperre (12.10.2026, `9d9ab9c`)

**Restklassen (reproduziert im Benchmark, tatsächliche Payloads):** Agens nach „durch/von“ + Zeilenumbruch, den das Modell
als Plural liest (Stuckenschneider); Apposition vor einem Rollenwort („Grotelueschen, Zeuge“); kurze Überschriftszeile
(„Zeugenaussage Fischer“); Aufzählung neben einem bereits ersetzten Personen-Platzhalter („[PERSON_01], Fuerst sowie …“).
Neue Kandidatenrollen in `app/privacy/review.py`; weiterhin nur mit Modell-Probing ≥ 1, formgleiche Plural-Lesart nur nach
„von/durch“ zugelassen („bei Mängeln“ bleibt ausgeschlossen – Gegenprobe zeigte sonst einen neuen Fehlalarm).

| identische Fälle | vorher (`6ddea15`) | nachher |
|---|---|---|
| Klartext-Namen mit Akte, Modus `mask` (1100) | 1,8 % | **0,4 %** |
| Klartext-Namen ohne Akte (440) | 1,4 % | **0,7 %** |
| Rückfragen im Chat (1100 Namensfälle, `ask`) | 22 | 39 (Namen, die vorher durchgingen) |
| Rechtswörter fälschlich maskiert (132) / Anweisungen verändert (17) | 1 / 1 | 1 / 1 |
| Fachbegriffe betroffen ohne Akte (100) / Fachfragen verändert (14) | 24 / 1 | 24 / 1 |
| Fachbegriffe betroffen mit Akte, künstliche Namensrollen-Sätze (600) | 99 | 100 („Rauchmelder“ +1) |
| Rückfragen in 19 synthetischen Dokumenten | 4 | 4 |

Der 600er-Wert zeigt einen bereits seit `4d201de` bestehenden Zielkonflikt des Masken-Modus (vor der Rückfrage-Funktion: 34/600)
in bewusst künstlichen Worst-Case-Sätzen; in den 19 Dokumenten und den Rechtswort-/Anweisungskorpora ist er nicht sichtbar.
Bekannte Grenzen (Tests als `xfail` dokumentiert): „Lange“ nach „durch“ (vom Modell als Adjektiv mit Lemma „lang“ gelesen),
„Zeugenaussage Richter“.

**Doppelversand:** `Lexono.exe` verhindert einen Zweitstart nur innerhalb einer Windows-Sitzung (Mutex ohne `Global\`); zweite
Benutzerkonten oder ein Entwicklungsserver können dieselbe Datenbank nutzen. Die frühere Sperre im Arbeitsspeicher ist ersetzt durch
`chat_message_claims` (Migration `schritt3_029`): Nachrichten-ID als Primärschlüssel, eigene kurze Sitzung, atomar über die Datenbank.
Freigabe nach Abschluss (Antwort gespeichert oder Rückfrage ohne Cloud-Aufruf). Liegengebliebene Sperre (> 15 min) = „unterbrochen“:
keine Rückfrage, keine automatische Wiederaufnahme, Hinweis an den Anwalt (fail-closed). Belegt durch Tests mit 4 echten Prozessen und
6 Threads auf einer Datei-Datenbank (genau ein Halter) und im installierten Build: zwei gleichzeitige Freigaben → genau eine
Verarbeitung und genau eine Cloud-Anfrage, die zweite „Diese Anfrage wird bereits verarbeitet“.

## 7. Fachwort-Fehlalarme, frühere Grenzfälle, zwei Windows-Konten (12.10.2026, `cb6858f`, `ad9ce81`)

**Fachwort-Fehlalarme.** Zerlegung des 600er-Korpus (mit Akte, Modus `mask`): 34 Fälle automatische Erkennung (unverändert seit
vor der Rückfrage-Funktion), 66 Fälle Rückfrage-Kandidaten – immer dieselben ~16 Fachwörter mit Probing-Score 1–3, in allen
Namensrollen. Gegenüberstellung mit den synthetischen Namen: Probing-Muster und Wortvektoren überschneiden sich (z. B. „Richter“
und „Rauchmelder“ beide nur im Rahmen „Herr X“). Übernommen: der Rahmen „Herr X hat unterschrieben“ ist für Kandidaten Pflicht
(jeder Testname erfüllt ihn, „Bescheid“/„Flachdach“/„Zugluft“ nicht) → 100 → 87/600, Klartext-Namen unverändert. Getestet, **nicht**
übernommen: Längengrenze 18 Zeichen (72/600 ohne Leck-Änderung, aber nur an 55 Namen belegt; seltene lange Nachnamen blieben ungeschützt).

**Frühere Grenzfälle.** „Lange“ nach „durch“: Lemma „lange“ (klein, als Adverb gelesen) scheiterte am großschreibungsempfindlichen
Formgleichheits-Abgleich. „Zeugenaussage Richter“: die Analysefassung glättete den Zeilenumbruch als weichen PDF-Umbruch.
**Befund im installierten Build (FAIL vor `ad9ce81`):** im echten Dokumentpfad kamen beide trotzdem im Klartext an – die Dokumentaufbereitung
(`local_ai_provider._normalize_document_text`) ersetzt Zeilenumbrüche durch zwei Leerzeichen, und „Lange geprueft“ (transliteriert)
wurde als Adjektiv geparst; die Unit-Tests hatten `\n` verwendet. Korrekturen: Zeilengrenze = Umbruch oder ≥ 2 Leerzeichen; Agens an der
Oberfläche („von/durch“ + Wort + partizipartiges Folgewort) bei Fehlparse; Leerraum-Token übersprungen; Tabellenzeichen „|“ (vom Tagger
als ADJ etikettiert) zählt nicht als Artikel/Adjektiv; Tabellenzelle nur, wenn dieselbe Zeile ein Rollenwort, „Name“ oder einen
Personen-Platzhalter enthält (Rechnungstabellen fragen nicht). Neuer Benchmark im echten Dokumentpfad (Sachverhalt durch
`_normalize_document_text`, 990 Namensfälle, 800 Fachwortfälle).

| identische Fälle | vorher (`81e78c6`) | nachher (`ad9ce81`) |
|---|---|---|
| Klartext-Namen mit Akte (1100, `mask`) | 0,4 % | **0,1 %** (1 Zweitname in einer Aufzählung) |
| Klartext-Namen ohne Akte (440) | 0,7 % | **0,0 %** |
| Klartext-Namen im Dokumentpfad (990) | 0,2 % (an `cb6858f` gemessen) | **0,0 %** |
| Rückfragen im Chat (1100 Namensfälle) | 39 | 42 |
| Fachwort-Fälle mit Akte (600) | 100 | **87** |
| Fachwort-Fälle im Dokumentpfad (800) | 105 (an `cb6858f`) | 105 |
| Fachbegriffe ohne Akte (100) / Fachfragen (14) | 24 / 1 | 23 / 1 |
| Rechtswörter (132) / Anweisungen (17) | 1 / 1 | 1 / 1 |
| Rückfragen in synthetischen Dokumenten | 4 (19 Dok.) | 2 (16 Fachdokumente, ohne die Namens-Testdokumente) |

Installierter Build aus `ad9ce81`: Grenzfall-PDF → Rückfrage „Richter“, „Lange“; nach „Als Person“ beide maskiert; „Bescheid“,
„Streitwert“, „Zugluft“, „Flachdach“, „Baumangel“ lesbar; Betrag erhalten; erneutes Absenden abgewiesen. Bekannte, vorbestehende
Grenze (xfail): transliteriertes Fachwort als Agens („durch Ueberweisung“) löst eine Rückfrage aus (kein Leck).

**Zwei Windows-Konten.** Geprüft: Datenbank `C:\ProgramData\Lexono\data\kanzlei_ai.db` (aus `C:\ProgramData\Lexono\.env`);
Programm pro Benutzer unter `%LOCALAPPDATA%\Lexono` (`PrivilegesRequired=lowest`); Einzelinstanz-Mutex nur je Sitzung. ACL der
Datenbankdatei: Eigentümer-Konto Vollzugriff, `VORDEFINIERT\Benutzer` nur Lesen/Ausführen. Ein zweites Konto kann also standardmäßig
nicht schreiben – weder Nachrichten speichern noch die Sperre anlegen; Test `test_claim_fails_closed_on_a_read_only_database` belegt,
dass die Sperre dann mit Fehler scheitert und nie als „erhalten“ gilt. Der echte Zwei-Konten-Lauf ist **NOT VERIFIED**: auf dem
Testrechner existiert nur ein aktives Konto, die Sitzung hat keine Administratorrechte; ein zweites Konto anzulegen ist eine
Systemänderung ohne Freigabe. Voraussetzungen für den Test: zweites lokales Konto, Schreibrecht beider Konten auf
`C:\ProgramData\Lexono\data` (nur falls der Mehrkonten-Betrieb gewollt ist – derzeit nicht vorgesehen), gleichzeitiges Senden
derselben Rückfrage aus beiden Sitzungen gegen den Fang-Server.

## 8. Testrealität: realistische Dokumente durch den echten Pfad (12.10.2026, `368f504`)

**Architektur-Audit (unabhängige Prüfung, Code-Belege):** Eine Entscheidung „kein personenbezogenes Datum“ wirkt ausschließlich in
`find_review_candidates`; automatische Erkennung (Regex, bekannte Entitäten, NER, Probing), Restrisiko-Scan, Final Payload Gate und
Siegel laufen nach jeder Entscheidung unverändert; „personenbezogen/unsicher“ fügen nur Treffer hinzu; Entscheidungen sind auf
Akte/Unterhaltung begrenzt; der Client kann nichts als „freigegeben“ markieren; alle vier Cloud-Provider prüfen das Siegel.
Behoben aus dem Audit: Entscheidungsbegriffe nur als einzelnes alphabetisches Wort (ein eingeschleuster Mehrwort-Begriff hätte in der
Überlappungsauflösung einen längeren Treffer anderer Kategorie verdrängen können); Masken-Modus ohne Obergrenze (bisher wurden nur die
ersten 8 unsicheren Begriffe maskiert).

**Realistische Dokumente** (`scripts/make_realistic_privacy_docs.py`, Goldstandard; `scripts/bench_realistic_documents.py`, echter Pfad
Extraktion → Dokument in Akte → DraftingService → versiegelter Payload): derselbe Inhalt als DOCX (Kopf-/Fußzeile, Liste, Beteiligten-
und Rechnungstabelle), PDF (echte Umbrüche, Tabellen als positionierter Text) und Scan (Bild-PDF, OCR mit gebündeltem Tesseract).

| Befund (vorher, `119f9af`) | Ursache (belegt) | Klasse | Maßnahme |
|---|---|---|---|
| DOCX: Tabellen, Kopf-/Fußzeile fehlten im Text (u. a. Pfannenstiel, Anschriften, IBAN, Rechnungsbeträge) | `_extract_docx_text` las nur `paragraphs` | Extraktion (Inhaltsverlust, kein Leck) | Absätze und Tabellen in Dokumentreihenfolge, Zeilen als „Zelle \| Zelle“, Kopf-/Fußzeilen |
| PDF und Scan: **Leck „Fuerst“** (Listenpunkt „- Fuerst“) | Aufzählungszeichen zählten nicht als Verbindung zum ersetzten Personen-Platzhalter; DOCX-Liste ohne Zeichen ebenso | Erkennung | Listenpunkte und Listenzeilen neben Personen-Platzhalter |
| PDF: „Estrich“ maskiert, Rückfragen zu „Menge“, „Estrich“ | flach gelegte Tabelle; Parser machte Zellen zum Subjekt eines Verbs aus dem nächsten Absatz | Formatierung | Subjekt-Rolle nur innerhalb einer Zeile (Präposition + Umbruch bleibt erlaubt – Scan-Gegenprobe) |
| DOCX nach Tabellen-Extraktion: Rückfragen „Menge“, „Estrich“ | Beteiligten- und Rechnungstabelle verschmolzen nach der Aufbereitung zu einer „Zeile“ | Formatierung | Tabellenzeilen-Grenze = Umbruch oder ≥ 2 Leerzeichen, die nicht an „\|“ stehen |

| Format (Goldstandard: 14 personenbezogene Werte, 16 Fachbegriffe/Beträge) | Lecks | fehlende Fachbegriffe/Beträge | Rückfragen |
|---|---|---|---|
| DOCX vorher / nachher | 0 (Tabellen fehlten) / **0** | 0 / **0** | Richter, Lange, Fuerst / **Richter, Lange, Fuerst** |
| PDF vorher / nachher | **1 (Fuerst)** / **0** | **1 (Estrich)** / **0** | Richter, Menge, Estrich / **Richter, Fuerst** |
| Scan vorher / nachher | **1 (Fuerst)** / **0** | 0 / **0** | Richter, Lange / **Richter, Lange, Fuerst** |

Alle übrigen Benchmarks auf identischer Basis unverändert (mit Akte 0,1 %, ohne Akte 0,0 %, Dokumentpfad 0/990; Fachwort-Fälle 87/600 bzw.
105/800; Rechtswörter 1/132; Anweisungen 1/17). Bündelung: Rückfragen erscheinen je Runde gemeinsam in einer Karte (höchstens 8, eine
Absendung, eine erneute Gesamtprüfung); wiederholte Rückfragen entfallen innerhalb der Unterhaltung („personenbezogen“ aktenweit,
„kein personenbezogenes Datum“ in der Unterhaltung) – per Test belegt.

**Installierter Build aus `368f504`:** Upload von DOCX, PDF und Scan im echten Chat, abgefangene Cloud-Payloads: jeweils Rückfragen nur zu
echten Namen; nach „Als Person“ **0 Goldwerte im Klartext**, alle Fachbegriffe und Beträge vorhanden. **Befund Konfiguration:** in der
installierten `.env` ist `OCR_ENABLED=false` – ein Scan bleibt dann „pending“, die Nachricht geht OHNE Dokumentinhalt an die Cloud (kein
Leck, aber Inhalt fehlt, ohne dass die Antwort darauf hinweist). Der Scan-Pfad wurde deshalb mit `OCR_ENABLED=true` nur für die
QA-Instanz (Prozessvariable, Konfiguration unverändert) geprüft.

## 9. Dokumentstatus und Chat-Verarbeitung (13.10.2026)

**Root Cause (belegt, Abschnitt 8 „Befund Konfiguration“).** Kette Upload → Status → Routing → Gateway → Payload:
`ChatService.attach_document` verarbeitet synchron. Ein Scan wird bei `OCR_ENABLED=false` zu `ocr_status="pending"`
ohne `extracted_text`. `record_user_message` verknüpft ihn trotzdem mit der Nachricht. `send_message(_stream)` prüft
den Dokumentstatus nirgends. `RuleBasedLocalAIProvider._build_sachverhalt` filtert auf `extracted_text IS NOT NULL`
und lässt den Scan **stillschweigend weg**. Gateway und Cloud-Aufruf laufen normal mit dem Text „Bitte das angehängte
Dokument verarbeiten.“ und ohne Dokumentinhalt, und die Antwort erweckt den Eindruck einer Auswertung. Dieselbe Lücke
besteht in vier weiteren Fällen:
- defektes PDF (`failed`);
- leere DOCX bzw. leerer OCR-Text (`done`/`not_needed` ohne Text; zählte sogar als „Dokumentkontext“);
- die Dokumentaktion „Dokument analysieren“ (Prompt mit Dateinamen, Dokument nicht verknüpft);
- **Teil-Scans** (PDF mit Textseiten und gescannten Seiten galt mit dem Text der Textseiten als vollständig).

Der bestehende Test `test_document_attachment_flows_through_ocr_pseudonymization_to_chat` schrieb genau dieses
Verhalten fest: ein Pseudo-PDF ohne Text, die Cloud-Anfrage wurde erwartet. Er nutzt jetzt ein echtes Text-PDF.

**Statusmodell** (`app/documents/readiness.py`). Es ist kein neues Datenmodell, sondern wird aus `ocr_status`,
`extracted_text` und dem Verarbeitungs-Audit abgeleitet:

| Zustand | Bedingung |
|---|---|
| `ready` | `not_needed`/`done` mit nicht-leerem Text |
| `processing` | Spaltenstandard ohne Text und ohne Abschluss-Audit; unbekannter Status (fail-closed) |
| `no_text` | verarbeitet, aber kein verwertbarer Text |
| `ocr_disabled` | `pending`, d. h. OCR nötig, aber bei der Verarbeitung deaktiviert; der Hinweis unterscheidet, ob OCR inzwischen aktiv ist |
| `failed` | Extraktion oder OCR fehlgeschlagen; technische Fehlerangabe aus dem Audit, per Konstruktion ohne Pfad/Inhalt |
| `unsupported` | Dateiformat nicht verarbeitbar |
| `deleted` | Dokument inzwischen gelöscht |
| `other_matter` | Aktenisolation nach Umhängen der Unterhaltung |
| `not_included` | lesbar, aber nicht im lokal gebauten Kontext |

**Durchsetzung (serverseitig, zweistufig).**
1. *Vorprüfung* in `ChatService.send_message`, `send_message_stream` und `pending_privacy_review`, nach den rein lokalen
   Antworten (Normzitat, „aktuellste Akte“) und vor jedem Drafting.
   - Bezugsdokumente: die Anhänge der aktuellen Nachricht.
   - Ohne Anhang, aber mit Dokumentbezug im Text („Dokument“, „Anhang“, „Datei“, „PDF“, „Scan“ …): die Anhänge der
     jüngsten früheren Nachricht mit Anhang.
   - Die Dokumentaktion verknüpft das Dokument jetzt mit der Nachricht.
   - Der Status wird frisch aus der Datenbank gelesen (`populate_existing`).
2. *Nachprüfung* in `DraftingService._prepare_and_gate` (`required_document_ids`), **nach** dem Lesen des Kontexts und
   **vor** dem Gateway: Jedes Bezugsdokument muss `ready` sein **und** laut `included_document_ids` tatsächlich in den
   Sachverhalt eingeflossen sein. Eine Statusänderung während der Anfrage (Neuverarbeitung, Löschen) führt so nie zu
   einer Antwort, die eine Auswertung vortäuscht. Wird ein Dokument vor dem Kontextaufbau fertig, wird es normal
   verwendet (Test).

Ist ein Bezugsdokument nicht `ready`, gilt:
- kein Gateway- und kein Cloud-Aufruf;
- lokale Antwort (`blocked=True`, nicht im Verlauf künftiger Cloud-Anfragen) mit dem Zustand je Dokument, den bereits
  lesbaren Dokumenten (keine Teilauswertung als Vollauswertung) und dem weiteren Vorgehen: erneut senden nach der
  Verarbeitung; bei deaktivierter OCR eine Fassung mit Textebene hochladen oder die Administration um OCR bitten und
  dann erneut hochladen; Fragen ohne Dokumentbezug bleiben möglich;
- Audit `chat_document_not_ready`, nur mit IDs und Zuständen, ohne Dateinamen.

Fragt jemand ohne Anhang in der Unterhaltung nach „dem Dokument“ und hat die Akte nicht verarbeitete Dokumente, nennt
die Antwort diese lokal unter „Offene Prüfpunkte“ als nicht ausgewertet. Allgemeine Fragen ohne Dokumentbezug sind
unverändert.

**Teil-Scans.** `extract_text` erkennt Seiten ohne nennenswerten Text, die zu mindestens 50 % ihrer Fläche aus einem
Bild bestehen. Logos und Unterschriften bleiben darunter, leere Seiten zählen nicht. Ein solches PDF ist ein OCR-Fall:
- Bei aktiver OCR wird das ganze Dokument mit der unveränderten Funktion `run_ocr` erkannt.
- Bei deaktivierter OCR bleibt es `pending`, und der Teiltext wird nicht als Akteninhalt gespeichert.

**Unverändert:**
- Presidio, Pseudonymisierung, Restrisiko-Scan, Pre-Cloud-Gate, Payload-Siegel, Fail-Closed;
- OCR-Konfiguration (`OCR_ENABLED=false` bleibt Standard und in der installierten `.env`);
- OCR-/Ollama-Implementierung.

Dokumentinhalt erreicht die Cloud weiterhin nur über `_prepare_and_gate` → Gateway.

**Tests** (`tests/test_chat_document_readiness.py`, 32 Tests, echter Pfad bis zur Cloud-Grenze, nur synthetische
Dokumente). Abgedeckt:
- Scan bei OCR aus;
- Scan bei OCR an (isolierte QA-Konfiguration, gebündeltes Tesseract): Inhalt im Payload, 0 Goldwerte im Klartext;
- leerer Scan bei OCR an;
- defektes PDF, leere DOCX, laufende Verarbeitung;
- PDF/DOCX erfolgreich, mit Goldstandard;
- gemischte Anhänge, Teil-Scan, Aktendokument ohne Anhang;
- Statusänderung (Status → pending, Löschen; streaming und nicht-streaming) sowie die Gegenrichtung;
- `/send`, Dokumentaktion, Datenschutz-Rückfrage;
- Statusmodell und Aktenisolation.

Gegenprobe: Auf dem Code vor der Änderung schlagen 19 der 32 Tests fehl. Die übrigen 13 (allgemeine Fragen, Erfolgsfälle,
Statusmodell) bestehen auf beiden Ständen.

**Installierter Build (`4594788`, 11.10.2026).**
- Build: `windows\build.ps1`, Exit 0. Bündel geprüft: Template und CSS enthalten den Darstellungs-Fix, das Python-Archiv
  enthält `app.documents.readiness`.
- Umgebung: still installiert, QA-Datenbank, abgefangene Cloud-Anfragen, nur synthetische Dokumente (`Status_*`).
- OCR aus: installierte Konfiguration. OCR an: nur Prozessvariable der QA-Instanz; die `.env` ist unverändert und hat
  keinen OCR-Eintrag, damit gilt `false`.

| Fall | Cloud-Payloads | Ergebnis |
|---|---|---|
| OCR aus: Scan; Folgefrage zum Dokument; Teil-Scan; defektes PDF (`FileDataError`); leere DOCX; gemischte Anhänge | je **0** | PASS. Lokaler Hinweis je Dokument, Layout gemessen: 3 Blöcke untereinander, 92 % Breite |
| OCR aus: allgemeine Folgefrage nach dem Hinweis; allgemeine Frage im neuen Chat | je 1 | PASS. Weder Hinweistext noch Dateiname im Payload |
| OCR aus: Text-PDF, DOCX | je 1 | PASS. „Estricharbeiten“ und „1.200,00“ im Payload, nach Datenschutz-Rückfrage „Keller“ (Kellergeschoss) mit „kein personenbezogenes Datum“ |
| OCR an: Scan; Teil-Scan | je 1 | PASS. Inhalt per OCR im Payload, beim Teil-Scan auch die gescannte Seite 2 („Dachrinne“) |
| OCR an: leeres OCR-Ergebnis | **0** | PASS. Hinweis „kein verwertbarer Text“, Layout wie oben |
| OCR an: allgemeine Frage | 1 | PASS |

Hinweis zu „Keller“: Das Wort ist zugleich ein häufiger Nachname, und die unveränderte Rückfrage vor dem Cloud-Aufruf ist
gewolltes Verhalten (bis zur Entscheidung 0 Payloads). Dokumentstatus und Datenschutz-Rückfrage greifen damit
nacheinander, ohne sich gegenseitig zu umgehen.

## 10. Dokumentbezug ohne Stichwort, Sonderfälle, „Keller“ (13.10.2026, `10853c1`)

**Befund 1 – Frage ohne Dokument-Stichwort (reproduziert im echten Pfad).** Nach einem nicht lesbaren Scan ging
„Welche Einwände erhebt die Gegenseite?“ an die Cloud. Der Prompt war als „Es liegt KEIN … Dokumentkontext vor – dies
ist eine allgemeine Frage“ gerahmt, und lokal gab es keinen Hinweis. Dokumentinhalt floss dabei nicht ab (er war nicht
lesbar), aber die Antwort konnte eine Auswertung vortäuschen. Ob eine Frage ein Dokument braucht, lässt sich ohne
Inhalt nicht sicher erkennen; weitere Stichwörter würden das Problem nur verschieben.

Maßnahme, kontextbasiert (`ChatService._unready_matter_documents`, `DraftingService._with_unreadable_documents_note`):
hat die Akte der Unterhaltung nicht lesbare Dokumente, dann
1. erhält die Cloud-KI einen festen Hinweis ohne Dateiname und ohne Inhalt („Inhalt unbekannt, keine Aussagen,
   keine Auswertung vortäuschen“). Er durchläuft wie jeder Sachverhalt den Gateway und ersetzt die irreführende
   Rahmung „allgemeine Frage“;
2. nennt die Antwort lokal unter „Offene Prüfpunkte“ die nicht ausgewerteten Dokumente.

Ausdrücklich dokumentbezogene Fragen und Anhänge blockieren weiterhin (Abschnitt 9). Allgemeine Fragen funktionieren
weiter; ohne nicht lesbare Dokumente bleibt die Rahmung unverändert.

**Befund 2 – lokale Hinweise im Cloud-Verlauf.** Der Block „Offene Prüfpunkte / Hinweise“ früherer Antworten ging als
Teil des Gesprächsverlaufs mit in spätere Cloud-Anfragen, zum Beispiel Dateinamen oder vorsichtshalber maskierte
Begriffe. `_build_history` überträgt bei Assistenten-Nachrichten jetzt nur noch den Antworttext. Der Block ist ein
lokaler Hinweis und kein Gesprächsbeitrag.

**Befund 3 – Rückfrage „Keller“.** „Die Abdichtung im Keller ist mangelhaft“ löste eine Rückfrage aus. Ursache war
allein die Rolle `vor_finitem_verb` („Keller ist“), obwohl der Parser „Keller“ als Kern der Präpositionalgruppe „im
Keller“ erkennt; Subjekt ist „Abdichtung“. Diese eine Rolle entfällt jetzt nach Präpositionen mit verschmolzenem
Artikel (im, ins, am, ans, aufs, ums, übers, überm, unterm, hinterm, vorm), wenn der Parser die Zuordnung bestätigt.
Bewusst nicht ausgenommen sind beim, vom, zum und zur, weil sie umgangssprachlich auch vor Namen stehen („beim
Huber“).

Positive Tests: „im Keller“, „Im Winter“, „am Fenster“. Negative Tests, weiterhin geschützt durch Rückfrage oder
automatische Maskierung: Subjekt, „Herr Keller“, „von Keller“, „Zeuge Keller“, „gegen Keller“, „beim Keller“, „vom
Keller“.

Benchmarks vor und nach der Änderung identisch:

| Benchmark | Wert |
|---|---|
| Namen-Lecks gesamt | 0,1 % (1/1100); Ergebnis je Gruppe identisch, nur die Laufzeit differiert |
| Dokumentpfad-Lecks | 0/990 |
| Rückfragen | 64/990 |
| Fachwort-Fälle | 105/800 |
| Fehlalarme | 1/132 |
| Anweisungen | 1/17 |
| Rückfrage-Begriffe in 22 Dokumenten | 8 |
| Fachbegriffe mit Rückfrage | 15/153 |

**Installierter Build `10853c1`** (Bündel geprüft: alle drei Änderungen enthalten; QA-Datenbank, abgefangene
Cloud-Anfragen, OCR aus, nur synthetische Dokumente):

| Fall | Cloud-Payloads | Ergebnis |
|---|---|---|
| Scan anhängen, danach „Welche Einwände erhebt die Gegenseite?“ | 0, dann 1 | PASS: Payload mit Unlesbar-Hinweis, ohne „allgemeine Frage“, ohne Dateiname und ohne Inhalt; lokaler Hinweis unter der Antwort |
| Danach allgemeine Frage | 1 | PASS: Verlauf ohne lokale Hinweise und ohne Dateiname |
| Text-PDF mit „im Keller“ | 1 | PASS: keine Rückfrage, „im Keller“ lesbar im Payload |
| Fortsetzung nach Rückfrage („durch Lange“ → „Als Person“) | 0 vor, 1 nach der Entscheidung | PASS: „Lange“ pseudonymisiert, Inhalt vorhanden |
| Statusänderung während der pausierten Anfrage (Rückfrage offen, Dokument über die Aktenroute gelöscht, dann Entscheidung abgeschickt) | 0 | PASS: Hinweis „inzwischen gelöscht“ |
| „Dokument analysieren“ auf der Aktenseite, Scan | 0 | PASS |
| „Dokument analysieren“, Text-PDF | 1 | PASS: Dokumentinhalt im Payload |

Zwei Prüfskript-Fehler habe ich anhand von Datenbank, Screenshot und Payload korrigiert bewertet:
- Der lokale Hinweis steht im eigenen Block „Offene Prüfpunkte“, das Skript las nur den Antworttext.
- Beim Prüfwort „Nachbesserungsfrist“ siehe die Risiken unten.

Nicht im installierten Build prüfbar ist eine Statusänderung *zwischen* Vorprüfung und Kontextaufbau, also innerhalb
von Millisekunden. Sie ist nur per Test belegt: NOT VERIFIED (installiert).

**Offene Risiken.**
- Die automatische NER pseudonymisiert „Nachbesserungsfrist“ im längeren Dokumentkontext als Person. Das ist
  vorbestehend und auf `687ba3f` identisch reproduziert. Es ist kein Leck, verfälscht aber den Inhalt. Eine Korrektur
  würde die automatische Erkennung im Dokumentpfad abschwächen; deshalb ist nichts geändert.
- Der Unlesbar-Hinweis wirkt auf die Antwort der Cloud-KI nur als Anweisung. Deterministisch sind der lokale Hinweis
  und die Blockade bei ausdrücklichem Dokumentbezug.
- Ein Nachname direkt nach „im/am/ins …“ vor einem finiten Verb hat diese eine Rolle nicht mehr; NER und alle übrigen
  Rollen greifen weiter.

## 11. Fachwort-Komposita und Nachnamen nach Artikel (13.10.2026, `25ecc05`)

**Ausgangsfall.** „Die Nachbesserungsfrist endet am 30.11.2026.“ wurde im Dokumentkontext zu „Die [PERSON_01] endet …“.

**Ursache.** spaCys NER (`de_core_news_lg`) etikettiert das ihm unbekannte Kompositum kontextabhängig als PER (Score
0,85). Es handelt sich um einen Erkennungsfehler, keinen Extraktions- oder Kontrollfehler. Keines der vorhandenen
Gegensignale trennt diesen Fall:
- Der Rahmensatz-Test gibt 5/5.
- POS=NOUN taugt nicht, weil auch echte Namen oft als NOUN getaggt werden.
- `drop_common_noun_persons` ist im Dokumentpfad bewusst aus (16,4 % Namen-Lecks).
- Die Vektor-Vokabularprüfung zerlegt auch Nachnamen („Stucke|n|Schneider“).

**Verwandter, schwerwiegenderer Fehler (Leck, Priorität 1).** Die neue Gegenprobe zeigt: Nachnamen direkt nach einem
Artikel („Nach Rücksprache hat die Pfannenstiel …“) gingen in 39 von 96 Fällen im Klartext in den Payload. Alle
Zweitsignale (PROPN ohne NER-Label, Modell-Probe, Rückfrage-Kandidaten) verlangten ein Wort ohne Artikel davor.

**Maßnahmen** (`app/privacy/presidio_ner.py`, `review.py`):
1. `is_derivational_compound`: Präfix, dann Ableitungs-Fugenelement (-ungs-/-heits-/-keits-/-schafts-/-tions-/-täts-),
   dann ein Kopfnomen aus einer geschlossenen Klasse von Sach- und Rechtsnomen (Frist, Urkunde, Anspruch, Beschluss,
   Recht, …).
   - Solche Einwort-Treffer der Kategorien Person und Organisation werden verworfen, ebenso in beiden Zweitsignalen und
     bei den Rückfrage-Kandidaten.
   - Die Regel sitzt im Detektor und wirkt deshalb identisch in Pseudonymisierung und Restrisiko-Scan.
   - Bewusst nicht für ORT, weil es reale Ortsnamen dieser Bauart gibt („Hoffnungsthal“).
2. `detect_article_names`: dieselbe Modell-Probe mit derselben Schwelle (≥4/5) auch direkt nach einem Artikel. Die
   Funktion fügt nur Treffer hinzu (fail-closed).

**Unabhängiger Review.** Ein separater Prüfer hat ausschließlich gelesen und gemessen. Befund (mittel): Die erste
Fassung verlangte nur ein dem Modell bekanntes Kopfwort. Damit gingen namensförmige Bildungen wie „Siedlungsmann“,
„Lichtungsberger“ oder „Stationsberg“ neu im Klartext durch, weil -mann, -berg und -hausen ebenfalls bekannte Wörter
sind. Behoben durch die geschlossene Kopfnomen-Klasse. Die neuen Leck-Tests (bar, nach Präposition, nach Artikel,
Tabellenzelle) schlagen gegen die erste Fassung in 17 von 18 Fällen fehl und bestehen jetzt. Die übrigen Review-Punkte
ergaben keine neuen Blockaden und keine Inkonsistenz zwischen Pseudonymisierung und Restrisiko-Scan.

**Messung** (identische Korpora vorher und nachher, nur synthetisch):

| Benchmark | vorher | nachher |
|---|---|---|
| Namen im Klartext (1100 Fälle, mask) | 0,1 % | 0,1 % (je Gruppe identisch) |
| Dokumentpfad: Namen-Lecks / Rückfragen | 0/990 / 64 | 0/990 / 64 |
| Dokumentpfad: Fachwort-Fälle maskiert | 105/800 | **97/800** |
| Fachwort-Korpus (600) | 87 | **81** (automatisch 34 → 28) |
| Fachbegriffe mit Rückfrage | 15/153 | 15/153 |
| Neu: Fachwort nach Artikel maskiert | 19/240 | **9/240** |
| Neu: Nachname nach Artikel im Klartext | **39/96** | **25/96** |

Installierter Build `25ecc05`, im Bündel geprüft: Upload eines synthetischen PDFs im echten Chat. Der abgefangene Payload
enthält „Die Nachbesserungsfrist endet am [DATUM_01]. Nach Rücksprache hat die [PERSON_01] die Unterlagen übergeben. Der
Betrag von 1.200,00 EUR ist offen; die Zustellungsurkunde liegt vor.“ (PASS, keine Rückfrage).

**Offene Risiken.**
- **Nachnamen nach Artikel (25/96 weiterhin im Klartext):** Betroffen sind Nachnamen, die zugleich Gattungswörter sind
  („die Bergmann“, „der Zimmermann“), oder die das Modell nur schwach als Name liest („Grotelueschen“ 1/5,
  „Stuckenschneider“ 3/5).
  - Eine Schwelle von 3 brächte 1 Namen und kostete 1 Fachwort.
  - Eine Rückfrage für alle unbekannten Wörter nach Artikel würde viele Fachkomposita treffen, weil sich auch
    Nachnamen in bekannte Wörter zerlegen lassen.
  - Als bekannte Grenze per `xfail` markiert.
- **Neue Fehlalarme durch `detect_article_names`:** „Teilabnahme“ (Probe 5/5, ohnehin auch als ORT maskiert) und
  „Schöffin“. Sie werden maskiert, nicht freigegeben.
- **„Feuchtigkeitsschaden“ als ORT:** Bleibt bewusst maskiert, weil die Regel nicht für ORT gilt.
- **Grenzen der Klassenregel:** Die Kopfnomen-Klasse ist geschlossen. Neue Fachkomposita mit anderem Kopfnomen
  profitieren nicht davon, und ein realer Nachname dieser Bauart ist in keinem Benchmark beobachtet, aber nicht
  ausgeschlossen.

## 12. Anrede-/Rollenkontext, Folgeerwähnungen, Briefkonventionen (Folgezyklus 13.10.2026, `38361af`, `91be2c3`)

**Vorgehen.** Damit die Regeln nicht an die 12 Namen von §11 angepasst werden, habe ich drei neue, vorab festgelegte
synthetische Benchmarks mit eigenen Namen angelegt:
- `scripts/bench_name_followups.py`:
  - A: förmliche Einführung mit Folgeerwähnung,
  - B: Name nach Artikel,
  - N: Negativfälle;
- `scripts/bench_letter_conventions.py`: Briefkonventionen;
- `scripts/bench_role_object_negatives.py`: Rollenwort + Objekt. Er stammt aus dem Red-Team-Review und enthält einen
  Schriftsatz ohne Namen.

Zusätzlich zählt eine eigene Prüfung über alle 2017 Benchmark-Texte × 2 Modi die Fail-Closed-Blockaden, weil die
Benchmarks Blockaden nicht als Leck werten.

**Befunde (vorher, gemessen).**
- „Herr Sommer“, „Zeugin König“ und „Rechtsanwältin Sommer“ gingen **vollständig** im Klartext an die Cloud.
  - Ursache: Die NER etikettiert Nachnamen, die zugleich Gattungswörter sind, nicht.
  - Die Rückfrage-Rolle „nach Rollenwort“ verlangt zusätzlich die Modellprobe „Herr X hat unterschrieben“, an der diese
    Namen scheitern.
- Nach „Herr Dr. Graf“ oder „Petra Graf“ blieb die Folgeerwähnung „Graf hat …“ im Klartext: 83 von 1288 Fällen. Ersetzt
  wurde nur der Mehrwort-Span.

**Maßnahmen** (`app/privacy/presidio_ner.py`; alle im gemeinsamen Detektor, also identisch für Pseudonymisierung und
Restrisiko-Scan):
1. **Starke Anreden** (Herr/Herrn/Frau/Hr./Fr./Eheleute/Familie/gez. sowie artikellose Rollenwörter wie „Zeugin
   Sommer“) → das nächste Wort ist ein Name.
   - Das gilt auch über Zeilen- und Mehrfach-Leerzeichengrenzen hinweg.
   - Übersprungen werden „Dr./Prof.“, Namenspartikel und Amts- bzw. Rollenbezeichnungen („Frau Rechtsanwältin
     Weber“ → Weber; „Herr Vorsitzender“ → kein Name).
2. **Rollenwort mit Artikel:** nur wenn der Parser das Wort an das Rollenwort hängt bzw. beide Subjekt desselben Verbs
   sind, und danach ein Nominalgruppen-Ende oder ein satzfinales Partizip folgt.
3. **Folgeerwähnungen:** Namensbestandteile jedes Personentreffers werden im ganzen Text mitersetzt, inklusive
   Bindestrich-Teilen, Genitiv und Versalien. Weitergetragene Formen sind vom Imperativ-Filter ausgenommen.
4. **Feminin-Singular-Kontext:** „die X“ als Subjekt eines Verbs im Singular bzw. „mit der X“, wenn X eine maskuline
   Personenbezeichnung ist (-mann, movierte Form „X-in“ im Vokabular) **und** die Modellprobe ≥ 1 ergibt. Ohne die
   Probe-Bedingung stiegen die Fehlalarme bei Rollenwörtern wie „Mieter“ und „Kläger“ von 1 auf 13 von 132.
5. **Personenbezogene Feldbezeichnungen** („Ansprechpartner: X“) und die invertierte Namenszeile („Sommer, Klaus“).

**Unabhängiges Red-Team (nur lesend, rund 700 Fallpaare, eigene neue Namen).**
- Kritisch an meiner ersten Fassung: Rollenwort + Objekt („dass der Mieter Mängel anzeigt“) wurde als Name maskiert und
  im ganzen Text ersetzt. Im synthetischen Schriftsatz waren es 17 falsche Platzhalter, 8 Fachbegriffe gingen verloren.
- Dazu kamen Umgehungen: „Die Zeugin X“ am Satzanfang, „Herr⏎X“, „Hr./Fr.“, „Herr von X“, Bindestrich, Genitiv,
  Versalien.
- Alles ist behoben und als Regressionstest festgehalten.
- Laut Red-Team generalisieren die Regeln auf neue Namen (52/340 → 0/340). Konsistenz zwischen Pseudonymisierung und
  Restrisiko-Scan: kein Befund.

**Verworfen, gemessen:**
- **Rubrum-Regel** („X ./. Y“): Der Punkt in „./.“ zählt im Imperativ-Filter als Satzende, der Restrisiko-Scan fand den
  Treffer trotzdem. Ergebnis war eine inkonsistente Fail-Closed-Blockade.
- **Schwelle 3 bzw. Zerlegung in bekannte Wörter für unbekannte Namen nach Artikel:** Auch Nachnamen lassen sich in
  bekannte Wörter zerlegen („Stucke|n|Schneider“).
- **Modellprobe als Trennsignal Name ↔ Fachwort nach Rollenwort:** „Sommer“ 0, „Feuchtigkeit“ 2.

| Benchmark (identische Fälle) | vorher (`54d73bf`) | nachher (`91be2c3`) |
|---|---|---|
| A Folgeerwähnung nach Einführung, Klartext | 83/1288 | **0/1288** |
| B Name nach Artikel ohne Einführung | 40/138 | **32/138** |
| Briefkonventionen, Klartext | 26/220 | **6/220** (nur Rubrum) |
| Artikel-Benchmark §11, Klartext | 25/96 | **21/96** |
| Namen-Benchmark (1100), Klartext | 0,1 % | **0,0 %** |
| Dokumentpfad: Lecks / Rückfragen | 0/990 / 64 | 0/990 / **43** |
| Fachwort-Korpora (800 / 600) | 97 / 81 | 97 / 81 |
| Fehlalarme Namens-Benchmark | 1/132 | 2/132 (+„Verwalter“ in grammatisch falscher Schablone) |
| Negativfälle N / Rollenwort-Objekt / Schriftsatz | 3/22 / 0/12 / 0 | 3/22 / 0/12 / 0 |
| Fail-Closed-Blockaden (2017 Texte × 2 Modi) | 10 | **4** (alle vorbestehend) |

**Installierter Build `91be2c3`** (im Bundle geprüft), echter Chat-Upload, abgefangener Payload: „Sehr geehrte Frau
[PERSON_01], wie mit [PERSON_02] besprochen, hat die Zeugin [PERSON_03] ausgesagt. [PERSON_02] bestätigte dies … Es ist
streitig, ob der Mieter Mängel angezeigt hat. Die Kaution von 2.550,00 EUR ist offen … gez. [PERSON_04]“ (PASS).

Der Zwischenlauf auf `38361af` war FAIL („hat die Zeugin König ausgesagt“ im Klartext) und führte zur Korrektur in
`91be2c3`.

**Offene Risiken (gemessen).**
- **Maskuliner Artikel + Berufsname ohne Einführung** („Der Fischer hat angerufen“, 17 der 32 B-Fälle): echt mehrdeutig.
- **Feminine Rahmen bei Nicht-Personen-Wörtern:** „die Sommer hat“ und „die Winter hat“ bleiben im Klartext.
- **Rubrum** „In Sachen X ./. Y“.
- **Fehlparse als Objekt:** „hat die Mandantin Licht unterschrieben“ (Parser: Objekt).
- **Schwache unbekannte Namen nach Artikel:** „Grotelueschen“.
- **Vorbestehende Fehlalarme:** „Herr Richter … Der Richter am Amtsgericht“ (wertbasierte Ersetzung) und
  Berufsbezeichnungen wie „Der Schneider hat die Hose gekürzt“.

### 12a. Verworfen nach zweitem Red-Team: Rückfrage bei maskulinem Artikel + Berufsname (`c548256`, zurückgenommen in `cbf48af`)

Idee: „Der Fischer hat angerufen“ ist echt mehrdeutig (Beruf oder Herr Fischer). Für die enge Klasse „maskuliner Artikel
+ maskuline Personenbezeichnung (-mann/movierbar) + Modellprobe ≥ 1, ohne Rollen-/Amtswörter“ sollte Lexono deshalb
nachfragen; im nicht-interaktiven Modus würde vorsichtshalber maskiert.

Gemessener Nutzen:
- Gruppe B: 32 → 26/138,
- Artikel-Benchmark: 21 → 17/96,
- 0 zusätzliche Rückfragen in den 22 synthetischen Kanzleidokumenten.

Das zweite unabhängige Red-Team hat vier realistische Schriftsatzabsätze und 38 Fachbegriffe gemessen, zusammen 118 Texte
ohne Namen. Ergebnis: **0 → 34 Rückfragen** und **14 → 48 verlorene Fachbegriffe** im Modus „mask“. Betroffen waren
zentrale Rechtsbegriffe wie „Der **Halter** haftet nach § 7 StVG“, „Der **Verbraucher** kann widerrufen“ und „Der
Verkäufer ist **Kaufmann**; der Kaufmann hat die Ware nach § 377 HGB zu untersuchen“. Weil der Gateway einen Wert
überall ersetzt, wird der Begriff im ganzen Text unlesbar.

Selbst nachgeprüft und bestätigt. Der Nutzen steht dazu in keinem vertretbaren Verhältnis; die Regel ist zurückgenommen
und war nie gepusht. Offen bleibt damit „maskuliner Artikel + Berufsname ohne Einführung“.

## 13. Normzitate (Folgezyklus 13.10.2026, `a1d20c7`)

**Befund (vorbestehend, auf `54d73bf` identisch).** „Der Halter haftet nach § 7 StVG.“ wurde zu „§ 7 [PERSON_01]“.
Gesetzesabkürzungen mit Kleinbuchstaben (StVG, StVO, StPO, BetrVG, ArbZG, BauGB, …) sind keine Versalwörter und fallen
deshalb unter die Namensdetektoren. Gemessen mit `scripts/bench_statute_citations.py`, 45 Abkürzungen × 4 Zitatformen:
**37 von 180** gingen verloren.

**Maßnahme.** `presidio_ner.drop_statute_abbreviations` verwirft Einwort-Treffer in Abkürzungsform (mindestens zwei
Großbuchstaben, höchstens 12 Zeichen), die unmittelbar auf ein Normzitat folgen („§ 7“, „§§ 1, 2“, „§ 280 Abs. 1“,
„Art. 3“). Die Regel ist kontextgebunden, nicht listenbasiert, und sitzt im gemeinsamen Detektor; sie gilt also identisch
für den Restrisiko-Scan.

| Messung | vorher | nachher |
|---|---|---|
| Normzitate: verlorene Abkürzungen | 37/180 | **3/180** (BImSchG, BeurkG in Mehrfachzitaten) |
| Normzitate: Namen-Lecks / Blockaden | 0 / 0 | 0 / 0 |
| Alle übrigen Benchmarks (§11, §12) und Blockaden (4) | | unverändert |

**Installierter Build `a1d20c7`** (im Bündel geprüft, inklusive Revert): Der Payload enthält „Sehr geehrter Herr
[PERSON_01], der Halter haftet nach § 7 StVG; ein Verstoß gegen § 3 StVO ist streitig. Die Ansprüche nach § 280 Abs. 1
BGB in Höhe von 4.250,00 EUR … gez. [PERSON_02]“ (PASS). Die Payload-Prüfungen aus §11 und §12 wurden auf demselben
Build wiederholt: PASS.

## 14. Unabhängiger Fallsatz: deklinierte Rollenwörter, Verbketten (Folgezyklus 13.10.2026, `8009d7b`)

Grundlage ist der vom zweiten Red-Team vorbereitete, von mir ausgeführte Fallsatz mit 34 neuen Namen und 22
Fachbegriff-Gegenfällen in verbfinalen Sätzen.
- **Partizip-Regel aus `91be2c3`:** 12 → 10 Lecks, ohne zusätzliche Fachwort-Verluste. Der Nutzen ist damit unabhängig
  von den eigenen Benchmarks belegt.
- **Weitere Ursachen:**
  - Die Rollenliste kannte nur Grundformen („den **Zeugen** Nebel“, „den **Sachverständigen** Zorn“).
  - Die Partizip-Regel prüfte nur ein einzelnes Verb („wird der Zeuge Krebs **aussagen müssen**“, „der Zeuge Gast
    **belehrt worden ist**“).
- **Maßnahmen:**
  - deklinierte Formen schwacher Nomen;
  - mehrteilige infinite Verbkomplexe;
  - Apposition gleicher Satzfunktion: Name und Rollenwort sind beide Akkusativ- bzw. Dativobjekt desselben Verbs.
    Echte Objekte haben eine andere Funktion als das Rollenwort.

| Messung | vorher | nachher |
|---|---|---|
| Unabhängiger Fallsatz: Namen im Klartext | 12/34 | **7/34** |
| Unabhängiger Fallsatz: verlorene Fachbegriffe / Rückfragen / Blockaden | 4 / 3 / 0 | 4 / 2 / 0 |
| Fachsprach-Korpus Red-Team 2 (118 Texte ohne Namen): verlorene Wörter / Rückfragen / Blockaden | 14 / 0 / 0 (`54d73bf`) | 14 / 0 / 0 |
| Alle Benchmarks aus §11–§13 und Blockadenprüfung | | unverändert |

Restliche 7: „Krebs“ (Parser: Objekt), „Eiche“ (attributives Partizip „von der Zeugin Eiche vorgelegten“) und fünf
Berufsnamen ohne Einführung (siehe §12a).

**Installierter Build `8009d7b`** (Bündel geprüft): Der Payload enthält „Die Kammer hat den Zeugen [PERSON_01] geladen
… dass der Zeuge [PERSON_02] belehrt worden ist. Gegen das Urteil hat der Beklagte Berufung eingelegt; die Zinsen von
312,50 EUR sind offen. Die Klägerin hat den Sachverständigen [PERSON_03] abgelehnt.“ (PASS). Die Payload-Prüfungen aus
§11–§13 wurden auf demselben Build wiederholt: alle PASS.

## 15. Drittes Red-Team: Gesamtdokumente, Regression „Anschriftenblock“, unsichtbare Zeichen (Folgezyklus 13.10.2026, `76c9d1b`)

Das dritte unabhängige Red-Team hat nur gelesen und gemessen: 6 realistische Gesamtdokumente mit vorab festgelegtem
Goldstandard und neuen Namen (Klageschrift, Aktennotiz, Mahnschreiben, Protokoll, E-Mail-Verlauf, Gutachten) sowie 20
Umgehungsformate. Ergebnis: **0 Namenslecks** in den 6 Dokumenten. Zwei Befunde:

1. **Regression aus `38361af` (kritisch für die Nutzbarkeit, kein Leck):** Briefe mit Anschriftenblock wurden in 29 von
   42 Varianten fail-closed blockiert, auf `54d73bf` waren es 2 von 42.
   - Ursache: Im Restrisiko-Scan steht der Name bereits als Platzhalter, die Analysefassung macht daraus Leerzeichen.
     Die Lückenprüfung der Anrede-Regel verglich nur mit dem unmittelbar vorangehenden Leerraum-Token, sodass
     „Betreff“, „Unser“ oder „Az“ als Name galten.
   - Meine Blockadenprüfung (§12) hatte das nicht erfasst, weil kein Benchmark-Text einen Anschriftenblock enthielt.
   - **Behoben:** Die Lücke wird jetzt im Originaltext ab der Anrede gemessen; ein Platzhalter oder Text dazwischen
     bricht ab, erlaubt ist höchstens ein Zeilenwechsel.
   - Ergebnis: **2/42**, dieselben zwei vorbestehenden Fälle wie auf `54d73bf`. Neuer Test
     `tests/test_privacy_letter_blocks.py` (29 Fälle; davon schlagen 27 auf `562bd94` fehl).
2. **Vorbestehendes Leck (kritisch):** Ein weiches Trennzeichen U+00AD im Namen („Hab­ermehl“, typisch bei
   PDF-Extraktion) verhinderte die Erkennung. **Behoben:** Der Gateway entfernt unsichtbare Formatzeichen (U+00AD,
   U+200B–U+200D, U+2060, U+FEFF) aus allen Feldern vor jeder Erkennung. Kein sichtbarer Inhalt ändert sich.

**Weitere Gegenprobe.** Den vom zweiten Red-Team vorbereiteten Fachsprach-Korpus (118 Texte ohne Namen) habe ich auf dem
Endstand ausgeführt: 14 verlorene Wörter, 0 Rückfragen, 0 Blockaden. Das ist identisch mit `54d73bf`; der gesamte Zyklus
hat dort keine Fehlalarme eingeführt.

**Installierter Build `76c9d1b`** (im Bündel geprüft). Der vollständige Brief wird nicht blockiert: „Herrn [PERSON_01]
[ADRESSE_01] Unser Zeichen: 204/26 Betreff: Mahnung … wir mahnen nach § 286 BGB die Zahlung von 1.200,00 EUR an. Herr
[PERSON_03] hat die Rechnung geprüft … gez. [PERSON_04]“ (PASS). Alle früheren Payload-Prüfungen (§11–§14) auf
demselben Build: PASS.

**Vom dritten Red-Team gemessene, vorbestehende und offene Punkte:**
- **Versalnamen im Rubrum** („In Sachen HABERLAND ./. WINKELMANN“): Leck, auf `54d73bf` identisch.
- **Durchgängige Kleinschreibung** („mein vermieter herr sauer“): Leck.
- **Fehlalarme** wie „Nachbarstreit Hecke“ → Person (mit Weitergabe), „Andernfalls“ → Person, „die MdE“ → Person,
  „Quebec Task Force“ → Organisation.
- **Gleicher Name, verschiedene Platzhalter** („Gernot Hahn“ / „Herr Hahn“): Das ist datenschutzseitig unkritisch, kann
  aber die Lesbarkeit der KI-Antwort mindern.
- **Attributives Partizip** („von der Zeugin Eiche vorgelegten“): Gemessen nicht trennscharf, weil der Parser Name und
  Objekt gleich analysiert; verworfen.

### 15a. Offener, gemessener Befund: Versalien (vorbestehend; weitgehend behoben in §16)

Gemessen auf `3af2849`, Modus „mask“, alle Anfragen zugelassen, also **Leck statt Blockade**:
- „HERRN⏎GERNOT HAHN⏎BIRKENALLEE 17⏎12345 MUSTERSTADT“ (Anschriftenblock für Fensterumschläge): **vollständig im
  Klartext**, Name **und** Anschrift.
- „Der Beklagte WINKELMANN hat bestritten.“: im Klartext.
- „In Sachen HABERLAND ./. WINKELMANN“: im Klartext.
- „Herr WINKELMANN … WINKELMANN bestätigte“: geschützt, über die NER.

Ursache: NER, Zweitsignale und Adressmuster erkennen Namen und Anschriften fast nur an der Groß-/Kleinschreibung.

Erprobt und **verworfen** (nicht committet): eine längengleiche zweite Analysefassung in normaler Schreibung („HAHN“ →
„Hahn“), analog zur Umlaut-Wiederherstellung. Ergebnis war nur ein Teilerfolg: „GERNOT“ erkannt, „HAHN“, Anschrift,
„Der Beklagte WINKELMANN“ und Rubrum weiterhin im Klartext. Eine belastbare Lösung muss auch die Adressmuster in
`app/privacy/detectors.py` einbeziehen (Pseudonymisierung und Restrisiko-Scan gemeinsam) und braucht eine eigene
Fehlalarm-Messung für Versal-Überschriften („BETREFF: MAHNUNG ODER KLAGE“). **Nächster Schritt mit hoher Priorität.**

## 16. Versalien: zusätzliche, validierte Treffer aus einer Analysefassung (Folgezyklus 13.10.2026, `39112b7`, `58a673f`)

**Ursache** (siehe §15a). NER, Zweitsignale und Adressmuster stützen sich auf die Groß-/Kleinschreibung. Ein Name oder
eine Anschrift in Versalien („GERNOT HAHN“, „BIRKENALLEE 17“) sieht für sie aus wie eine Überschrift.

**Änderung** in `app/privacy/detectors.py` (`detect_all(..., uppercase_variant=True)`, `_uppercase_variant_spans`):

1. **Längengleiche Analysefassung.** Nur *kurze* Versalfolgen werden in normale Schreibung gebracht („HAHN“ → „Hahn“):
   - höchstens 4 Wörter;
   - getrennt nur durch ein Leerzeichen oder einen Bindestrich;
   - Wörter ab 4 Buchstaben mit Vokal.

   Vokallose Abkürzungen wie STVG oder GMBH bleiben unverändert. Ganze Versalsätze bleiben ebenfalls unverändert: gemessen
   hält das Modell dort Verben für Namen (34 statt 26 verlorene Wörter).
2. **Alle Detektoren laufen auf dieser Fassung.** Ein Treffer wird nur übernommen, wenn er einen umgeschriebenen Bereich
   berührt und validiert ist:
   - Bekannte Entitäten, Personen mit Titel, Firmen mit Rechtsform und Regex-Detektoren außer der Anschrift gelten ohne
     weitere Prüfung.
   - Anschrift: Straße mit Hausnummer gilt immer; PLZ mit Ort nur, wenn der Ort als Eigenname (PROPN) erkannt wird.
   - NER-Person/-Ort: Jedes umgeschriebene Wort muss PROPN sein. Kein einzelnes Versalwort allein auf seiner Zeile
     (Überschrift). Kein Treffer direkt nach einem Institutionswort („LANDGERICHT KÖLN“).
   - NER-Organisationen werden nie übernommen.
   - Gesetzesabkürzungen nach einem Normzitat („§ 3 EFZG“) werden verworfen, mit demselben Filter wie in `presidio_ner`.
3. **Nur zusätzliche Treffer.** Ein ursprünglicher Treffer wird nie verdrängt oder verkürzt. Erlaubt ist nur eine
   Obermenge **gleicher** Kategorie („HANS MEIER“ ⊃ „MEIER“). Der Wert ist immer der Originalausschnitt, an den Rändern
   auf Buchstaben/Ziffern beschnitten (`58a673f`: der Rubrum-Trenner „./.“ verschwand sonst im Platzhalter).
4. **Restrisiko-Scan unverändert** (`security_check.py`, `uppercase_variant=False`). Grund (Red-Team 4, C2): die
   Varianten-NER hängt vom Kontext ab. Neben Platzhaltern erzeugte sie inkonsistente Fail-Closed-Blockaden normaler
   Briefe. Der Scan ist damit nicht schwächer als zuvor; nur die Pseudonymisierung maskiert mehr.

Presidio, Pseudonymisierung, Restrisiko-Scan, Pre-Cloud-Gate, Payload-Siegel und Fail-Closed sind nicht abgeschwächt.

**Messung vorher (`f843ab7`) / nachher**, jeweils synthetisch:

| Messung | vorher | nachher |
|---|---|---|
| Versalnamen/-anschriften im Klartext (`scripts/bench_uppercase.py`, 36 Werte) | 35/36 | **0/36** |
| Versal-Schlüsselwörter verloren (Überschriften, Fachbegriffe, Normzitate, Beträge; 59) | 3/59 | 3/59 |
| ganze Versalabsätze, verlorene Wörter (`scripts/bench_uppercase_negatives.py`) | 26/171 | 26/171 (identisch) |
| Blockaden, 2017 Texte × 2 Modi | 4 | 4 |
| Briefe mit Anschriftenblock blockiert (§15) | 2/42 | 2/42 |

Alle übrigen Benchmarks aus §11–§15 sind identisch zu `f843ab7`:
- Normzitate 3/180;
- Folgeerwähnungen A 0/1288, B 32/138, N 3/22;
- Artikel-Bench 21/96;
- Briefe 6/220;
- Rollenobjekte 0;
- Namen 0,0 %, Fehlalarme 2/132;
- Dokumentpfad 0/990;
- 600er-Korpus 81;
- Fachsprache 14/0/0.

Die 6 Gesamtdokumente des dritten Red-Teams ergeben weiterhin 0 Lecks.

**Viertes Red-Team** (unabhängiger Agent, nur lesend/messend). Befunde und Umgang damit:
- **C1 (kritisch):** Ein Brief mit Versal-Anschrift und „Sehr geehrter Herr Meier“ wurde in 6 von 8 Fällen blockiert. Der
  Varianten-Treffer „HANS MEIER“ wurde wegen der Überlappung verworfen, „HANS“ blieb im Klartext. **Behoben** durch die
  Obermengen-Regel und den Restrisiko-Scan ohne Variante.
- **C2:** kontextabhängige Blockaden, etwa in Gerichtslisten. **Behoben** über Punkt 4.
- **B1:** Versal-Gesetzesabkürzungen mit Vokal („EFZG“, „MUSCHG“) wurden zu Personen. **Behoben.**
- „LANDGERICHT KÖLN“ wurde zu Person, „Herr THIELKER“ blieb im Klartext. **Behoben**, mit Tests.

Neue Tests in `tests/test_privacy_uppercase.py` (54). Gegen `f843ab7` schlugen 20 der damals 33 Tests fehl; die übrigen
prüfen, dass Negativfälle unverändert bleiben. Gesamtsuite: 3727 bestanden. Es gibt 4 Fehlschläge in `test_design_refresh`: die
Logo-Dateien wurden im Arbeitsbaum außerhalb dieser Arbeit gelöscht, nicht von diesem Zyklus.

**Installierter Build `58a673f`** (Funktionen im Bündel geprüft). Echter Chat-Upload eines PDFs, Payload am lokalen
Capture-Endpunkt:
- Fall 7:
  - Eingang: „HERRN / HANS MEIER / HAUPTSTRASSE 1 / 10115 BERLIN / BETREFF: IHR SCHREIBEN … Sehr geehrter Herr Meier …
    AMTSGERICHT HAMBURG“.
  - Payload: „HERRN [PERSON_02] [ADRESSE_01] BETREFF: IHR SCHREIBEN VOM [DATUM_01] Sehr geehrter Herr [PERSON_02], … §
    535 BGB. AMTSGERICHT HAMBURG“.
  - Ergebnis: **PASS**, nicht blockiert, Überschrift und Gericht lesbar.
- Fall 6:
  - Eingang: Versalbrief mit Rubrum und „GEZ. FENJA RÜBSAM“.
  - Payload: „HERRN [PERSON_01] [ADRESSE_01] BETREFF: MAHNUNG In Sachen [PERSON_02] ./. [PERSON_03] … GEZ. [PERSON_04]“.
  - Ergebnis: **PASS**.
- Frühere Fälle 1–5 (§11–§14): alle **PASS** (je genau ein Cloud-Aufruf, Namen geschützt, Fachbegriffe/Beträge/
  Normzitate lesbar).
- Sichtbare Oberfläche (Screenshot): Dokument „Verarbeitet“, Cloud-KI „Bereit“. Die Antwort zeigt die Namen nur lokal,
  nach der Re-Personalisierung; im Payload stehen Platzhalter.
- Ein erster Lauf scheiterte in allen 7 Fällen am QA-Aufbau: Der Ablageordner des Capture-Servers fehlte, der Endpunkt
  lieferte deshalb einen Fehler. Nach dem Anlegen des Ordners lief derselbe Build durch; Lexono selbst wurde dafür nicht
  geändert.

Auf Build `39112b7` hatte Fall 6 noch „In Sachen [PERSON_02] [PERSON_03]“ ergeben, ohne „./.“; das führte zu `58a673f`.

**Offen / nicht behoben** (überwiegend vom vierten Red-Team gemessen):
- Versalzeilen mit mehr als 4 Wörtern;
- Namen mit 3 Buchstaben („OTT“, „LAU“) und vokallose Namen;
- mehrere Leerzeichen oder Tabulatoren zwischen Versalwörtern;
- nicht-deutsche Großbuchstaben (Ş, Æ, ẞ);
- Bindestrich-Varianten (U+2011, Halbgeviertstrich);
- Hausnummer nach maskiertem Ort;
- Versal-Firmennamen (NER-Organisationen werden bewusst nicht übernommen).

Vorbestehend und nicht durch diesen Zyklus verursacht: „Der Beklagte WINKELMANN“ wird als `[ORGANISATION]` maskiert. Der
Name ist geschützt, das Rollenwort geht verloren.

Eine vollständige Leckfreiheit ist nicht belegt.

### 16a. Großbuchstaben außerhalb A–Z/ÄÖÜ, Teiltreffer, Rubrum-Trenner (Folgezyklus 13.10.2026)

**Befund** (eigene Messung, synthetisch). Versalnamen mit nicht-deutschen Buchstaben gingen im Klartext an die Cloud:
„ŞAHIN“, „GÓMEZ“, „RENÉ LEFÈVRE“, „MØLLER“; „SØREN KJÆRGAARD“ wurde blockiert. Ergebnis: **5 von 17 Werten im
Klartext, 1 Blockade.**

Ursachen:
- Das Versalwort-Muster kannte nur A–Z/ÄÖÜ. Ein Wort mit „Ş“ oder „Ø“ wurde nie in normale Schreibung gebracht.
- Überlappte ein Varianten-Treffer einen ursprünglichen Treffer **anderer** Kategorie, wurde er ganz verworfen. Beispiel:
  Die NER hielt „ŁUKASIEWICZ“ für einen Ort, deshalb wurde der Variantentreffer „Agnieszka Łukasiewicz“ verworfen und der
  Vorname blieb im Klartext.

**Änderung** (`app/privacy/detectors.py`):
1. **Zweiter, rein additiver Durchgang.**
   - Er nutzt Großbuchstaben aller Schriften des BMP.
   - Wörter, deren Kleinschreibung die Länge ändert (z. B. „İ“), werden übersprungen.
   - Die Vokalprüfung arbeitet auf dem Grundbuchstaben (NFD), zusätzlich gelten Æ/Ø/Œ als Vokale.
   - Der erste Durchgang (A–Z/ÄÖÜß) bleibt **unverändert**. Seine Treffer gelten im zweiten Durchgang wie ursprüngliche
     Treffer.
   - Für rein deutsche Texte sind beide Fassungen gleich; der zweite Durchgang entfällt dann.
2. **Teiltreffer.** Statt den ganzen Varianten-Treffer zu verwerfen, werden nur die Teile **außerhalb** der ursprünglichen
   Treffer übernommen. Der ursprüngliche Treffer bleibt exakt erhalten.
3. **Rubrum-Trenner.** Ein Varianten-Treffer wird am Rubrum-Trenner „./.“ geteilt; die NER lieferte „Dvořák ./. Haberland“
   als einen Treffer.

**Fünftes Red-Team** (unabhängiger Agent, nur messend; 88 Texte, alter und neuer Stand identisch):
- **Kritisch, behoben:** Allein mit dem erweiterten Zeichenvorrat wurden lange Versalzeilen wie „KLAGE DES HERRN EMRE
  ŞAHIN GEGEN DIE FIRMA“ zu **einer** Folge mit mehr als 4 Wörtern. Auf `58a673f` hatte „Ş“ die Folge beendet, „EMRE“ war
  geschützt; mit der Erweiterung allein blieben „EMRE“, „HANS MEIER“ und „YILDIRIM“ im Klartext. Ebenso ergab „Zeugin
  İPEK AYDIN“ keinen Treffer mehr. Behoben durch die zwei Durchgänge.
- **Nach der Korrektur auf denselben 88 Texten:** 25 statt 43 Lecks. Kein Fall, in dem ein vorher geschützter Wert nun im
  Klartext steht. 0 statt 1 Blockade.
- **Offsets:** NFD-Text, İ, ẞ, griechisches Schluss-Sigma, weiches Trennzeichen, Vollbreite und weitere Sonderzeichen:
  kein Absturz; jeder Wert entspricht exakt dem Originalausschnitt.
- **Neue Fehlalarme (mittel, akzeptiert):** nur in fremdsprachigem Versaltext, z. B. „ÉTAT“, „VENTE“, „PRÁCE“, „MÉNAGE“,
  „RÈGLEMENT GÉNÉRAL“ (→ Person). Das ist eine Übermaskierung, kein Leck. In deutschem und juristischem Versaltext gibt es
  keine neuen Fehlalarme.

**Messung `58a673f` → neu** (identische Texte, nacheinander im selben Interpreter gemessen):

| Messung | `58a673f` | neu |
|---|---|---|
| Versalnamen mit nicht-deutschen Buchstaben im Klartext (20 Werte) | 5/17 + 1 Blockade | **0/20** |
| Versalnamen/-anschriften (`bench_uppercase`) | 0/36 | 0/36 |
| Versal-Fachinhalt verloren | 3/59 | 3/59 |
| Versal-Negativkorpus | 26/171 | 26/171 |
| Blockaden, 2017 Texte × 2 Modi | 4 | 4 |
| Dokumentpfad: Namen-Lecks / Rückfragen / Fachwörter | 0/990 / 43 / 97/800 | identisch |

Ebenfalls identisch: Normzitate 3/180, Folgeerwähnungen 0/1288, 32/138, 3/22, Artikel 9/240, Briefe 6/220,
Rollenobjekte 0, Briefblockaden-Test 29/29.

Neue Tests in `tests/test_privacy_uppercase.py` (68 insgesamt). Davon schlagen 7 auf `58a673f` fehl; 4 sichern die
Red-Team-5-Fälle ab. Gesamtsuite: 3741 bestanden; die 4 Fehlschläge in `test_design_refresh` betreffen fehlende Logo-Dateien
im Arbeitsbaum, wie in §16.

**Installierter Build `e82a84c`** (Bündel geprüft: `_uppercase_variant_pass`, `_UPPERCASE_RUN_AZ`, `_has_vowel` enthalten;
Installation Exit 0; Health 200). Echter Chat-Upload, Payload am Capture-Endpunkt:
- **Fall 8, neu:**
  - Eingang: „HERRN / MEHMET ŞAHIN / BIRKENALLEE 17 / … In Sachen DVOŘÁK ./. HABERLAND / KLAGE DES HERRN EMRE ŞAHIN GEGEN
    DIE FIRMA / Die Zeugin GÓMEZ … Die Klägerin MØLLER … § 286 BGB“.
  - Payload: „HERRN [PERSON_01] [PERSON_02] [ADRESSE_01] BETREFF: MAHNUNG In Sachen [ORGANISATION_01] ./. [PERSON_03]
    KLAGE DES HERRN [PERSON_04] [ORGANISATION_02] GEGEN DIE FIRMA Die Zeugin [ORT_01] … Die Klägerin [PERSON_05] verlangt
    1.200,00 EUR nach § 286 BGB.“
  - Ergebnis: **PASS**. Alle 8 Namen sind geschützt, nichts blockiert, Fachinhalt lesbar. Einige Namen tragen die falsche
    Kategorie (Organisation/Ort).
  - Sichtbare Oberfläche (Screenshot): Dokument „Verarbeitet“; die lokale Antwort zeigt die Namen mit Ş/Ř/Ó/Ø korrekt
    re-personalisiert.
  - Die Lesbarkeitsprüfung wertet U+00A0 als Leerzeichen: PyMuPDF schreibt mit der Arial-Schrift geschützte Leerzeichen
    in das Test-PDF.
- **Fälle 1–7:** alle **PASS** auf demselben Build.

**Weiterhin offen** (vom fünften Red-Team gemessen):
- griechische/kyrillische Versalnamen (Vokalprüfung nur lateinisch);
- Vollbreite-Buchstaben;
- „İ“ im Wortinneren („İBRAHİM“);
- Rubrum ohne Leerzeichen („ŞAHIN./.MØLLER“);
- geschütztes Leerzeichen zwischen Versalwörtern;
- lange Versalzeilen mit mehr als 4 Wörtern (auch rein deutsch, vorbestehend);
- Vornamen, die je nach NER als Organisation oder Ort maskiert werden (maskiert, aber in falscher Kategorie).

### 16b. Ungewöhnliche Leerzeichen zwischen Versalwörtern (Folgezyklus 13.10.2026, `a1d4203`)

**Befund** (eigene Messung, danach im installierten Build `e82a84c` mit echtem Upload reproduziert).
- In einer Versal-Anschrift mit geschütztem (U+00A0), schmalem geschütztem (U+202F) oder Achtel-Geviert-Leerzeichen
  (U+2009) zwischen Vor- und Nachname gingen beide Namen im Klartext an die Cloud. Gemessen: **4 von 33 Werten** je
  Leerzeichenart; mit normalem Leerzeichen 0 von 33.
- Solche Zeichen entstehen typisch bei der PDF-Extraktion. Das Test-PDF mit Arial-Schrift enthielt sie bereits.
- Ursache: Die Versalfolge, Teile der NER und die Adressmuster kennen nur das normale Leerzeichen als Worttrenner.

**Folge im installierten Build** (`payload_044`, Fall 9 „vorher“):
- Payload: „HERRN KLAUS BROCKHAGEN [ADRESSE_01] … FRAU ODETTE PASCHKE [ADRESSE_02]“.
- Zusätzlich stand in der lokalen Vorabanalyse (Ollama) „Herrn Klaus Brockhagen … Frau Odette Paschke“.
- Das ist **kein eigener Leckkanal**: Ollama sieht nur den bereits pseudonymisierten Text und hat den dort unerkannt
  gebliebenen Namen wiedergegeben.
- `extend_with_local_note` prüft den Zusatztext gegen alle bekannten Originalwerte, aber bewusst ohne eigenen NER-Scan.
  Ein im Dokument unerkannter Name erscheint daher auch dort. Bewertung: Folgefehler der Erkennungslücke, kein
  zusätzlicher Informationsabfluss; als Beobachtung offen.

**Änderung** (`app/privacy/gateway.py`, neben der bestehenden Entfernung unsichtbarer Formatzeichen):
- Vor **jeder** Erkennung werden Unicode-Leerzeichen der Kategorie Zs (außer dem normalen Leerzeichen) zu einem normalen
  Leerzeichen: U+00A0, U+1680, U+2000–U+200A, U+202F, U+205F, U+3000.
- Länge und sichtbarer Inhalt bleiben gleich. Pseudonymisierung und Restrisiko-Scan sehen danach denselben,
  normalisierten Text.
- Tabulatoren bleiben unverändert, weil sie Tabellenstruktur tragen.

**Messung** (`58a673f` → neu):
- Geschützte, schmale und Achtel-Geviert-Leerzeichen: je 4/33 → **0/33**.
- Alle Benchmarks aus §16a sind unverändert (identische Ausgabe bis auf die Laufzeit).
- 15 neue Tests; alle 15 schlagen auf `58a673f` fehl.
- Gesamtsuite: 3756 bestanden, 4 Logo-Fehlschläge wie zuvor.

**Installierter Build `a1d4203`** (Bündel geprüft: `_UNUSUAL_SPACES` im Gateway, `_uppercase_variant_pass` in den
Detektoren; Installation Exit 0; Health 200).
- **Fall 9:** Versal-Anschriften mit U+00A0/U+202F, echter Upload.
  - Vorher, auf `e82a84c`: **FAIL**, 4 Namen im Klartext (siehe oben).
  - Nachher: **PASS**. Payload: „HERRN [PERSON_01] [ADRESSE_01] BETREFF: MAHNUNG FRAU [PERSON_02] [ADRESSE_02] wir mahnen
    nach § 286 BGB die Zahlung von 1.200,00 EUR an.“
  - Screenshot: Dokument „Verarbeitet“, lokale Antwort korrekt re-personalisiert.
- **Fälle 1–8** auf demselben Build: alle **PASS**.

**Weiterhin offen:**
- Tabulator zwischen Versal-Vor- und Nachname: 4/33. Behoben in §16c.
- Doppelte Leerzeichen zwischen **allen** Wörtern: 17/33, auch in normaler Schreibung; vorbestehend. Doppelte Leerzeichen
  markieren in der Dokumentaufbereitung Zeilenumbrüche, eine Zusammenfassung wäre daher mehrdeutig. Nicht geändert.

### 16c. Tabulator zwischen Versal-Vor- und Nachname (Folgezyklus 13.10.2026, `45172a6`)

**Befund** (eigene Messung). „HERRN / KLAUS⇥BROCKHAGEN“ (Tabulator, z. B. aus DOCX/PDF) ergab 4 von 33 Werten im
Klartext. Mit normalem Leerzeichen waren es 0.

**Änderung.** Nur in der Analysefassung des **zweiten, additiven** Durchgangs (§16a) wird ein Tabulator zwischen zwei
umgeschriebenen Versalwörtern zum Leerzeichen. Bedingung: Es ist der **einzige** Tabulator der Zeile. Der Payload-Text
behält den Tabulator.

**Erste Fassung verworfen:** Ohne die Zeilenbedingung wurden Versal-Tabellenzeilen („NACHBAR⇥LAERM⇥JA“) zu Personen;
gemessen stieg der Verlust von 2/49 auf 5/49 Tabellenwörter.

**Messung** (`58a673f` → neu):

| Messung | `58a673f` | neu |
|---|---|---|
| Tabulator zwischen Versalnamen | 4/33 | **0/33** |
| Versal-Tabellen mit mehreren Spalten (verlorene Wörter) | 2/49 | 2/49 |
| zweispaltige Versal-Tabellen (verlorene Wörter) | 1/50 | 1/50 |

Ebenfalls gemessen:
- Alle Benchmarks aus §16a sind unverändert.
- Red-Team-5-Satz: 24 Lecks (`58a673f`: 43), kein neu im Klartext stehender Wert, keine Blockade.
- 4 neue Tests.
- Gesamtsuite: 3760 bestanden; die 4 Logo-Fehlschläge wie zuvor.

**Installierter Build `45172a6`** (Bündel geprüft, Installation Exit 0, Health 200):
- **Fall 10, neu:** DOCX mit „KLAUS⇥BROCKHAGEN“ und einer Versal-Tabelle. Der Tabulator steht nachweislich im von der
  Anwendung extrahierten Text.
  - Payload: „HERRN [PERSON_01] [ADRESSE_01] BETREFF: MAHNUNG … § 286 BGB … ZEUGE⇥THEMA⇥LADUNG NACHBAR⇥LAERM⇥JA
    HAUSMEISTER⇥SCHLUESSEL⇥NEIN“.
  - Ergebnis: **PASS**.
  - Screenshot: Dokument „Verarbeitet“, lokale Antwort korrekt.
- **Fälle 1–9** auf demselben Build: alle **PASS**.

### 16d. Sechstes Red-Team (Endstand gegen `f843ab7`) und Gesetzesabkürzungen ohne § (`9350f40`)

**Sechstes Red-Team** (unabhängiger Agent, nur messend):
- **Umfang:** 171 neue synthetische Texte, Endstand `df84001` gegen den Ausgangsstand `f843ab7`. Modus „mask“ für alle
  Texte, „ask“ für 11, dazu Rückführung (`reconstruct_response`) und Kontrolllauf mit normalen Leerzeichen.
- **Regressionen: keine.** Kein auf `f843ab7` maskierter Wert steht neu im Klartext. Keine neue Blockade; 2 frühere
  Blockaden durch Ortsreste neben U+00A0 entfallen.
- **Lecks:** 122 → 32 bzw. 13 → 5 Werte im Klartext.
- **Rückführung und Offsets:** Alle Werte entsprechen exakt dem Original. Kein Absturz mit U+2028/U+2029/U+0085,
  vertikalem Tabulator, Seitenvorschub oder gemischten Tabulatoren/Leerzeichen.
- **Rückführung kosmetisch:** In 16 Fällen ist sie nicht bytegleich, weil vorbestehend ein Anrede-Alias auf den
  vollen Namen zurückgeführt wird.
- **Neue Fehlalarme (mittel):**
  - Gesetzesabkürzungen ohne § („nach dem BEEG“, „die STVO“) und „Art. 6 Abs. 1 lit. b DSGVO“ → Ort/Person;
  - „IBAN“, „COVID“;
  - Markennamen („AUDI A4“, „VW GOLF“), Kürzel („ADAC“);
  - „Gerichtsstand ist BERLIN“ → Ort.

**Änderung** (nur für die **zusätzlichen** Varianten-Treffer; ursprüngliche Treffer bleiben unberührt): Verworfen werden
Variantentreffer, die
- einer festen Liste von Gesetzesabkürzungen sowie „IBAN“ und „COVID“ entsprechen (auf `f843ab7` lesbar), oder
- direkt auf ein Normzitat mit Buchstaben-Untergliederung folgen („lit. b/f“, „Buchst. a“).

Der gemeinsame Zitatfilter in `presidio_ner` kannte nach „lit.“ nur Ziffern.

**Messung** (7 Red-Team-Fehlalarme / 3 Namen in denselben Sätzen):
- `58a673f`: 5/7 verloren.
- neu: **0/7** verloren, Namen weiterhin geschützt, z. B. „Nach Art. 6 Abs. 1 lit. b DSGVO hat Frau [PERSON_01]
  eingewilligt.“
- Alle Benchmarks aus §16a sind unverändert.
- Red-Team-5-Satz: 24 Lecks.
- 6 neue Tests.
- Gesamtsuite: 3766 bestanden; die 4 Logo-Fehlschläge wie zuvor.

Markennamen, Kürzel wie „ADAC“ und Ortsnamen in Versalien bleiben übermaskiert. Das ist kein Leck, mindert aber die
Lesbarkeit.

**Weiterhin offen** (vom sechsten Red-Team gemessen, vorbestehend oder nicht abgedeckt):
- doppelte Leerzeichen zwischen Versal-Vor- und Nachname;
- mehrere Tabulatoren in einer Namenszeile („ANSPRECHPARTNER⇥FRANZISKA⇥LEHMKUHLE⇥DURCHWAHL“);
- Straßen ohne Straßensuffix in Versalien („AM ANGER 5“);
- Adelsprädikate („VON DER HEYDT-WALLRAF“);
- Rubrum in normaler Schreibung nach einem Aktenzeichen („Görtzen ./. Habicht-Lindner“);
- Namen mit „İ“/„Ş“ in einzelnen Kontexten;
- griechische und kyrillische Versalnamen.

**Installierter Build `9350f40`** (Bündel geprüft: `_UPPERCASE_NON_NAMES` enthalten; Installation Exit 0; Health 200).
- **Fall 11, neu:** Versal-Anschrift, „BETREFF: AUSKUNFT NACH ART. 15 DSGVO“, „Art. 6 Abs. 1 lit. b DSGVO … Frau ODETTE
  PASCHKE“, „nach dem BEEG“, „Die STVO“, „angegebene IBAN“.
  - Payload: „FRAU [PERSON_01] [ADRESSE_01] BETREFF: AUSKUNFT NACH ART. 15 DSGVO Nach Art. 6 Abs. 1 lit. b DSGVO hat Frau
    [PERSON_01] eingewilligt. Elterngeld richtet sich nach dem BEEG. Die STVO gilt. Bitte überweisen Sie auf die
    angegebene IBAN.“
  - Ergebnis: **PASS**.
  - Die im Screenshot nach „Art. 6 Abs.“ endende lokale Antwort ist ein Artefakt des QA-Stubs: Die Echo-Antwort des
    Capture-Servers nimmt die ersten drei durch „. “ getrennten Abschnitte. Das ist kein Lexono-Verhalten.
- **Fälle 1–10** auf demselben Build: alle **PASS**.

## 17. Presidio-Audit: Konfiguration, Datenfluss, Vergleich A/B/C (13.10.2026, `fc0ee62`)

### 17.1 Bestandsaufnahme (gemessen; Skript `scripts/bench_presidio_audit.py`)

**Versionen:** presidio-analyzer/-anonymizer 2.2.364, spaCy 3.8.16, `de_core_news_lg` 3.8.0.

Das Modell ist im installierten Build enthalten (`_internal\de_core_news_lg`, `presidio_analyzer\conf`). Indirekter
Laufzeitnachweis: NER-Platzhalter (`[ORT_01]`, `[PERSON_01]`) in echten Payloads des installierten Builds.

**Konfiguration** (`presidio_ner._get_analyzer_engine`):
- NLP-Engine `spacy` mit `de_core_news_lg`, `supported_languages=["de"]`.
- `LemmaContextAwareEnhancer` (Presidio-Standard).
- Für „de“ registriert: 10 Recognizer (Crypto, Date, Email, Iban, Ip, MedicalLicense, MacAddress, Phone, Url, Spacy).
- **Lexono fragt davon nur `PERSON`, `LOCATION`, `ORGANIZATION` ab** (Schwelle `_MIN_SCORE = 0.5`). Strukturierte Daten
  (E-Mail, Telefon, IBAN, Steuer-ID, BIC, Datum, Aktenzeichen, Kunden-/Vertrags-/Rechnungsnummern, Anschrift, Firma mit
  Rechtsform) erkennen die eigenen Regex-Detektoren in `detectors.py`.

**Beobachtungen:**
- Der `SpacyRecognizer` vergibt einen festen Score von 0,85. Die Schwelle 0,5 wirkt daher nur auf Muster-Recognizer.
- Die Kontextwörter der Standard-Recognizer sind englisch („phone“, „bank“) und für deutsche Texte wirkungslos.
- Das Modell-Label `MISC` ist in Presidios Zuordnung nicht enthalten und wird ignoriert.
- Presidio 2.2.364 enthält **13 offizielle deutsche Recognizer**, standardmäßig `enabled: false`: Steuer-ID,
  Steuernummer, USt-IdNr., Renten-/Kranken-/Ausweis-/Pass-/Führerscheinnummer, Kfz, Handelsregister, PLZ, LANR, BSNR.
  Lexono hat sie bisher nicht genutzt.

**Datenfluss:**
1. Upload (PDF über PyMuPDF, DOCX über python-docx, TXT; OCR nur bei `ocr_enabled`, Standard aus).
2. Dokumenttext über `_normalize_document_text`, danach Aktenkontext.
3. `ClaudePrivacyGateway.prepare_request`:
   - unsichtbare Zeichen und ungewöhnliche Leerzeichen bereinigen (§15, §16b);
   - zusammengeführter Text mit Feldmarkern;
   - `Pseudonymizer`/`detect_all`: Regex-Detektoren, `detect_presidio_entities` (Presidio-Analyse,
     Umlaut-Analysefassung, Lexono-Namensregeln §11–§14), Versal-Durchgänge (§16), Überlappungsauflösung,
     Wiederholungen;
   - im Modus „ask“ Rückfragen;
   - Restrisiko-Scan (`SecurityCheckService`, `detect_all` ohne Versal-Fassung auf dem pseudonymisierten Text);
   - Final Payload Gate; Siegel.
4. Optional die lokale Ollama-Notiz (`extend_with_local_note`, Leak-Prüfung, neues Siegel).
5. Claude-Aufruf; Antwortprüfung; lokale Rückführung.

### 17.2 Vergleich auf eingefrorener Testbasis

**Testbasis:**
- `scripts/presidio_audit_corpus.json`: 65 synthetische Fälle, 80 PII-Werte, 104 lesbar zu haltende Werte.
- Erzeugt mit `scripts/build_presidio_audit_corpus.py`; Nummern mit gültiger Prüfziffer aus festem Zufallsstartwert.
- Inhalts-SHA-256 `2de13db5…`. Die Testbasis wurde nach dem ersten Lauf **nicht** verändert.

**Varianten** (gleicher Text im Dokumentpfad):
- **A:** Presidio wie konfiguriert.
- **B:** alle de-Recognizer plus die 13 deutschen, alle Entitäten, gleiche Schwelle.
- **C:** vollständige Lexono-Pipeline.

| Variante | übersehene PII | fälschlich maskiert | Blockaden | Rückfragen (ask) | Laufzeit/Fall* |
|---|---|---|---|---|---|
| A | 24/80 | 13/104 | – | – | 0,007 s |
| B | 15/80 | 13/104 | – | – | 0,018 s |
| C vorher (`1c9d91c`) | 13/80 | 7/104 | 0 | 2 | 0,059 s |
| **C nachher (`fc0ee62`)** | **8/80** | **7/104** | **0** | **1** | **0,059 s** |

\*Die Laufzeit von C ist isoliert gemessen: Median aus 3 Durchgängen, alt und neu je zweimal, ±1 %. In parallelen
Läufen schwankte sie zwischen 0,13 und 0,16 s, das ist Lastrauschen.

**Einordnung (Schlussfolgerung aus den Messwerten):**
- **Modellgrenzen, auch in B:**
  - Namen in Versalien („HAHN“, „WINKELMANN“) und in Kleinschreibung („herr sauer“);
  - Folgeerwähnungen ohne Anrede („Eiche bestätigte“);
  - Fachwörter als Entität („TATBESTAND“, „Prüfpunkt“, „Landgerichts“);
  - fremdsprachige Sätze.

  Presidio allein schließt diese Lücken nicht. Die Lexono-Regeln (§11–§16) schließen sie nachweislich: name_caps 4/9 →
  0/9, role_followup 1/6 → 0/6, Überschriften-Fehlalarme 6 → 2.
- **Integrationsfehler (behoben, E1):**
  - `normalize_ner_span_boundaries` verwarf jeden Treffer ohne **lateinischen** Buchstaben. Die gedachte Regel war:
    Treffer ohne Buchstaben verwerfen.
  - Folge: Griechische/kyrillische Namen, die Presidio korrekt erkannte, gingen im Klartext hinaus. A war hier besser
    als C (1/6 gegenüber 3/6).
  - Jetzt prüft die Regel Buchstaben jeder Schrift: C 3/6 → 1/6, Rückfragen 2 → 1.
- **Ungenutzte Presidio-Funktion (übernommen, E2, eingeschränkt):** deutsche Recognizer, siehe 17.3.
- **Weiterhin Lücken in C:**
  - Steuernummer, Personalausweis, Kfz-Kennzeichen (Recognizer verworfen, siehe 17.3);
  - URL mit Namen, IP-Adresse;
  - Postfachnummer;
  - Kleinschreibung;
  - griechische Versalnamen.

### 17.3 Übernommen und verworfen

**E1 übernommen:** Buchstabenprüfung aller Schriften.

**E2, erste Fassung mit 12 deutschen Recognizern, verworfen nach unabhängigem Review.** Die eigene Messung zeigte FN
11 → 5 bei unveränderten Fehlalarmen. Die Testbasis enthielt die Nummern aber nur im kanonischen Format direkt neben dem
Kontextwort.
- Das Review fand in 78 realistischen Sätzen **6 neue Fail-Closed-Blockaden**, vorher 0. Ursache: Kontextabhängige
  Muster (Grundscore < 0,5) erreichten die Schwelle im Restrisiko-Scan, weil dort Platzhalter als Leerzeichen den Abstand
  zum Kontextwort verkürzen. Beispiel: „Fahrzeuge KL AB 12, KL CD 34 und KL EF 56“.
- Dazu **16 Fehlalarme in 68 Sätzen:**
  - „VW T 6“, „DER B 51“ (Kfz);
  - 13-stellige Liefer-/Vorgangsnummern (Steuernummer ohne Prüfziffer);
  - 9-stellige Nummern neben „Praxis“ (BSNR);
  - Teilenummern im Ausweisformat.

**E2 übernommen (eingeschränkt):** nur Recognizer, deren Muster die Schwelle **ohne Kontextwort** erreichen und ein
unverwechselbares Format haben. Damit sind Pseudonymisierung und Restrisiko-Scan konsistent.
- USt-IdNr. („DE“ + 9 Ziffern, Prüfziffer);
- Handelsregister („HRA/HRB n“);
- Rentenversicherungsnummer im strengen Format (Geburtsdatum + Buchstabe + Prüfziffer).

Die Treffer laufen im selben Presidio-Aufruf (kein zweiter spaCy-Lauf) als Kategorie `kennung`, Platzhalter
`[KENNUNG_nn]`.

Gegenmessung des Reviews auf der eingeschränkten Fassung:
- Zahlenreiche Sätze: 1 Änderung („Referenz DE202600123“, gültiges USt-IdNr.-Format, vertretbar).
- **Blockadesätze: 0 neue Blockaden.**
- 180 Zufallsnummern (9/11/13 Stellen, Teile-/Ausweisformate): 0 Änderungen.
- Leckformate: 8 verbessert, darunter „de489681509“ und „hrb 1234“. „HRB 123 456“ ist nur teilweise geschützt
  (`[KENNUNG_01] 456`).

**Verworfen** (gemessen):
- Kfz, Steuernummer, LANR/BSNR, Personalausweis, Reisepass, Krankenversicherungs-, Führerscheinnummer: Blockaden bzw.
  Fehlalarme (siehe oben).
- Steuer-ID: deckt `detect_steuer_id` bereits ab.
- IP: „Version 2.4.1.0“ und „Kapitel 10.2.3.4“ würden als IP erkannt.
- URL: Quellenangaben wie gesetze-im-internet.de würden maskiert. In einzelnen Kontexten maskiert die spaCy-NER solche
  URLs bereits heute als Organisation (vorbestehend).
- PLZ: Grundscore 0,05 erreicht die Schwelle nie.
- `MISC`-Zuordnung: 3 Treffer auf bereits geschützter PII gegenüber 8 Fehlalarmen auf lesbar zu haltenden Werten
  („StVO“, „Streitwert“, Fremdsprachensätze).

**Keine Eigenregel entfernt:** Alle Lexono-Regeln schließen gemessene Lücken (siehe 17.2). Für ein Entfernen gibt es
keinen Beleg.

**Bekannte Nebenwirkung von E1 (gering, Review gemessen):** Einzelne griechische/kyrillische Gattungswörter
(„ΠΕΡΙΛΗΨΗ“, „ООО“) werden maskiert. Das ist eine Übermaskierung, kein Leck; keine Blockaden.

### 17.4 Validierung

**Regressionsbenchmarks:** identisch zu `1c9d91c`.
- Versalien 0/36, 3/59; Versal-Absätze 26/171; Normzitate 3/180;
- Folgeerwähnungen 0/1288, 32/138, 3/22; Artikel 9/240; Briefe 6/220; Rollenobjekte 0;
- Blockaden 4 von 2017×2; Dokumentpfad 0/990; Briefblockaden 29/29.

**Tests:**
- 20 neue Tests in `tests/test_privacy_presidio_audit.py`; davon schlagen auf `1c9d91c` 9 der ersten Fassung fehl.
- Gesamtsuite: 3786 bestanden; die 4 Fehlschläge in `test_design_refresh` kommen von außerhalb dieser Arbeit gelöschten
  Logo-Dateien.

**Installierter Build `fc0ee62`** (Bündel geprüft: `_id_spans`, `_GERMAN_ID_RECOGNIZERS` enthalten; Installation
Exit 0; Health 200).
- **Fall 12, neu:** DOCX mit griechischem Namen, HRB, USt-IdNr., Rentenversicherungsnummer, Lkw-Flotte mit drei
  Kennzeichen, „VW T 6“ und Quellen-URL; echter Upload.
  - Payload: „Der Mandant [PERSON_01] … eingetragen unter [KENNUNG_01] beim Registergericht, USt-IdNr. [KENNUNG_02]. Die
    Versicherungsnummer [KENNUNG_03] … Fahrzeuge KL AB 12, KL CD 34 und KL EF 56 der Spedition; Kfz: VW T 6 Transporter.“
  - Alle 5 Schutzwerte sind maskiert, keine Blockade.
  - Zwei Lesbarkeitsprüfungen schlagen fehl („Lkw-Flotte“ → `[ORT_01]`, Quellen-URL → `[ORGANISATION_02]`). Beides
    ist **vorbestehend**, auf `1c9d91c` identisch. Derselbe Text liess dort 4 Kennwerte und den Vornamen im Klartext.
  - Screenshot: Die lokale Antwort zeigt die Kennnummern und den griechischen Namen korrekt re-personalisiert.
- **Fälle 1–11** auf demselben Build: alle **PASS**.

**Nachweisgrenzen:** Geprüft wird die vom Lexono-Client an den (lokal umgeleiteten) Anthropic-Endpunkt gesendete
Payload, nicht der TLS-Verkehr. Screenshots belegen nur die sichtbare Darstellung.

## 18. Verbliebene Lecks der Testbasis: Ursachen und Behebung (13.10.2026, `49c3687`, `3df99a2`)

**Ausgangsstand:** `39b4900`, Build `fc0ee62` installiert. Eingefrorene Testbasis (`scripts/presidio_audit_corpus.json`,
Inhalts-SHA-256 `2de13db5…`): **8/80 PII im Klartext**, 7/104 Fehlalarme, 0 Blockaden, 1 Rückfrage.

### 18.1 Ursachen (je Fall Stufe für Stufe verfolgt)

Kein Fall wurde vom Restrisiko-Scan oder vom Pre-Cloud-Gate erfasst: Pseudonymisierung und Restrisiko-Scan übersahen
dieselben Werte (konsistente Lecks, keine Blockaden). Extraktion und Normalisierung waren in keinem Fall die Ursache.

| Fall | Leck | versagender Schritt | Art |
|---|---|---|---|
| L01 | „herr sauer“ (Kleinschreibung) | Presidio und Lexono erkennen nichts; „sauer“ ist zugleich ein gewöhnliches Adjektiv | Modellgrenze |
| G03 | „ΠΑΠΑΔΟΠΟΥΛΟΣ“ | Presidio erkennt nur das erste Wort. Die Versal-Fassung übersprang griechische Wörter, weil die Vokalprüfung nur lateinische Vokale kannte. Danach verwarf die Wortart-Validierung den Treffer („Γιαννης“ = NOUN). | Integrationsfehler (Lexono) |
| A05 | Postfachnummer „10 20 30“ | weder Presidio noch Lexono hatten einen Detektor | Erkennungslücke |
| P05, P09, P10 | Steuernummer, Personalausweis, Kfz | Presidio **erkennt** sie (0,65–1,0). Die Recognizer waren bewusst nicht registriert: kontextabhängige Scores erzeugten Blockaden im Restrisiko-Scan (§17). | Integrationsentscheidung |
| P13 | IP-Adresse | Presidio-IpRecognizer erkennt sie (0,95), war aber nicht angefragt (Fehlalarme bei Versionsnummern) | Integrationsentscheidung |
| P12 | LinkedIn-Link mit Namen | UrlRecognizer erkennt ihn, war aber nicht angefragt (Quellenangaben) | Integrationsentscheidung |

### 18.2 Änderungen

1. **Kennnummern mit Bezeichnung** (`presidio_ner._ID_LABEL_BEFORE`):
   - Betroffen sind die offiziellen Presidio-Recognizer DeTaxNumber, DeIdCard, DePassport, DeFuehrerschein und DeKfz
     sowie der vorhandene IpRecognizer.
   - Ein Treffer wird nur übernommen, wenn seine Bezeichnung **unmittelbar** davor steht, auf derselben Zeile (z. B.
     „Steuernummer“, „St.-Nr.“, „Ausweisnummer“, „Reisepass“, „Führerschein“, „(amtliches) Kennzeichen“, „amtl. Kz.“,
     „IP-Adresse“).
   - Damit ersetzt eine feste Nachbarschaft Presidios veränderliches Kontextfenster. Steht zwischen Bezeichnung und Wert
     etwas inzwischen Maskiertes, fehlt die Nachbarschaft, und der Restrisiko-Scan erzeugt keinen neuen Treffer.
     Ergebnis: konsistent und ohne neue Blockaden.
   - Kfz-Sonderregeln: kein Kleinbuchstabe. Nach dem bloßen Wort „Kennzeichen“ sind mindestens zwei Ziffern nötig
     (Review: „Kennzeichen VW T 6“, „EU A 3“).
   - Alles läuft im selben Presidio-Aufruf, ohne zweiten spaCy-Lauf.
2. **Profil-Links** (`_PROFILE_URL`):
   - Nur LinkedIn-/Xing-Profile, Facebook, Instagram, X/Twitter und TikTok, Platzhalter `[PROFIL_URL_nn]`.
   - Grenze vor dem Host. Das Review fand „dropbox.com/…“ und „fedex.com/…“ als Treffer über die Teilzeichenfolge
     „x.com/“.
   - Bewusst kein GitHub, keine allgemeinen URLs: Quellenangaben bleiben lesbar.
3. **Postfachanschrift** (`detectors._PO_BOX_PATTERN`): gilt wie Straße und Hausnummer. Voraussetzung sind mindestens
   zwei Zifferngruppen, mindestens fünf Ziffern oder eine direkt folgende PLZ mit Ort. Das Review fand sonst Fehlalarme
   wie „Im Postfach 3 neue Nachrichten“ und „Postfach 2025 vom …“ und ein freigelegtes „4711“ in
   „Postfach 0221 4711“.
4. **Griechische/kyrillische Versalnamen** (zweiter, rein additiver Versal-Durchgang):
   - Griechische und kyrillische Vokale gelten in der Vokalprüfung.
   - Ein Varianten-Treffer, dessen Nicht-PROPN-Wörter ausschließlich nicht-lateinisch sind, wird zugelassen, wenn er
     einen ursprünglichen NER-Treffer enthält.
   - Übernommen wird nur der Teil außerhalb dieses Treffers. Im installierten Build (Fall 13) war „ΓΙΑΝΝΗΣ“ als
     Organisation erkannt und „ΠΑΠΑΔΟΠΟΥΛΟΣ“ blieb im Klartext; das führte zu `3df99a2`.

**Bewusst nicht geändert:** L01 (Kleinschreibung). Jede Heuristik für „herr/frau + kleingeschriebenes Wort“ würde in
kleingeschriebenen Chat-Texten gewöhnliche Wörter maskieren. „sauer“ ist auch als Wort gebräuchlich. Bleibt Modellgrenze.

### 18.3 Messwerte

**Eingefrorene Testbasis** (identische Fälle; die Testbasis wurde nicht verändert):

| | `39b4900` | neu |
|---|---|---|
| PII im Klartext | 8/80 | **1/80** (L01) |
| Fehlalarme | 7/104 | 7/104 |
| Blockaden | 0 | 0 |
| Rückfragen | 1 | 1 |
| Laufzeit/Fall | 0,128 s | 0,127 s |

**Erweiterte, separat ausgewiesene Tests** (nicht Teil der Testbasis):
- Review-Sätze aus §17:
  - 68 zahlenreiche Sätze, 10 Blockadefälle und 240 Zufallsnummern: **0 Änderungen, 0 neue Blockaden**.
  - 43 Leckformate: 10 verbessert.
- Neues unabhängiges Review (205 Eingaben, alt `39b4900` gegen neu):
  - **0 neue Blockaden.**
  - Rund 100 zusätzlich geschützte Werte: Steuernummer, Ausweis, Pass, Führerschein und Kfz mit Bezeichnung, IPv4/IPv6,
    Postfach, Profil-Links, griechische/kyrillische Vollnamen.
  - Die dort gefundenen Fehlalarme wurden behoben (siehe 18.2).
- Akzeptierte Übermaskierung (gemessen):
  - „Ihr Kennzeichen RA BK 26“: Bezugszeichen und Kennzeichen sind gleich aufgebaut, im Zweifel wird maskiert.
  - Facebook-Firmenseite, „x.com/bundestag“.
  - „ΕΛΛΑΔΑ“, „ДОГОВОР АРЕНДЫ“ teilweise als Person.
  - „IP 1.2.3.4 … Kapitel 1.2.3.4“: Die vorhandene Wiederholungsersetzung maskiert auch die Kapitelnummer.

**Regressionsbenchmarks:** identisch zu `39b4900`.
- Versalien 0/36, 3/59; Versal-Absätze 26/171; Normzitate 3/180;
- Folgeerwähnungen 0/1288, 32/138, 3/22; Artikel 9/240; Briefe 6/220; Rollenobjekte 0;
- Blockaden 4 von 2017×2; Dokumentpfad 0/990; fremde Großbuchstaben 0/20; Briefblockaden 29/29.

**Tests:**
- 41 neue Tests in `tests/test_privacy_remaining_leaks.py`; die erste Fassung schlug auf `39b4900` in 10 Fällen fehl.
- Gesamtsuite: 3822 bestanden; die 4 Fehlschläge in `test_design_refresh` kommen von außerhalb dieser Arbeit gelöschten
  Logo-Dateien.

### 18.4 Offene Punkte

- **Bekannte Schwäche** (Review, auf `39b4900` für PERSON/ADRESSE identisch): Ein Dokument, das wörtlich einen
  Platzhalter wie „[KENNUNG_01]“ enthält, wurde mit unbekanntem Präfix blockiert. Jetzt wird es wie bei `[PERSON_01]`
  zugelassen. Der Platzhalter wird in der lokalen Antwort auf den echten Wert zurückgeführt. Es geht kein Klartext
  hinaus, die lokale Rückführung kann aber irreführen.
- **Weiterhin unerkannt** (Review gemessen):
  - Wert nicht unmittelbar nach der Bezeichnung („Kennzeichen lautet …“, Klammern/Anführungszeichen, Bezeichnung in der
    Tabellenkopfzeile);
  - Bezeichnungen „Kz.“, „Kennz.“, „FS-Nr.“, „Postf.“, „PF“;
  - Steuernummern ohne Schrägstrich (10/11 Stellen) und mit Leerzeichen;
  - kleingeschriebene Kennzeichen;
  - weitere Netzwerke (threads.net, bsky.app);
  - Namen in Kleinschreibung (L01).

### 18.5 Installierter Build `3df99a2`

Bündel geprüft (`non_latin_extension`, `_weak_plate`, `_PROFILE_URL`, `_PO_BOX_PATTERN` enthalten), Installation
Exit 0, Health 200. Echter Upload, Payload am lokal umgeleiteten Endpunkt.

**Fall 13, neu (DOCX):** Postfach-Briefkopf, griechischer Versalname, Steuernummer, Ausweisnummer, amtliches
Kennzeichen, IP-Adresse, LinkedIn-Profil; dazu ohne Bezeichnung „KL AB 12“, „VW T 6“, „Version 2.4.1.0“,
13-stellige Lieferscheinnummer.
- Auf Build `49c3687`: 7 von 8 Werten geschützt, „ΠΑΠΑΔΟΠΟΥΛΟΣ“ im Klartext; das führte zu `3df99a2`.
- Auf `3df99a2`: **PASS**, alle 8 Werte geschützt, alle 5 lesbar zu haltenden Angaben lesbar, keine Blockade.
- Screenshot: lokale Antwort korrekt re-personalisiert.
- Die erste Auswertung auf `49c3687` zeigte fälschlich „kein Cloud-Aufruf“: Der neu gestartete Capture-Server
  nummerierte ab `payload_001.json` und überschrieb eine ältere Datei. Die Payload wurde direkt geprüft; alte Payloads
  werden seither vor jedem Neustart archiviert.

**Fälle 1–11:** PASS.

**Fall 12:** Alle Schutzwerte maskiert, keine Blockade. Zwei Lesbarkeitsprüfungen scheitern („Lkw-Flotte“ als Ort,
Quellen-URL als Organisation durch die spaCy-NER). Das ist vorbestehend und auf `1c9d91c` identisch (§17).

**Nachweisgrenzen:** Geprüft wird die vom Lexono-Client gesendete Payload, nicht der TLS-Verkehr. Screenshots belegen
nur die Darstellung.

## 19. Privacy-Hardening: Platzhalter-Integrität, weitere Kennnummer-Formate, Pilotbereitschaft (13.10.2026, `cef580c`)

**Ausgangsstand:** `7cd8145`, Build `3df99a2` installiert.

### 19.1 Platzhalter-Integrität

**Befund** (gemessen auf `7cd8145`, Rückführung der unveränderten Payload):
- Neun Fälle mit wörtlich im Dokument stehenden Platzhaltern: **0 von 9 exakt zurückgeführt**.
  - Sieben Fälle setzten einen **fremden Wert** an die Stelle des Originaltexts. Beispiel: Ein wörtliches „[PERSON_01]“
    und der Name „Karl Brenner“ bekamen denselben Platzhalter. Die Rückführung schrieb zweimal „Karl Brenner“.
    Ebenso bei `[ADRESSE_01]`, `[KENNUNG_01]`, zwei Dokumenten und einem Lexono-Export.
  - Zwei Fälle wurden blockiert („[PERSON_07]“ und „[PERSON_1]“ ohne Gegenstück).
- Das unabhängige Review (104 Fälle) maß auf `7cd8145` in 76 Kollisionsfällen 12 fremde Werte und 31 Blockaden.
- Fehlerhafte Zuordnungen wurden still „geraten“:
  - Bei einem Duplikat mit anderem Wert wurde der erste Wert eingesetzt.
  - Bei einer Verkettung wurde ein Wert zweimal ersetzt.
  - Ein nacktes Token „PERSON_01“ aus dem Dokument wurde zu „[Angabe einsetzen]“.

**Ursachen:**
1. Die Nummerierung erzeugter Platzhalter berücksichtigte im Eingangstext vorhandene gleichlautende Zeichenfolgen nicht.
2. `reconstruct` ersetzte mit aufeinanderfolgenden `str.replace`.
3. Reste wurden nach der Rückführung neutralisiert, also auch zurückgeführter Originaltext.

**Änderung** (`app/privacy/pseudonymizer.py`, `gateway.reconstruct_response`, `security_check`):
1. **Wörtliche Platzhalter maskieren.**
   - Jede platzhalterähnliche Zeichenfolge im Eingangstext wird selbst maskiert, Kategorie `platzhaltertext`,
     Platzhalter `[TEXTMARKE_nn]`, Originalwert ist der exakte Text. Erfasst wird, was das Final Payload Gate als Token
     wertet, sowie nackte Tokennamen der bekannten Präfixe.
   - Danach stammt jeder Platzhalter der Payload aus der Erzeugung, eine Verwechslung ist ausgeschlossen.
   - Liegt ein Detektor-Treffer innerhalb eines wörtlichen Platzhalters, hat der Platzhalter Vorrang.
   - Ragt ein Treffer darüber hinaus (Platzhalter mitten in einer IBAN), wird die Vereinigung unter der Kategorie des
     Treffers maskiert.
   - Schreibvarianten werden exakt unterschieden.
   - Die Leck-Prüfung überspringt diese Kategorie: Sie enthält keinen personenbezogenen Wert.
   - Der Restrisiko-Scan bleibt unverändert, weil die Maskierung nur in `pseudonymize` geschieht.
2. **Rückführung in einem Durchgang.**
   - Bekannte Platzhalter werden zuerst durch Schutzzeichen ersetzt (Private-Use U+E000/U+E001).
   - Danach werden nur Reste des Antworttexts zu „[Angabe einsetzen]“, dann werden die Werte eingesetzt.
   - Keine Verkettung; zurückgeführter Originaltext wird nie neutralisiert.
3. **Fehlerhafte Zuordnungen nicht raten.** Ein Platzhalter mit widersprüchlichen Werten, ungültiger Form oder leerem
   Wert wird nicht zurückgeführt. Er erscheint als „[Angabe einsetzen]“ statt mit einem möglicherweise fremden Wert.

**Messung:**
- 9 Kollisionsfälle: 0/9 → **9/9 exakt**.
- Review-Satz (76 Kollisionsfälle):
  - fremde Werte: 12 → **0**;
  - Blockaden: 31 → **0**;
  - exakte Rückführung in allen Feldern (Sachverhalt, Anmerkungen, Argumente, Verlauf) und bei mehreren Dokumenten.
- Fehlerhafte Zuordnungen: 13 Szenarien × 3 Antworten, kein fremder Wert eingesetzt.

### 19.2 Weitere Kennnummer-Formate

Erweiterte Leckfälle (separat, 18 Werte): **10/18 → 1/18** im Klartext, 0 Blockaden. Das Prinzip aus §18 bleibt: Treffer
nur mit unmittelbar vorangestellter Bezeichnung, nun auch mit „ist“/„lautet“ dazwischen.

Ergänzt wurde:
- zusätzliche Presidio-`PatternRecognizer`-Formate unter denselben Entitäten (Grundscore 0,1, ohne Bezeichnung wirkungslos):
  - Steuernummer mit 10 bis 13 Ziffern oder mit Leerzeichen;
  - Kennzeichen „HH AB1234“;
  - „HRB 123 456“, kontextunabhängig wie das offizielle Muster;
- Bezeichnungen „Kennz.“, „Kfz-Kz.“, „FS-Nr.“;
- „Postf.“;
- Profil-Links threads.net/@ und bsky.app/profile/;
- kleingeschriebene Kennzeichen.

**Fehlalarme durch das Review gefunden und behoben:**
- „Das Kennzeichen ist um 18 Uhr gestohlen“, „ist zu 80 Prozent“, „Fahrzeugkennzeichen und Km 45“ wurden maskiert.
  Jetzt gilt: Ist die erste Buchstabengruppe ein gewöhnliches Wort, wird sie verworfen. Zweibuchstaben-Wörter gelten nur
  in Kleinschreibung als gewöhnlich, weil „AB“, „UM“, „IN“ und „AM“ echte Unterscheidungszeichen sind.
- Die erste Fassung der Vereinigungsregel machte einen wörtlichen Platzhalter zu `[PERSON_02]`, wenn die NER „Person_01“
  darin fand. Behoben: Treffer innerhalb eines wörtlichen Platzhalters werden verworfen.

### 19.3 Messwerte und Validierung

**Eingefrorene Testbasis** (unverändert, Inhalts-SHA-256 `2de13db5…`):
- FN 1/80, FP 7/104, 0 Blockaden, 1 Rückfrage — identisch zu `7cd8145`.
- Laufzeit 0,136 s pro Fall (Parallellauf).

**Erweiterte Sätze:**
- Review-Sätze aus §17: Fehlalarm-, Blockade- und Nummernreihen unverändert; 6 Leckformate verbessert.
- Review-Satz §18 (205 Eingaben): 22 Änderungen, alle beabsichtigt, 0 neue Blockaden.
  - 14 zusätzlich geschützte Werte;
  - 5 Kollisionsfälle sauber getrennt;
  - 3 vorher blockierte Eingaben mit wörtlichen Platzhaltern werden verarbeitet.

**Regressionsbenchmarks:** identisch zu `3df99a2`.

**Tests:**
- 42 neue Tests:
  - `tests/test_privacy_placeholder_integrity.py`: 15 von 16 der ersten Fassung schlagen auf `7cd8145` fehl.
  - `tests/test_privacy_identifier_formats.py`: 12 der ersten Fassung schlagen auf `7cd8145` fehl.
- Gesamtsuite: 3864 bestanden; die 4 Fehlschläge in `test_design_refresh` kommen von außerhalb dieser Arbeit gelöschten
  Logo-Dateien.

**Installierter Build `cef580c`.** Bündel geprüft (`_literal_placeholder_spans`, `_merge_literal_spans`,
`_plate_has_common_word` enthalten), Installation Exit 0, Health 200. Echter Upload, Payload am lokal umgeleiteten
Endpunkt.

- **Fall 14, neu (DOCX):** wörtliches „[PERSON_01]“ neben „Herr Karl Brenner“, Formularfeld „PERSON_01“,
  wörtliches „[ADRESSE_01]“ neben einer Anschrift, Steuernummer ohne Schrägstrich, „Kennz.“-Kennzeichen,
  Unfallsatz „Das Kennzeichen ist um 18 Uhr gestohlen worden“.
  - Payload: „Muster: [TEXTMARKE_01] und Herr [PERSON_01] … Formularfeld [TEXTMARKE_02] … [TEXTMARKE_03] steht neben
    [ADRESSE_01]. Steuernummer [KENNUNG_01] … [KENNUNG_02]. Das Kennzeichen ist um 18 Uhr gestohlen worden.“
  - Die lokale Antwort im Screenshot zeigt die wörtlichen Platzhalter unverändert und die echten Werte an der richtigen
    Stelle: „Muster: [PERSON_01] und Herr Karl Brenner …“.
  - Ergebnis: **PASS**.
- **Fälle 1–11 und 13:** PASS.
- **Fall 12:** wie in §17 (zwei vorbestehende Lesbarkeitsbefunde, alle Schutzwerte maskiert).

### 19.4 Pilotbereitschaft (Nachweise statt Pauschalurteil)

**Belegt in diesem Zyklus:**
- Keine Verwechslung wörtlicher und erzeugter Platzhalter mehr: 0 fremde Werte in 85 Kollisionsfällen.
- Keine neuen Blockaden in allen Vergleichssätzen (Testbasis, Review-Sätze §17/§18, Review 104 Fälle).
- Eingefrorene Testbasis 1/80 PII im Klartext bei 7/104 Fehlalarmen.
- Installierter Build: 14 Upload-Fälle mit Payload-Prüfung.
- P0-05 (Presidio/Pseudonymisierung) ist damit weiter gestützt.

**Nicht geprüft in diesem Zyklus (NOT VERIFIED):**
- echter Anthropic-Aufruf (P0-07; geprüft wurde nur die vom Lexono-Client gesendete Payload am lokalen Endpunkt);
- TLS-Netzwerkverkehr;
- OCR-Pfad (in der Installation deaktiviert);
- Gateway-Pfad (P0-08, laut Masterdokument zurückgestellt).

**Gemessene Restrisiken:**
- **Mittel:**
  - Kennnummern ohne unmittelbar vorangestellte Bezeichnung oder in abweichendem Format bleiben im Klartext, z. B.
    „Kennzeichen des Gegners: D-XY 123“, „Der PKW HH-AB 1234“;
  - Sozialversicherungsnummer mit Leerzeichen;
  - Ausweisnummer mit ungültiger Prüfziffer;
  - Konto- und Mitgliedsnummern ohne Bezeichnung.
- **Niedrig:**
  - Namen in Kleinschreibung (Modellgrenze);
  - ein wörtlicher Platzhalter mitten in einer sonst nicht erkannten Rufnummer („030 [TELEFON_01] 1234567“; auf
    `7cd8145` blockiert, jetzt Rest sichtbar, weil die Nummer auch ohne Platzhalter von keinem Detektor erkannt wird);
  - Homoglyph-Platzhalter (Vollbreite, kyrillisches „Е“) werden nicht als Platzhalter erkannt. Sie gehen unverändert
    hinaus und werden nicht zurückgeführt.
- **Vorbestehend:**
  - „HRB-Verfahren und Az. 12 O 345/26 …“ wird vom Restrisiko-Scan blockiert („person“);
  - die spaCy-NER übermaskiert einzelne Fachwörter („Lkw-Flotte“, „a 7“).

**Empfehlung:** Für einen begleiteten Pilotbetrieb mit synthetischen bzw. freigegebenen Testakten ist der
Pseudonymisierungspfad nach den obigen Messungen geeignet. Vor Echtdaten sind folgende Schritte nötig:
1. P0-07 mit echtem Aufruf erneut belegen.
2. Die Kennnummer-Restrisiken fachlich bewerten. Dürfen unbezeichnete Kfz- und Sozialversicherungsnummern im
   Klartext bleiben, oder ist eine Rückfrage gewünscht? Das ist eine fachliche Entscheidung.
3. Anwender anweisen, die lokale Vorschau der erkannten Werte vor dem Senden zu nutzen.

Eine vollständige Leckfreiheit ist nicht belegt.

## 20. End-to-End-Sicherheitsprüfung der Pre-Cloud-Grenze (13.10.2026, `0f4b787`)

**Ausgangsstand:** `f035b2e` = origin/main; installierter Build `cef580c`, codegleich. Ein `ROADMAP.md` existiert im
Repository nicht; maßgeblich sind `TODO.md` und `LEXONO_MASTER_PRODUCT.md`.

### 20.1 Datenfluss und ausgehende Verbindungen (Code gelesen)

**Pfad:** Upload (`chat_router`) → Extraktion (`documents/extraction.py`: PyMuPDF, python-docx; OCR standardmäßig aus) →
`_normalize_document_text` → `DraftingService` → `ClaudePrivacyGateway.prepare_request`:
- Bereinigung;
- Pseudonymisierung mit wörtlichen Platzhaltern als `[TEXTMARKE]`;
- Restrisiko-Scan;
- Final Payload Gate;
- HMAC-Siegel.

Danach folgt der Cloud-Adapter. **Jeder** der vier Cloud-Adapter ruft unmittelbar vor dem Versand `require_gateway_seal`
auf:
- Schreiben: Anthropic direkt und Gateway-Relay;
- Prüfung: Anthropic direkt und Gateway-Relay.

**Weitere ausgehende Verbindungen, ohne Nutzertext:**
- Systemstatus: `models.list`;
- gesetze-im-internet.de: nur das Gesetzeskürzel in der URL;
- Updater: Manifest.

Die lokale KI (Ollama) erhält nur die bereits pseudonymisierte Payload und die pseudonymisierte Antwort.

**Konfigurationsrisiko (nicht geändert):** `OLLAMA_BASE_URL` ist in den Einstellungen frei wählbar, auch für entfernte
Hosts. Dorthin ginge nur der bereits pseudonymisierte Text, also kein Umgehen der Grenze. Ob nicht-lokale Adressen
zulässig sein sollen, ist eine fachliche Entscheidung.

### 20.2 Nachweis mit kontrolliertem Transport (`tests/test_privacy_cloud_boundary_e2e.py`, 9 Tests)

Echter Produktpfad: synthetisches PDF über `/dashboard/chat/send` → Gateway → `AnthropicClaudeWritingProvider` mit dem
**echten Anthropic-SDK** (anthropic 1.5, Transport `httpx2.MockTransport`). Das SDK serialisiert den vollständigen
HTTP-Request, es verlässt nichts den Rechner.

Belegt:
- Kein geschützter Wert im Request (Body, Header, Systemanweisung). Geprüft wurden 10 synthetische Werte: Name, Anschrift,
  IBAN, E-Mail, Telefon, Steuernummer, Kennzeichen, Zeugin.
- Nur erlaubte Felder (`model`, `max_tokens`, `thinking`, `system`, `messages`; kein `metadata`), Ziel
  `https://api.anthropic.com/v1/messages`.
- Fachinhalt lesbar (Betrag, Normzitat); wörtlicher Platzhalter als `[TEXTMARKE]`.
- Lokale Rückführung korrekt: „Karl Brenner“ ist zurück, der wörtliche „[PERSON_01]“ bleibt wörtlich.
- Fehler in der Pseudonymisierung (NER-Ausfall): **kein Request**.
- Transportfehler (Timeout, Verbindungsfehler): genau der versiegelte Versuch, kein Ausweichpfad.
- Unversiegelte, nach dem Siegel veränderte oder als Unterklasse gefälschte Payload: `UnsealedPayloadError` in `write`
  und `write_stream`, **0 Requests**.
- Streaming-Pfad: Server-Sent-Events, `stream: true`, gleiche Garantie.
- Erfundene oder veränderte Platzhalter in der Antwort werden nie zu einem echten Wert.

**Abgrenzung:** Das belegt die SDK-Payload. Ein echter Anthropic-Aufruf, die TLS-Übertragung und der Gateway-Pfad sind
**nicht** belegt.

### 20.3 Grenzfälle: Befund, Ursache, Änderung

Messung auf einem erweiterten, getrennt ausgewiesenen Satz, vorher → nachher:

| Fall | vorher | nachher | Ursache / Änderung |
|---|---|---|---|
| Lookalike-Platzhalter: kyrillisches „Е“, griechisches „Ο“, Vollbreite; im Review auch Kombinationszeichen, mathematisch fett, „O“ statt Null, Kapitälchen | **Fremdwert möglich**, wenn das Modell den Platzhalter normalisiert (2 von 4 bzw. 6 Formen) | 0 | Erkennung wörtlicher Platzhalter auf längengleicher ASCII-Fassung, dazu lockere Klammerform → `[TEXTMARKE]` |
| „Tel. 030 [TELEFON_01] 1234567“ | Ziffern im Klartext | maskiert, exakt zurückgeführt | angrenzende Zifferngruppen in die Textmarke (Fenster 200 Zeichen, auch „/“) |
| „Der PKW HH-AB 1234“ | Leck | maskiert | Fahrzeugwort als Bezeichnung, mit Zwei-Ziffern-Regel wie bei „Kennzeichen“ |
| SV/RV-Nummer mit Leerzeichen, Kontonummer, Mitgliedsnummer | Leck | maskiert | bezeichnete Formate (nur mit unmittelbar vorangestellter Bezeichnung) |
| „Kennzeichen des Gegners: D-XY 123“ | Leck | **Leck (offen)** | Bezeichnung nicht unmittelbar davor |

**Gesamt:** Lecks 8 → 1, Lookalike-Kollisionen 2 → 0, Fehlalarme 0 → 0, Blockaden 0 → 0.

**Vom unabhängigen Review gefunden und behoben:**
- Neue Blockaden, weil die NER die bloße Bezeichnung neben dem neuen Platzhalter für eine Person oder einen Ort hielt
  („SV-Nr.“, „Kto.-Nr.“, „Rentenversicherungsnummer“, „Sparkasse“). Behoben strukturell: Ein NER-Treffer, der **ganz**
  aus einer Bezeichnung nach `_ID_LABEL_BEFORE` besteht, wird verworfen, ohne eigene Wortliste. Dazu kommt „sparkasse“
  in `_NEVER_ENTITY_WORDS` (32 Einträge, Wächtertest ≤ 48).
- Weitere Lookalike-Formen.
- Abgeschnittene lange Ziffernfolgen.
- Siegel-Fälschung per Unterklasse: Prüfung jetzt auf den exakten Typ, Digest über die Basisklasse. Vorher erforderte
  das prozessinternen Schadcode.

### 20.4 Messwerte und Validierung

**Eingefrorene Testbasis** (unverändert, Inhalts-SHA-256 `2de13db5…`):

| | `f035b2e` | `0f4b787` |
|---|---|---|
| PII im Klartext | 1/80 | 1/80 |
| Fehlalarme | 7/104 | **6/104** („Sparkasse“ bleibt lesbar) |
| Blockaden | 0 | 0 |
| Rückfragen | 1 | 1 |
| Laufzeit | ≈ 0,13 s/Fall | ≈ 0,13 s/Fall |

**Erweiterter Grenzfallsatz** (separat, siehe 20.3): 8 Lecks → 1, 0 → 0 verlorene Fachinhalte, 0 → 0 Blockaden,
2 → 0 Lookalike-Kollisionen.

**Review-Satz** (105 Eingaben, unabhängiges Review alt gegen neu): 0 neue Blockaden, 0 Lecks; die Review-Befunde M1, M2,
L1, L2 und L5 sind behoben.

**Review-Sätze §17/§18:** 0 neue Blockaden; Änderungen nur in Leckformaten (geschützt) und Lesbarkeit von
Bezeichnungen.

**Regressionsbenchmarks:** identisch zu `cef580c`.

**Tests:**
- 37 neue Tests in `tests/test_privacy_cloud_boundary_e2e.py` (9) und `tests/test_privacy_boundary_edge_cases.py` (28).
  Die 8 E2E-Tests der ersten Fassung bestehen auch auf `f035b2e`: Die Grenze hielt dort bereits. 9 Grenzfalltests
  schlagen auf `f035b2e` fehl.
- Gesamtsuite: **3901 bestanden**; die 4 Fehlschläge in `test_design_refresh` betreffen außerhalb dieser Arbeit
  gelöschte Logo-Dateien.
- Zwischenzeitlich schlug der Wächtertest `test_exception_list_stays_small_and_documented` fehl (Ausnahmeliste auf 63
  Einträge gewachsen). Die Grenze wurde nicht angehoben; die Lösung wurde auf die strukturelle Ableitung umgestellt.

**Installierter Build `0f4b787`.** Bündel geprüft (`_CONFUSABLE_TO_ASCII`, `_PLACEHOLDER_LOOSE`, `_is_identifier_label`),
Installation Exit 0, Health 200. Die installierte Anwendung sendet über das echte Anthropic-SDK an den lokal umgeleiteten
Endpunkt (`ANTHROPIC_BASE_URL`).
- **Fall 15, neu (DOCX):** Lookalike-Platzhalter, Rufnummer mit eingebettetem Platzhalter, „Der PKW …“, SV-Nummer mit
  Leerzeichen, Kontonummer bei der Sparkasse, Mitgliedsnummer.
  - Payload: „Feld: [TEXTMARKE_01] und Herr [PERSON_01] … Tel. [TEXTMARKE_02] … Der PKW [KENNUNG_01] stand um 18 Uhr …
    SV-Nr. [KENNUNG_02]; Kontonummer [KENNUNG_03] bei der Sparkasse; Mitgliedsnummer [KENNUNG_04]. Forderung
    1.200,00 EUR nach § 286 BGB.“
  - Screenshot: Die lokale Antwort zeigt den Lookalike wörtlich und Name und Rufnummer exakt zurückgeführt.
  - Ergebnis: **PASS**.
- **Fälle 1–11, 13, 14:** PASS.
- **Fall 12:** wie in §17 (zwei vorbestehende Lesbarkeitsbefunde, alle Schutzwerte maskiert).

### 20.5 Bewertung

| Prüfpunkt | Status | Beleg |
|---|---|---|
| SDK-Payload und Pre-Cloud-Gate | **PASS** | E2E mit echtem SDK und kontrolliertem Transport; installierter Build mit Payload-Prüfung (15 Fälle) |
| Fail-Closed | **PASS** | NER-Ausfall ohne Request; unversiegelt/verändert/Unterklasse ohne Request; Transportfehler ohne Ausweichpfad |
| Anthropic-Aufrufpfad (echter API-Aufruf) | **NICHT VERIFIZIERT** | ohne ausdrückliche Autorisierung kein echter Aufruf |
| TLS und Gateway | **NICHT VERIFIZIERT** | nur lokaler Endpunkt bzw. MockTransport; Gateway (P0-08) zurückgestellt |
| Installierter Build | **PASS** | `0f4b787`, Health 200, 14/15 Fälle PASS (Fall 12: vorbestehende Lesbarkeit) |
| Restlecks / Fehlalarme | **teilweise** | Testbasis 1/80 und 6/104; offene Fälle siehe unten |

**Offene Risiken** (gemessen):
- **Mittel:**
  - Kennnummern, deren Bezeichnung nicht unmittelbar davor steht („Kennzeichen des Gegners: D-XY 123“, „Krad M-AB 77 von
    …“ teilweise);
  - Namen in Kleinschreibung (Modellgrenze).
- **Niedrig:**
  - Übermaskierung von Fahrzeugmodellen nach einem Fahrzeugwort („Lkw DAF XF 480“);
  - Klammerformeln wie „[ΔT_2]“ und Ziffern um wörtliche Platzhalter werden als Textmarke maskiert (kosmetisch, exakte
    Rückführung);
  - frei wählbare `OLLAMA_BASE_URL` (nur pseudonymisierter Text).
- **Vorbestehend:** „HRB-Verfahren und Az. …“ wird blockiert; spaCy übermaskiert einzelne Fachwörter.

**Pilotentscheidung:** Geeignet für einen **begrenzten, begleiteten Pilotbetrieb** mit synthetischen bzw. ausdrücklich
freigegebenen Testakten. Gründe:
- Die Pre-Cloud-Grenze ist auf SDK-Ebene durchgängig belegt: kein geschützter Testwert im Request, Fail-Closed bei Fehlern
  und Manipulation, exakte Rückführung ohne Fremdwerte.
- Der installierte Build verhält sich gleich.

**Vor Echtdaten erforderlich:**
1. Ein autorisierter echter Anthropic-Aufruf mit synthetischen Daten (P0-07), inklusive Prüfung, dass die
   TLS-Verbindung zu api.anthropic.com geht.
2. Eine fachliche Entscheidung zu nicht unmittelbar bezeichneten Kennnummern und zu `OLLAMA_BASE_URL`.
3. Eine Anwenderanweisung zur lokalen Vorschau.

Eine vollständige Leckfreiheit ist nicht belegt.



## 21. Offene Freigabepunkte: Anthropic-Aufrufpfad, unbeschriftete Kennnummern, KI-Endpunkte (13.10.2026, `cb8b02c`, `95f837c`)

Ausgangsstand `065b238` (§20). Nur synthetische Daten. Kein echter Anthropic-Aufruf: eine ausdrückliche Autorisierung lag
nicht vor.

### 21.1 Anthropic-Aufrufpfad und Netzwerkziel

**Befund (gemessen):** Das Anthropic-SDK übernimmt ohne `base_url`-Argument still die Umgebungsvariable
`ANTHROPIC_BASE_URL`. War sie gesetzt (IT-Skript, Schadsoftware, Testrest), gingen pseudonymisierte Anfragen **und** der
API-Schlüssel (`x-api-key`) an diesen Host, auch unverschlüsselt über `http://`. Der Regressionstest
`test_no_ai_gateway_proxy` prüfte nur „kein `base_url`-Argument“ und hätte diese Umleitung nicht erkannt.

**Änderung (`app/ai_providers/endpoint_policy.py`):**
- Zulässig sind nur `https://api.anthropic.com` und ein Prüfendpunkt auf diesem Rechner. Letzterer gilt nur zusammen mit
  der ausdrücklichen QA-Freigabe `LEXONO_QA_LOOPBACK_CLOUD_ENDPOINT=1`.
- Jedes andere Ziel ist ein Konfigurationsfehler: Die Factory meldet „nicht konfiguriert“, es gibt keinen Versand.
- Das Ziel wird in Schreib-Provider, Review-Provider, Erreichbarkeitsprüfung (`system_health`) und Statusanzeige
  (`cloud_health`, vorher sprach sie den fremden Host an und meldete „online“) ausdrücklich gesetzt.
- Zusätzlich serverseitig im Lexono-Relay (`gateway/relay.py`).
- `test_no_ai_gateway_proxy` wurde bewusst und sichtbar verschärft: Er prüft jetzt, dass trotz gesetzter Umgebungsvariable
  die offizielle Adresse verwendet wird.

**TLS (lokal belegt, nicht live):** Der vom SDK erzeugte Client verwendet `CERT_REQUIRED`, Hostnamenprüfung und
mindestens TLS 1.2. Getestet ist das in `test_factory_pins_the_official_https_endpoint_with_certificate_verification`.
Eine echte TLS-Verbindung zu api.anthropic.com wurde nicht hergestellt.

**Opt-in-Nachweis:** `scripts/verify_anthropic_live.py --authorized-live-call` mit `ANTHROPIC_API_KEY`.
- Ohne beides bricht das Skript ab; das ist geprüft.
- Mit Freigabe sendet es nur einen eingebauten synthetischen Text über Gateway, Factory und SDK.
- Es gibt Zertifikat, Aussteller und TLS-Version aus und prüft die Payload auf Klartextwerte.

**Grenze:** Wer die Umgebung des Benutzers setzen kann, kann auch die QA-Freigabe setzen. Die Freigabe verhindert
versehentliche und Ein-Variablen-Umleitungen, aber keinen gezielten lokalen Angreifer. Ein solcher Prozess könnte den
Schlüssel ohnehin aus der Konfiguration lesen.

### 21.2 Lokale KI-Endpunkte (Ollama)

**Befund:**
- `OLLAMA_BASE_URL` war frei auf einen fremden Rechner setzbar, unverschlüsselt.
- Review: Unabhängig davon übernahm httpx `HTTP_PROXY`/`ALL_PROXY` und unter Windows den System-Proxy, auch für
  127.0.0.1. Der Text für die lokale KI ging dann trotz lokaler Adresse an den Proxy-Rechner.

**Änderung:**
- Standardmäßig ist nur dieser Rechner zulässig (`localhost`, 127.0.0.0/8, `::1`).
- Ein anderer Rechner braucht `LOCAL_AI_ALLOW_REMOTE_ENDPOINT=true`. Die Einstellungsseite lehnt eine fremde Adresse ohne
  diese Freigabe mit einer verständlichen Meldung ab.
- Ohne Freigabe entsteht keine Verbindung: Health-Status „nicht erreichbar“ mit Begründung, Chat blockiert nach §65,
  kein stiller Cloud-Ersatz.
- Alle Ollama-Aufrufe ignorieren Proxy-Einstellungen (`trust_env=False`). Der Download des Ollama-Installers aus dem
  Internet nutzt den System-Proxy weiterhin.

**Kompatibilität:** Eine bestehende Installation mit einem Ollama auf einem anderen Rechner meldet nach dem Update sichtbar
„nicht ausdrücklich freigegeben“, bis die Freigabe gesetzt ist. Das gehört in die Release-Notiz.

### 21.3 Unbeschriftete Kennnummern

**Befund (§20):** „Kennzeichen des Gegners: D-XY 123“ ging im Klartext hinaus. Die Bezeichnung steht nicht unmittelbar vor
dem Wert, und `presidio_ner` maskiert nur bezeichnete Kennnummern.

**Änderung (`app/privacy/review.py`):** Eine kennzeichenförmige Zeichenfolge wird zum **unsicheren Kandidaten** der
bestehenden Rückfrage-Stufe. Das ist keine neue globale Maskierungsregel.
- Im Modus „ask“ (Chat-Standard) fragt Lexono vor dem Versand.
- Im Modus „mask“ wird die Folge vorsichtshalber als `[KENNUNG_nn]` maskiert.
- Entscheidet der Anwalt „nicht personenbezogen“, geht die Folge unverändert hinaus.
- Die Kandidaten sind nicht durch die Höchstzahl je Runde begrenzt, sonst ginge ein Kennzeichen hinter vielen
  Namenskandidaten ungefragt hinaus.

Erfasste Formen:
- Bindestrich- und Gedankenstrichform („D-XY 123“, „D–XY 123“);
- mit und ohne Leerzeichen vor den Ziffern;
- Zeilenumbruch vor den Ziffern (PDF), auch Windows-CRLF (Review: vorher Klartext);
- Wert in eckigen Klammern.

**Speicherung der Entscheidung (`app/privacy/decisions.py`, Befund des installierten Builds):**
- Der Entscheidungsweg nahm bisher nur ein rein alphabetisches Wort an. Das ist eine Audit-Regel vom 12.10.2026 gegen
  eingeschleuste Mehrwort-Begriffe.
- Die Antwort zu „D-XY 123“ wurde deshalb verworfen, und Lexono fragte im Chat endlos erneut. Es ging nichts hinaus
  (fail-closed), der Chat war für solche Dokumente aber unbenutzbar.
- Die Unit-Tests hatten das nicht gezeigt, weil sie Entscheidungen direkt übergeben. Erst der UI-Durchlauf im
  installierten Build deckte es auf.
- Jetzt gilt zusätzlich genau die einzeilige Kennzeichenform. Sie enthält nur Buchstaben, Bindestriche, Leerzeichen und
  Ziffern und wird immer als `kennung` maskiert.
- Ein Zeilenumbruch im Kennzeichen wird im Begriff zu einem Leerzeichen und im Text trotzdem gefunden.
- Ein Regressionstest läuft über den echten Speicherweg (`record_decision` → `load_decisions` → Gateway).

Für die reine Leerzeichenform („D XY 123“) gilt: Sie zählt nur mit mindestens zwei Ziffern, Fahrzeugbezug in der Nähe,
ohne „Zeichen:/Az./Nr.“ davor und ohne Funktionswort oder Marke als erste Gruppe. Gemessen ergab sie sonst 12 Fehlalarme
im Fehlalarm-Satz, z. B. „VW T 5“, „DER B 51“, „Ihr Zeichen: SB KL 123“.

**Ausnahmen, eng gefasst:**
- Gesetzes- und Normenkürzel (ISO, VDE, VOB, SGB, BGB, ZPO, HGB, IEC);
- römische Gliederungs- und Senatsnummern I–XX ohne V;
- das Paar „DIN EN“;
- Zeitschriften-Reporte „…-RR 2020, …“.

Das Review fand zweimal echte Unterscheidungszeichen in den Ausnahmelisten: EN, GG, EU, UN, V, AM, AN, KM, RA, SB und DEM.
Deren Kennzeichen gingen dadurch wieder im Klartext hinaus. Alle sind entfernt, und ein Test sichert sie ab.

**Messung:**

| Messung | Ergebnis |
|---|---|
| 916 vorhandene synthetische Texte | 8 Treffer, alle echte Kennzeichen; 0 Treffer im Zitat-Negativsatz (30 Fälle) |
| Presidio-Audit (eingefrorener Korpus) | unverändert: C = FN 1/80, FP 6/104, 0 Blockaden |
| Batterie | unverändert bis auf Laufzeiten |
| Review-Satz 8 (Modus „mask“) | 5 vorher im Klartext gesendete Kennzeichen jetzt maskiert (Tabelle, Klammern, Anführungszeichen, Spiegelstrich); 1 vorher blockierter Fall jetzt maskiert statt blockiert, mit exakter Rückführung; 1 Übermaskierung („Kennzeichen der Gruppe ABC D 12“) |
| Review-Satz 7 | 3 Spediteur-Kennzeichen „KL AB 12“ jetzt maskiert; „Bezug SB XY 12“ wird maskiert (mehrdeutig) |

**Bekannte Restlücken, nicht maskiert:**
- Leerzeichenform ohne Fahrzeugbezug in der Nähe („Gegner: B AB 1234“);
- Leerzeichenform mit nur einer Ziffer;
- „D XY-123“;
- Zeilenumbruch zwischen den Buchstabengruppen bzw. Leerzeile vor den Ziffern;
- Kleinschreibung.

**Teilmaskierung:** Je nach Kontext erkennt die NER-Stufe schon den Ortsteil („D-XY“ → `[ORT_01]`). Dann bleiben nur
die Ziffern lesbar („[ORT_01] 123“), und die Rückfrage entfällt. Das vollständige Kennzeichen geht nicht hinaus, die
Maskierung ist aber nicht einheitlich `[KENNUNG]`.

**Bekannte Fehlalarme**, die eine Rückfrage oder Übermaskierung kosten, aber kein Leck sind: „Anlage B AB 12“ und
„Raum B E 12“ mit Fahrzeugbezug in der Nähe sowie Bindestrich-Codes wie „K-A 3“, „VV-RV 3100“, „IT-SA 2024“.

**Fachliche Entscheidungen (offen, nicht eigenmächtig festgelegt):**
1. Ob kennzeichenförmige Folgen im Modus „mask“ maskiert oder ob dort ebenfalls gefragt werden soll.
2. Wie andere unbeschriftete Nummern behandelt werden: Kontonummer ohne Bezeichnung, Vertrags- und Kundennummern im
   Fließtext. Sie werden derzeit nicht erkannt, weil eine Zifferngruppe allein nicht von Beträgen, Aktenzeichen und
   Fundstellen unterscheidbar ist; eine globale Ziffernregel wäre die abgelehnte aggressive Regel.
3. Ob die Leerzeichenform auch ohne Fahrzeugbezug gefragt werden soll (mehr Rückfragen).

### 21.4 Weitere Review-Befunde

**Offene Entscheidung:** Anthropic-Datenverkehr nutzt weiterhin System-Proxy und `SSL_CERT_FILE`/`SSL_CERT_DIR` aus der
Umgebung.
- Kanzleien benötigen häufig einen Unternehmens-Proxy.
- Bei HTTPS sieht der Proxy nur einen Tunnel.
- Ein Risiko entsteht nur durch ein untergeschobenes Stammzertifikat bzw. einen TLS-inspizierenden Proxy. Das ist eine
  Entscheidung der Kanzlei-IT.
- Vorschlag: aktiven Proxy und eigene Zertifikatsquelle in der Systemprüfung anzeigen.

**Bekannt, nicht geändert:**
- `scripts/e2e_summary_judge.py` (Entwicklerskript) setzt kein `base_url`.
- Der Entscheidungsweg nimmt jeden kennzeichenförmigen Begriff an, nicht nur die angebotenen Kandidaten. Das Review fand
  in Überlappungsversuchen kein Leck: Ein solcher Begriff maskiert, er hebt keine Maskierung auf. Eine Prüfung gegen die
  angebotenen Kandidaten würde den Weg zusätzlich schließen.
- Ein Ziel, das nicht zulässig ist, meldet im Chat „Die Cloud-KI ist nicht konfiguriert (kein Anthropic-API-Schlüssel
  hinterlegt)“. Das ist fail-closed, aber irreführend für die Administration.
- Die Rückfrage-Karte formuliert für Kennzeichen „ob es sich um eine Person handelt“ bzw. „Als Person behandeln“. Die
  Wirkung ist richtig (pseudonymisieren), der Text passt für Kennzeichen aber nicht.

### 21.5 Prüfungen

**Quellcode:**
- Gezielte Tests: Endpunkt-Politik, Kennzeichen, Entscheidungen, Ollama, Proxy, Statusanzeige, Relay.
- Volle Suite auf `cb8b02c`: 3973 bestanden. Fehlgeschlagen sind die 4 bekannten `test_design_refresh`-Fälle (fehlende
  Logos) und 2 Fälle, die bewusst angepasst wurden („KL AB 12“, siehe oben).
- Nach dem Entscheidungs-Fix wurde die volle Suite von Claude Code wegen Speichermangels des Systems bei 7 % abgebrochen,
  ohne Fehlschlag bis dahin. Ersatzweise liefen alle 58 betroffenen Testdateien (Datenschutz, Chat, Entwurf, KI-Provider,
  Einstellungen, Systemprüfung): 2021 bestanden, 0 Fehlschläge.
- Messkette auf eingefrorenem Code (`h8`): identisch mit `h7`.

**SDK-Payload:**
- Der Ende-zu-Ende-Nachweis aus §20 mit echtem SDK und kontrolliertem Transport ist unverändert grün.
- Neu belegt: Das Ziel ist trotz gesetztem `ANTHROPIC_BASE_URL` die offizielle HTTPS-Adresse, mit Zertifikats- und
  Hostnamenprüfung und mindestens TLS 1.2.

**Installierter Build (`95f837c`, Installer neu erstellt und installiert; Bundle enthält alle neuen Funktionen):**
- Loopback-Ziel ohne QA-Freigabe: 0 Anfragen am Prüfendpunkt, sichtbare Sperrmeldung.
- Fall 16 (neu, unbeschriftete Kennzeichen):
  - beide Kennzeichen vor jedem Versand erfragt;
  - danach als `[KENNUNG_nn]` maskiert;
  - genau ein Cloud-Aufruf;
  - Fundstellen, Senatszeichen, DIN EN, Beträge und Paragraphen lesbar und nicht erfragt;
  - Rückführung korrekt.
- Fälle 1–11, 14, 15: PASS.
- Fälle 12 und 13: Einziger Unterschied zum Vorbuild ist die beabsichtigte Rückfrage zu „KL AB 12“ (Spedition), danach
  maskiert. In Fall 12 bleiben die zwei vorbestehenden Lesbarkeitsbefunde aus §20 unverändert.
- Nebenbefund (vorbestehend): „Vgl.“ und „Urt.“ werden in einem Fall als `[PERSON]` übermaskiert.

### 21.6 Bewertung

| Prüfpunkt | Status | Beleg |
|---|---|---|
| Anthropic-Aufruf und SDK-Payload | **PASS** | autorisierter Live-Aufruf (21.7): Payload ohne Klartextwerte, Antwort des Modells erhalten, Rückführung fehlerfrei (zweiter Lauf mit neuem Schlüssel) |
| TLS und Gateway | **TLS PASS (live); Gateway NICHT VERIFIZIERT** | TLS 1.3 zu api.anthropic.com, Zertifikat Google Trust Services, Hostname geprüft (21.7); Lexono-Gateway (P0-08) zurückgestellt, Relay-Ziel festgelegt |
| Unbeschriftete Kennnummern | **PASS für kennzeichenförmige Folgen, mit dokumentierten Restlücken** | Rückfrage bzw. Maskierung, Fehlalarm-Messung, installierter Build Fall 16; andere unbeschriftete Nummern offen (fachliche Entscheidung) |
| Entfernte KI-Endpunkte | **PASS** | nur dieser Rechner ohne Freigabe, kein Proxy, Einstellungsseite lehnt ab, 0 Netzverbindungen im Test |
| Tests und installierter Build | **PASS** | volle Suite auf dem Endstand `c170abb`: 3983 bestanden, nur die 4 bekannten `test_design_refresh`-Fälle (fehlende Logos) fehlgeschlagen; installierter Build `95f837c` 14/16 PASS, Fälle 12 und 13 nur beabsichtigte Änderung |
| Restrisiken und Pilotfähigkeit | **begrenzter, begleiteter Pilot mit synthetischen bzw. freigegebenen Testakten; keine Echtdaten** | siehe unten |

**Keine Echtdatenfreigabe.** Vorher erforderlich:
1. (Erledigt, 21.7: erfolgreicher echter Anthropic-Aufruf mit synthetischen Daten, P0-07 für den Direktpfad. Der neue
   Schlüssel ist auf Wunsch des Owners in die installierte Konfiguration übernommen. Danach lieferte der installierte
   Build eine echte Modellantwort auf eine synthetische Chat-Frage, gegen die QA-Datenbank.)
2. Fachliche Entscheidungen des Owners (13.10.2026):
   - Kennzeichen ohne Bezeichnung im Modus „mask“ werden weiter vorsichtshalber maskiert; im Chat wird gefragt.
   - Anthropic-Verkehr nutzt weiterhin System-Proxy und System-Zertifikate. Ein vertrauenswürdiges Stammzertifikat bzw.
     der Verzicht auf TLS-Inspektion für api.anthropic.com ist eine Vorgabe an die Kanzlei-IT.
   - Unbeschriftete Konto-, Vertrags- und Kundennummern: Der Owner verlangt eine dauerhafte, kontextabhängige
     Schutzstrategie als eigenes Arbeitspaket. Keine pauschale Maskierung, keine Übergangslösung. **Umgesetzt in §22**
     (Korpus: 26/32 → 0/32 im Klartext); die dort dokumentierten Restlücken und offenen Entscheidungen bleiben.
3. (Erledigt nach Abschluss: volle Suite auf `c170abb` – 3983 bestanden, nur die 4 bekannten `test_design_refresh`-Fälle fehlgeschlagen.)
4. (Erledigt: Release-Notiz in RELEASE_NOTES.md, „Breaking Changes“, Nachtrag 13.10.2026.)

Eine vollständige Leckfreiheit ist nicht belegt.

### 21.7 Autorisierter Live-Nachweis (13.10.2026)

Ausdrücklich freigegeben durch den Owner. Ausgeführt wurde `scripts/verify_anthropic_live.py --authorized-live-call`.
- Schlüssel aus `C:\ProgramData\Lexono\.env`, nur in der Prozessumgebung, nicht ausgegeben.
- `ANTHROPIC_BASE_URL` war nicht gesetzt.
- Gesendet wurde nur der eingebaute synthetische Text, über Gateway (Pseudonymisierung, Restrisiko-Scan, Siegel),
  Factory (festgelegtes Ziel) und SDK.

| Nachweis | Ergebnis |
|---|---|
| Payload vor dem Versand | kein synthetischer Klartextwert (Name, Straße, Rufnummer, Kennzeichen) |
| TLS live | api.anthropic.com, TLS 1.3, Aussteller Google Trust Services, gültig bis 20.12.2026, Hostname geprüft |
| Übertragung | Request erreichte die Anthropic-API (Antwort mit Request-ID `req_011CfxzqtbNQ7Fnoo7NAeZLA`) |
| Antwort | **401 `authentication_error` „invalid x-api-key“**: der hinterlegte Schlüssel (Format `sk-ant-…`) ist ungültig oder widerrufen |
| Fehlerverhalten | Ausnahme ohne Ausweichpfad, kein zweiter Versuch über einen anderen Weg |

**Erster Lauf:** Übertragungsweg, Ziel und TLS waren real belegt, die Antwort scheiterte am abgelaufenen Schlüssel.

**Zweiter Lauf** mit dem neuen Schlüssel des Owners (aus der Entwicklungs-`.env`, die nicht versioniert wird):

| Nachweis | Ergebnis |
|---|---|
| Payload vor dem Versand | kein synthetischer Klartextwert |
| TLS live | api.anthropic.com, TLS 1.3, Google Trust Services, Hostname geprüft |
| Antwort | Modellantwort erhalten (399 Zeichen) |
| Rückführung | fehlerfrei |

Damit ist der echte Anthropic-Aufrufpfad (Gateway → Factory mit festgelegtem Ziel → SDK → TLS → API) mit synthetischen
Daten **belegt**. Nicht Teil dieses Nachweises sind das Lexono-Gateway (P0-08, zurückgestellt) und der installierte
Build: Dessen Konfiguration `C:\ProgramData\Lexono\.env` enthielt zu diesem Zeitpunkt noch den abgelaufenen Schlüssel.

**Installierter Build mit neuem Schlüssel:**
- Der Schlüssel wurde auf Wunsch des Owners übernommen: nur die Schlüsselzeile ersetzt, die vorherige Datei gesichert, der
  Wert nicht ausgegeben.
- Ausgeführt gegen die QA-Datenbank und die echte API, ohne Zielumleitung.
- Frage: „Herr Karl Brenner fragt: Was regelt § 823 Abs. 1 BGB?“
- Ergebnis: inhaltliche Modellantwort (543 Zeichen), keine Konfigurationsmeldung, keine Platzhalter in der Antwort
  (**PASS**).


## 22. Schutzstrategie für unbeschriftete Nummern (13.10.2026, `8147725`)

Auftrag des Owners (Antwort auf §21.6): eine dauerhafte, kontextabhängige Strategie für Konto-, Vertrags-, Kunden- und
andere Kennnummern ohne Bezeichnung. Keine pauschale Maskierung, keine Übergangslösung. Nur synthetische Daten.

### 22.1 Policy

| Klasse | Beispiele | Behandlung |
|---|---|---|
| **Juristische und sachliche Angaben** | § 823 Abs. 1 BGB, Art. 6 DSGVO, VO (EG) Nr. 1393/2007, BT-Drs. 19/12345, NJW 2020, 2345, BeckRS 2021, 1234567, ECLI, EuGH C-311/18, Rn., Seite, Anlage K 3, DIN EN 1504-3, ISBN, Version, Gebühr Nr. 3100 VV RVG; Beträge mit Währung oder Dezimalteil, Mengen mit Einheit, Prozent, Uhrzeiten, Jahreszahlen und -listen; Artikel-, Teile-, Serien-, EAN/GTIN-Nummern, BLZ | **lesbar**, keine Rückfrage |
| **Personenbeziehbare Kennnummer mit Bezeichnung** (Stufe 1, deterministisch) | Versicherungsschein-, Policen-, Schaden-, Patienten-, Personal-, Zähler-, Vorgangs-, Auftrags-, Fall-, Bescheid-, Beitrags-, Lieferschein-, Sendungs-, Versichertennummer, Mandatsreferenz, Kassenzeichen, „Ihr/Unser Zeichen“, Geschäftszeichen, „Gz.“, „Ref.“, „Kd.-Nr.“; Gattungswort direkt vor einer Nummer mit mindestens fünf Ziffern („Vertrag 88231457“, „Kunde 4471123“, „Police 7781234-55“) | **maskiert** als `[KENNUNG_nn]`, ohne Rückfrage |
| **Nie freigebbar** (Stufe 1) | Zahlungskarte (13–19 Ziffern, Luhn-Prüfung, Netzpräfix oder Gruppierung), Rest einer Rentenversicherungsnummer („120380 L 512“) | **maskiert**, auch eine Anwaltsentscheidung „nicht personenbezogen“ gibt sie nicht frei |
| **Kennnummernförmig ohne Bezeichnung** (Stufe 2, unsicher) | ab 7 Ziffern (Gruppen mit Bindestrich, Schrägstrich, einem Leerzeichen, Gedankenstrich oder Zeilenumbruch), mit Punkten gruppiert ohne Währung, Codes aus Buchstaben und mindestens 4 Ziffern (auch klein geschrieben) | **Rückfrage** im Chat (Modus „ask“), **vorsichtshalber maskiert** im Modus „mask“; Anwaltsentscheidung wird gespeichert |

**Konsistenz und Rückführung:**
- Derselbe Wert erhält im ganzen Text denselben Platzhalter.
- Die Rückführung ist exakt (Messung: 0 Abweichungen).

**Kontext:**
- Stufe 2 sucht im **Originaltext**. Die Regeln für „lesbar lassen“ greifen also auf die ursprüngliche Formulierung, auch
  wenn die NER ein Nachbarwort bereits maskiert hat.
- Gefragt wird, solange irgendein Teil der Nummer im Versandtext noch lesbar ist. Bruchstücke neben einem Platzhalter
  zählen mit.
- Ein maskierter Name macht eine Nummer nicht unbedenklich.

### 22.2 Implementierung

- `app/privacy/detectors.py`:
  - `detect_referenznummer` (Stufe 1);
  - `detect_zahlungskarte_und_sv_rest`;
  - „Kd.-Nr.“ im bestehenden Kundennummer-Muster;
  - `REFERENCE_LABEL_WORDS`: Bezeichnungen, die die NER neben einem Platzhalter nicht mehr als Person liest.
- `app/privacy/presidio_ner.py`: Diese Bezeichnungen fließen in `_is_identifier_label` ein, jetzt auch im
  PROPN-Zweitsignal. Vorher führte „Patientennummer [KENNUNG_01]“ zu einer Fail-Closed-Blockade.
- `app/privacy/review.py`:
  - `_unlabelled_identifiers` mit Ausnahmen (Stufe 2);
  - `_trim_at_detector_spans`: Ein Kandidat, der in ein Aktenzeichen u. Ä. hineinragt, wird an dieser Grenze gekürzt;
  - `_still_readable` zählt gegen die Vorkommen außerhalb der Nummer;
  - `is_identifier_shaped_term`;
  - kurze Entscheidungen greifen nicht an Fundstellen- oder Betragsstellen;
  - Bezeichnungen werden nicht als Name erfragt.
- `app/privacy/decisions.py`: Kennnummernförmige Entscheidungen sind speicherbar. Ohne das hätte sich die Endlos-Rückfrage
  aus §21.3 wiederholt.

### 22.3 Messung (vorher → nachher)

| Messung | Vorher (`3c89181`) | Nachher |
|---|---|---|
| Nummern-Korpus (51 Fälle: 32 schutzbedürftige, 48 lesbar zu haltende Werte) | **26 von 32 im Klartext**, 0 Rückfragen | **0 von 32**, Rückfrage in 6 Fällen (alle echte unbeschriftete Kennnummern), 0 Blockaden, keine zusätzliche Fehlmaskierung, Rückführung exakt |
| 916 vorhandene synthetische Texte (echtes Gateway, Modi „mask“ und „ask“) | 5 Blockaden, 9 Texte mit Rückfrage | 5 Blockaden (dieselben), 15 Texte mit Rückfrage. Die 8 neuen Rückfragen betreffen echte Kennungen: 5 bisher lecke USt-IdNr./Steuer-ID/Versichertennummer-Varianten, FIN, Packungsnummer, Dokumentcode. 0 neue Blockaden, Rückführungsabweichungen unverändert (74, vorbestehende Textnormalisierung) |
| Review-Sätze 7 und 8 | – | 16 geänderte Fälle, alle Verbesserungen: bisher lecke Steuernummern (4×), Ausweis-/Passnummern, Schaden-Nr., „unser Zeichen“, Dokument-ID, Vorgangsnummer jetzt maskiert; Bezeichnungen „Pass-Nr.“/„FS-Nr.“ jetzt lesbar statt `[PERSON]`; 0 neue Blockaden |
| Presidio-Audit (eingefrorener Korpus), Batterie | C = FN 1/80, FP 6/104, 0 Blockaden | unverändert |
| Volle Testsuite (`8147725`) | – | 4061 bestanden, nur die 4 bekannten `test_design_refresh`-Fälle fehlgeschlagen |

### 22.4 Unabhängige Reviews

Vier Runden mit über 300 synthetischen Fällen durch das echte Gateway. Jede Runde fand echte Befunde, alle behoben und
als Test abgesichert:
- Runde 1:
  - Lecks bei Punktgruppen, kleingeschriebenen Codes, Zeilenumbruch;
  - Bruchstücke neben Platzhaltern;
  - Freigabe von Kartennummern per Entscheidung;
  - zwei neue Blockaden;
  - Fehlalarme bei EU-Verordnungen, EuGH, BeckRS, Gegenstandswert, ISBN.
- Runde 2:
  - weitere Trennzeichen;
  - Luhn-Fehlalarme vor Beträgen;
  - unnötige Rückfragen durch Teilvorkommen.
- Runde 3: Tabulator und Doppelleerzeichen verbanden Tabellenspalten und eine Zahl mit dem Aktenzeichen davor (Leck).
  Diese Trennzeichen sind zurückgenommen.
- Runde 4:
  - endlose Rückfrage bei sehr langem Kandidaten;
  - „-123456“ nach Aktenzeichen;
  - kurze Entscheidungen maskierten Fundstellen.

**Installierter Build (`8147725`, Installer neu erstellt und installiert; Bundle enthält alle neuen Funktionen):**
- Fall 17 (neu, Nummern):
  - beide unbeschrifteten Nummern vor dem Versand erfragt, danach maskiert;
  - bezeichnete Schaden-, Policen- und Patientennummer sowie die Zahlungskarte ohne Rückfrage maskiert;
  - Paragraph, BGH-Aktenzeichen, NJW-Fundstelle, Rn., Drucksache, Beträge und Laufleistung lesbar;
  - die wiederholte Nummer hat denselben Platzhalter;
  - genau ein Cloud-Aufruf, Rückführung korrekt.
  - Zwei Prüfungen fielen formal durch: Eine war ein Teilzeichenfehler im Prüfskript („2345“ in „1234567890“). Die
    andere ist die vorbestehende NER-Übermaskierung „Artikelnummer A123456789.“ → `[PERSON]`; auf dem Vorstand
    `3c89181` gleich.
- Fälle 1–11 und 14–16: PASS.
- Fälle 12 und 13: nur die beabsichtigten Änderungen. Fall 12 unverändert gegenüber §21; Fall 13 zusätzlich die jetzt
  maskierte Lieferscheinnummer (§22.5).

### 22.5 Bewusst geänderte frühere Erwartungen

Lieferschein-, Vorgangs- und Patientennummer sowie eine Ausweisnummer mit ungültiger Prüfziffer galten früher als lesbar.
Jetzt werden sie maskiert:
- Sie sind Bezüge auf einen konkreten Vorgang mit einer Person bzw. eine Gesundheits- oder Ausweiskennung.
- Eine ungültige Prüfziffer macht eine Ausweisnummer nicht unbedenklich.

Die Tests prüfen jetzt den lesbaren Rest und tragen einen sichtbaren Vermerk. „143 815 08154 Belege“ (Steuernummer-Form
ohne Bezeichnung) ist jetzt eine Rückfrage statt Klartext.

### 22.6 Restrisiken und offene Entscheidungen

**Nicht erfasst (dokumentierte Restlücken):**
- Nummern mit 6 oder weniger Ziffern ohne Bezeichnung.
- Durch Tabulator oder mehrere Leerzeichen getrennte Nummern. Die Trennzeichen wurden bewusst zurückgenommen, siehe
  Runde 3.
- „Zeichen:“ ohne „Ihr/Unser“.
- Nummern, die nur in früheren KI-Antworten des Gesprächsverlaufs stehen.
- „4471 80331 München“: Ob „4471“ zur Nummer gehört, ist nicht entscheidbar.
- Nummern mit mehr als 80 Zeichen.

**Vorbestehend, nicht Teil dieser Änderung:**
- Daten werden als `[DATUM]` maskiert. Chronologie und Fristen sind dadurch für das Modell nicht berechenbar. Eine Lockerung
  wäre eine fachliche Entscheidung, siehe unten.
- Grundbuchangaben mit Ortsnamen und Zählpunkte werden blockiert.
- NER-Übermaskierung einzelner Fundstellenkürzel („BGBl“, „Vgl.“, „Urt.“).
- Zitierte OLG-Aktenzeichen mit Ziffernanfang werden maskiert.

**Offene fachliche Entscheidungen:**
1. Daten lesbar lassen, damit Fristen berechnet werden können, oder weiterhin maskieren.
2. Grundbuchblatt und Flurstück: Sie sind über das Grundbuch eigentümerbestimmbar. Stufe 1 (immer maskieren) oder wie
   jetzt Stufe 2.
3. Beträge ohne Währung mit Punktgruppen („Zahlung 12.345.678“ ohne „von“) werden erfragt. Das kann akzeptiert oder als
   Betrag gewertet werden.

Eine vollständige Leckfreiheit ist nicht belegt.


## 23. Datumspolicy, Grundbuchangaben, kurze Nummern, Verlaufskennungen; API-Schlüssel-Backup; P0-08 (13.10.2026, `953dc8d`)

Owner-Entscheidungen (Auftrag 13.10.2026), umgesetzt auf `64be4f1`. Nur synthetische Daten.

### 23.1 Datumspolicy

**Produktentscheidung:** Sachverhalts-, Vertrags-, Frist- und Ereignisdaten werden nicht mehr pauschal maskiert.
Chronologie, Abstände und Fristberechnung bleiben erhalten.

| Datum | Behandlung | Begründung |
|---|---|---|
| Sachverhalts-, Vertrags-, Frist-, Ereignisdatum, auch Todesdatum, Zeiträume, ISO/Schrägstrich | **lesbar** | juristischer Kern (Fristen, Reihenfolge, Abstände). Vorher waren alle `[DATUM_nn]`, und Claude erhielt nur eine lokal berechnete Einordnung |
| **Geburtsdatum** (am Geburtskontext erkannt) | **maskiert** `[GEBURTSDATUM_nn]`, konsistent je Wert | starkes Quasi-Merkmal: Zusammen mit Wohnort, Rolle oder Arbeitgeber ist eine Person auch bei pseudonymisiertem Namen reidentifizierbar. Selbst selten rechtserheblich; erheblich ist das Alter |
| Geburtstag als Jahrestag („feierte am … seinen 45. Geburtstag“) | **maskiert** `[GEBURTSTAG_nn]`, ohne Altersangabe | mit dem Alter wäre das Geburtsdatum exakt ableitbar |

**Erkennung des Geburtskontexts (`detectors.detect_geburtsdatum`):**
- Bezeichnung davor: „geb.“ (direkt, allenfalls „am“ und ein Wochentag), „geboren am“, „Geburtsdatum (des Kindes):“,
  „Geburtstag ist der“, „Geb.-Datum“, „DOB“, „date of birth“; „*“ nur als Apposition.
- Danach „geboren“, „geborene“, „zur Welt gekommen“.
- Datum oder Lebensdaten in Klammern bzw. zwischen Kommas direkt nach einer Personenbezeichnung, wenn das Geburtsjahr
  plausibel ist (mindestens 14 Jahre zurück).
  - Nicht bei „seit/ab/bis/Beginn/Eintritt/Einzug“ danach.
  - Ein Zeitraum zählt nur bei Verstorbenen oder ab 14 Jahren Spanne als Lebensangabe.
- In Tabellen und CSV die Spalte unter „Geburtsdatum“.

**Alter (`date_chronology.build_age_note`):**
- Je `[GEBURTSDATUM_nn]` nur die vollendeten Lebensjahre zum heutigen Tag, mit dem Hinweis volljährig/minderjährig.
- Bewusst keine Tagesabstände zu lesbaren Daten; daraus wäre das Geburtsdatum exakt zurückrechenbar.
- Die Notiz sagt ausdrücklich, dass sie nur gilt, sofern der Wert tatsächlich ein Geburtsdatum ist.
- Der Prompt verbietet, ein Geburtsdatum zu errechnen.

### 23.2 Grundbuch, kurze Nummern, frühere KI-Antworten

**Grundbuch (`detectors.detect_grundbuch`):**
- Deterministisch maskiert (`[GRUNDBUCH_nn]`, konsistent je Wert):
  - Grundbuchblatt (auch „Blatt-Nr.“, „Grundbuchblatt-Nr.“, bis 60 Zeichen nach „Grundbuch“);
  - Flurstück, Flst., Flst.-Nr., Flurstücksnummern, Listen und Tabellenspalten.
- Eine einstellige Flurstücksnummer wird samt Bezeichnung maskiert. Sonst fände die Leck-Prüfung die „1“ überall im
  Text, und der Gateway würde blockieren.
- Lesbar bleiben Gemarkung, Flur, Grundbuchbezirk, Amtsgericht und „Blatt 12 der Akte“.
- Die NER liest „Flur 3“ und „Blatt 12345“ nicht mehr als Ort (`presidio_ner._LAND_REGISTER_LABEL_HIT`). Vorher wurden
  solche Sätze blockiert.

**Kurze Nummern (`review._SHORT_LABELLED`):**
- 5–6 Ziffern sind nur nach einer allgemeinen Nummernbezeichnung („Nummer“, „Nr.“, „ID“, „#“, „Kennung“) unsicher:
  Rückfrage im Chat, Maskierung im Entwurf.
- Ohne Bezeichnung bleiben sie lesbar: Postleitzahl, Seite, Stückzahl, Betrag.
- Fundstellen bleiben lesbar („Nr. 3100 VV RVG“, „§ 2 Nr. 3“).

**Frühere KI-Antworten (`review.history_only_identifiers`):**
- Kennnummern und Kennzeichen, die nur im Gesprächsverlauf der KI stehen, werden ohne Rückfrage maskiert.
- Vorher gingen sie unverändert hinaus.
- „Nicht personenbezogen“ wird respektiert. Werte, die auch im Anwalts- oder Dokumenttext stehen, laufen über die normale
  Rückfrage.

### 23.3 Messung (vorher `64be4f1` → nachher)

| Messung | Vorher (`64be4f1`) | Nachher |
|---|---|---|
| Datums-Korpus (21 Fälle: 12 Geburtsdaten, 27 Sachverhalts-/Frist-/Vertrags-/Ereignisdaten, mehrere Personen, Chronologien) | Geburtsdaten 0/12 im Klartext; **Sachverhaltsdaten 27/27 verdeckt** | Geburtsdaten **0/12** im Klartext, keine Rückrechnung über Tagesabstände; Sachverhaltsdaten **0/27 verdeckt**; 0 Blockaden; Rückführung exakt |
| Nummern-Korpus (§22) | 0/32 im Klartext; 10 von 48 lesbar zu haltenden Werten verdeckt | 0/32 im Klartext; **2 von 48** verdeckt (nur noch vorbestehende NER-/Aktenzeichen-Fälle; die Daten sind jetzt lesbar) |
| 916 synthetische Texte, echtes Gateway | 5 Blockaden, 15 Texte mit Rückfrage | 5 Blockaden (dieselben), 17 Texte mit Rückfrage (neu nur „HRB Nr. 12345“ aus dem Leck-Satz, 2×); 69 Payloads geändert, überwiegend jetzt lesbare Daten; 0 neue Blockaden; Rückführungsabweichungen unverändert (74, vorbestehend) |
| Review-Sätze 7/8 | – | 13 geänderte Fälle: Daten lesbar; IP-Adressen jetzt vollständig maskiert (vorher nur zufällig teilweise durch die Datumsmaskierung); 0 neue Blockaden |
| Batterie (2017 Texte × 2 Modi) | 4 Blockaden | **2 Blockaden** (aufgehoben: Grundbuchangabe „Flur“/„Blatt“ als Ort) |
| Presidio-Audit (eingefrorener Korpus) | C = FN 1/80, FP 6/104, 0 Blockaden | unverändert |
| Volle Testsuite (`953dc8d`) | – | 4141 bestanden, nur die 4 bekannten `test_design_refresh`-Fälle (fehlende Logo-Dateien) fehlgeschlagen |

**IPv4-Adressen:** Die Messkette deckte auf, dass die bisherige pauschale Datumsmaskierung „10.0.12“ in IP-Adressen nur
zufällig verdeckt hatte. Nach der Datumspolicy ging „Zugriff von 10.0.12.77“ im Klartext hinaus. Eine eigene
IPv4-Erkennung schließt das (`detectors.detect_ip_adresse`); Versions- und Kapitelnummern bleiben lesbar.

### 23.4 Unabhängige Reviews

Zwei Runden, jeweils über 100 Fälle durch das echte Gateway.

**Runde 1 fand:**
- Geburtsdatums-Lecks: Wörter zwischen Bezeichnung und Datum, ISO-Datum, DOB, Kommas statt Klammern, Lebensdaten,
  Tabellen und CSV;
- Grundbuch-Lecks: „Blatt-Nr.“, „Flst.-Nr.“, Plural, Tabellen;
- eine Blockade bei einstelligen Flurstücken;
- falsche Geburtsdaten mit falscher Altersangabe: Mietbeginn in Klammern, Fußnoten-Stern;
- Daten mit Schrägstrich als Kennnummer.

**Runde 2 fand:**
- Geburtsnamen nach „geb.“ verdeckten Sachverhaltsdaten;
- „Geburtstagsfeier“, Tabellentrenner und Mietzeiträume wurden als Geburtsdatum gelesen;
- Jahrestage erzeugten eine falsche Altersangabe.

Alles ist behoben und als Test abgesichert.

### 23.5 API-Schlüssel-Backup

`C:\ProgramData\Lexono\.env.bak_20261012_230319` wurde ohne Ausgabe von Werten geprüft:
- Der enthaltene Schlüssel (SHA-256-Präfix `0312bb4b`) ist genau der Schlüssel, den Anthropic in beiden Live-Läufen am
  13.10.2026 mit 401 „invalid x-api-key“ abgelehnt hat (`req_011CfxzqtbNQ7Fnoo7NAeZLA`, `req_011Cfy1FCmQM156DsKvV9dWg`,
  §21.7). Die Datei war seit 22:12:39 unverändert.
- Alle übrigen 34 Zeilen sind mit der aktuellen Konfiguration identisch.
- Ein abgelaufener Schlüssel muss nicht aufbewahrt werden.

Alle drei Bedingungen sind erfüllt: Das Backup wurde **gelöscht**.

### 23.6 P0-08 – Lexono-Gateway

P0-08 bleibt zurückgestellt und wurde weder weiterentwickelt noch aktiviert. Es ist jetzt als **verpflichtender
Prüfpunkt vor dem späteren Pilotbetrieb** in `LEXONO_MASTER_PRODUCT.md` (§7, P0-08) eingetragen. Vor dem Pilot gilt
eines von zwei:
1. Gateway verifiziert und betrieben: Schlüssel nicht auf Kanzlei-PCs, Authentisierung, Begrenzung und Protokollierung
   gegen einen echten Server getestet.
2. Eine dokumentierte, vom Owner unterzeichnete Entscheidung für den Direct-Key-Pfad mit seinem Risiko: echter Schlüssel
   in `C:\ProgramData\Lexono\.env` auf dem Kanzlei-PC.

Der direkte Anthropic-Pfad (P0-07) ist davon getrennt und live belegt (§21.7).

**Installierter Build (`953dc8d`, Installer neu erstellt und installiert; Bundle enthält alle neuen Funktionen):**
- Fall 18 (neu):
  - Geburtsdatum maskiert, mit Altersangabe;
  - Mietvertrags-, Kündigungs-, Räumungsfrist- und Mängel-Chronologie lesbar;
  - Grundbuchblatt und Flurstück maskiert, „Flur 3“ lesbar;
  - keine Rückfrage, genau ein Cloud-Aufruf, Rückführung korrekt (**PASS**).
- Fälle 1–11 und 14–16: PASS.
- Fälle 12, 13, 17: unverändert gegenüber dem Vorbuild (nur die in §21/§22 dokumentierten beabsichtigten Änderungen bzw.
  die vorbestehende NER-Übermaskierung).
- Nebenbefund: Die lokale Fristerkennung führt das (maskierte) Geburtsdatum als „Mögliche Frist (unreviewed)“. Das ist
  kein Leck, sondern ein Qualitätsthema der Fristerkennung.

### 23.7 Bewertung, Restrisiken und Freigabebedingungen

**Datenschutz und Sachverhaltstreue werden getrennt bewertet:**

| Bereich | Datenschutz | Sachverhaltstreue |
|---|---|---|
| Daten | Geburtsdaten: 0 von 12 im Klartext (Korpus), Jahrestag und Alter ohne Rückrechnungsmöglichkeit | 0 von 27 Sachverhalts-, Frist- und Vertragsdaten verdeckt (vorher 27 von 27); Reihenfolgen und Abstände unverfälscht |
| Grundbuch | Blatt- und Flurstücksnummern maskiert, konsistent | Lage, Gemarkung, Flur und Amtsgericht lesbar; vorher blockierte Sätze werden gesendet |
| Kurze Nummern / Verlauf | zusätzliche Rückfragen nur hinter einer Nummernbezeichnung; Verlaufskennungen maskiert | Fundstellen, Postleitzahlen und Beträge lesbar |

**Restrisiken:**
- **Lesbare Ereignisdaten.** Zusammen mit konkreten Umständen (Unfallort, Uhrzeit) sind sie über Presseberichte
  zuordenbar. Das ist eine bewusste Abwägung der Produktentscheidung; keine pauschale Maskierung.
- **Todesdaten sind lesbar.** Sie sind das stärkste verbleibende Reidentifikationsmerkmal (Traueranzeigen).
  **Offene fachliche Entscheidung:** wie Geburtsdaten schützen oder lesbar lassen.
- **Geburtsdaten ohne erkennbaren Kontext bleiben lesbar.** Beispiel: „Herr Brenner, 01.09.1998 eingestellt“ ist bewusst
  mehrdeutig, ebenso ein bloßes Datum in einer Liste ohne Kopfzeile.
- **CSV-Zeilen mit Namen** werden von der NER-Stufe weiterhin blockiert. Das ist vorbestehend und fail-closed.
- Die Restlücken aus §22.6 bleiben bestehen.

**Freigabebedingungen vor Echtdaten (keine Freigabe durch diese Arbeit):**
1. **P0-08:** verifiziert oder vom Owner unterzeichnete Direct-Key-Entscheidung (siehe 23.6).
2. **Fachliche Entscheidung:** Todesdaten schützen oder lesbar lassen.
3. **Organisatorisch:**
   - Anwenderhinweis zur Rückfrage-Karte (Kennnummern, Kennzeichen);
   - Anwenderhinweis zur Datumspolicy;
   - Release-Notiz.
4. **Pilot:** begleiteter Pilotbetrieb mit freigegebenen Testakten. Auswertung der Rückfragen und Fehlalarme vor
   Echtdaten.

Eine vollständige Leckfreiheit ist nicht belegt.


## 24. Todesdaten und Fristerkennung ohne Geburts-/Todesdaten (13.10.2026, `5cafe0f`, `58c1a1e`)

Owner-Auftrag nach §23 (offene Entscheidung „Todesdaten“), Ausgangsstand `ed0035e`. Nur synthetische Daten.

### 24.1 Todesdaten

Policy nach dem Geburtsdatum-Prinzip (§23):
- Ein am Todeskontext erkanntes Datum wird vor dem Cloud-Versand als `[TODESDATUM_nn]` maskiert, konsistent je Wert über
  Text, Dokumente, Gesprächsverlauf und Argumente.
- Lokal geht nur die **Zahl der vollen Jahre seit dem Tod** hinaus (`date_chronology.build_death_note`).
- Bewusst nicht übergeben werden Tagesabstände und die Lage relativ zu lesbaren Daten. Zwei lesbare Daten wenige Tage um
  den Todestag würden ihn sonst praktisch offenlegen.
- Erbrechtliche Fristen ab dem Erbfall berechnet der Anwalt mit dem echten Datum. Der Prompt verbietet, das Datum zu
  errechnen oder zu vermuten.

**Erkannt (`detectors.detect_todesdatum`):**
- **Bezeichnung davor** (mit Wortgrenze):
  - Wörter: „verstorben“, „gestorben“, „verstarb“, „starb“, „verschied/verschieden“, „Todestag“, „Todesdatum“,
    „Todeszeitpunkt“, „Sterbedatum/-tag“, „Sterbefall“, „Todesfall“, „Ableben“, „Erbfall“;
  - Kürzel: „gest. (am)“, „verst. (am)“, „†“;
  - Wendung: „der Tod (des Vaters) (ist/trat) am“.
  - Zwischen Bezeichnung und Datum stehen höchstens 45 Zeichen ohne Ziffer, Satz-/Tabellentrenner, Klammer oder Komma.
    Ein Komma ist nur direkt vor dem Datum erlaubt.
  - Stoppwörter in dieser Lücke beenden den Todeskontext: Frist, bis, spätestens, Termin, wurde, und, oder, beigesetzt,
    beerdigt, eröffnet, Antrag, Zahlung, Schreiben, Vertrag.
- **Danach** nur mit festen Füllwörtern: „in Köln“, „im Krankenhaus“, „nach langer Krankheit“, dann „verstorben“,
  „gestorben“ oder „verschieden“; außerdem „die am … Verstorbene“.
- **Zeilenumbruch** zwischen Bezeichnung und Datum nur, wenn die Vorzeile mit einer ausdrücklichen Datumsbezeichnung
  endet („Todesdatum:“). Nicht nach „Todesfall gemeldet“ oder der Überschrift „Erbfall“.
- **Lebensdaten** nach einer Personenbezeichnung, in allen Datumsformaten (ISO, Monatsname, Klammer oder Komma).
- **Traueranzeige** „* 12.03.1940 † 01.02.2024“: Geburts- und Todesdatum.
- **„verstorbene Mutter (Datum)“.**
- **Tabellen und CSV:** die Spalte „Sterbedatum/Todesdatum/verstorben/gest.“.

**Lesbar bleiben:**
- „Unfall mit Todesfolge am“, „Todesanzeige erschien am“, „Beerdigung am“, „Testament vom“, „Todesfallleistung“;
- „Erbfall eingetreten; Antrag vom“, „verstorben, Beerdigung am“, „verstorben und wurde am … beigesetzt“;
- Kenntnis-, Bescheid- und Schreibensdaten, die nur ein Todes-Wort im Satz haben, etwa „Am 05.03.2024 erfuhr die Tochter,
  dass der Vater verstorben ist“. Das ist die Kenntnis, mit der die Ausschlagungsfrist beginnt.

### 24.2 Fristerkennung

**Befund (installierter Build `953dc8d`, Fall 18):** Die lokale Fristerkennung schlug jedes Datum vor. Das maskierte
Geburtsdatum erschien als „Mögliche Frist (unreviewed): [GEBURTSDATUM_01]“.

**Korrektur (`deadlines/extractor.py`):**
- Geburts- und Todesdaten erzeugen keinen Fristvorschlag. Erkannt werden sie mit denselben Regeln wie in der
  Pseudonymisierung.
- Steht ein Fristen-Schlüsselwort vor oder nach dem Datum, bleibt es **immer** ein Vorschlag. Eine echte Frist geht durch
  eine Fehlerkennung nie verloren.
- Bereits gespeicherte Altvorschläge filtert `local_ai_provider._build_argumentationspunkte`. Geprüft wird am Ausschnitt
  und am Volltext des Quelldokuments (Tabellenzeilen ohne Kopf); Ausschnitte mit Schlüsselwort nie.
- Kein Schutz entfällt: Die Argumente laufen weiterhin vollständig durch den Gateway.

### 24.3 Messung (vorher `ed0035e` → nachher)

| Messung | Vorher | Nachher |
|---|---|---|
| Todesdaten-Korpus (13 Fälle: 10 Todes-/Lebensdaten, 11 lesbar zu haltende Daten inkl. „Todesfolge“/„Todesanzeige“/„Beerdigung“) | **9 von 10 Todesdaten im Klartext** | **0 von 10**, 0 von 11 lesbaren Daten verdeckt, 0 Blockaden, Rückführung exakt |
| Datums-Korpus (§23) | 0/12 Geburtsdaten im Klartext | unverändert; „verstarb am“ (K07) jetzt bewusst maskiert |
| Nummern-Korpus (§22) | 0/32 | unverändert |
| 916 synthetische Texte, Review-Sätze, Presidio-Audit, Batterie | – | **0 Änderungen** (keine neue Blockade, Rückfrage oder Maskierung) |
| Fristvorschlag Geburts-/Todesdatum | ja | nein; echte Fristen, Vertrags-, Kenntnis- und Termindaten weiterhin vorgeschlagen |
| Volle Testsuite (`5cafe0f`) | – | 4201 bestanden, nur die 4 bekannten `test_design_refresh`-Fälle (fehlende Logo-Dateien) fehlgeschlagen |

### 24.4 Unabhängige Reviews

**Runde 1 fand:**
- „Verstorbenen“ ohne Wortgrenze. Dadurch wurde eine echte Frist („Erben des Verstorbenen haben Frist bis …“) als
  Todesdatum maskiert und ging als Frist verloren.
- Weitere Lecks: „verst./gest. am“, „starb“, „Todeszeitpunkt“, „Sterbefall“, Zeilenumbruch, Lebensdaten in ISO- und
  Monatsform, Traueranzeige.
- Gespeicherte Tabellen-Altvorschläge wurden nicht gefiltert.

**Runde 2 fand Übergriffe der Korrekturen:**
- Die freie Lücke nach dem Datum traf Kenntnis- und Bescheiddaten.
- Die Zeilenumbruch-Erweiterung traf einen Termin.
- „verschieden am“ fehlte.

Alles ist behoben und als Test abgesichert.

**Installierter Build (`58c1a1e`; Installer zweimal neu erstellt und installiert, Bundle enthält alle neuen Funktionen):**
- **Fall 19 (neu):**
  - Geburts- und Todesdatum maskiert;
  - Testament-, Eröffnungs-, Frist-, Termin- und „Todesfolge“-Datum lesbar;
  - die Fristvorschläge enthalten weder Geburts- noch Todesdatum, die echte Ausschlagungsfrist ist enthalten;
  - genau ein Cloud-Aufruf, Rückführung korrekt (**PASS**).
- **Dritter Fund aus Fall 19 im ersten Build (`5cafe0f`):**
  - Der Fristausschnitt schnitt das Geburtsdatum „12.03.1940“ in der Mitte. Das Bruchstück „1940“ ging hinaus, weil keine
    Datumserkennung es mehr traf; das war vorbestehend.
  - Behoben (`58c1a1e`): Der Ausschnitt endet nur an Leerraum. Gespeicherte Altausschnitte werden um Rand-Bruchstücke
    gekürzt. Ein vollständiges Datum wird über den Wert konsistent maskiert.
  - Nach dem Neubau gab es kein Bruchstück mehr (Prüfung „kein Geburtsjahr-Bruchstück“, auch mit Altvorschlägen früherer
    QA-Läufe).
- **Fall 18:** PASS.
- **Fälle 1–11 und 14–16:** PASS.
- **Fälle 12, 13, 17:** unverändert gegenüber den Vorbuilds; nur die in §21 und §22 dokumentierten beabsichtigten
  Änderungen bzw. die vorbestehende NER-Übermaskierung.
- Die volle Testsuite auf `58c1a1e` ergab 4203 bestanden, nur die 4 bekannten `test_design_refresh`-Fälle schlugen fehl.

### 24.5 Bewertung und Restrisiken

| Punkt | Status |
|---|---|
| Todesdaten vor dem Cloud-Versand maskiert (Korpus, Gateway, Verlauf, Argumente) | **PASS** |
| Ereignisdaten mit „Tod“ nur als Wortteil lesbar, keine pauschale Maskierung von Nachbardaten | **PASS** |
| Keine indirekte Wiederherstellung (keine Tagesabstände, kein Bezug zu lesbaren Daten in den Notizen) | **PASS** |
| Geburts-/Todesdatum löst keine Frist aus; echte Fristen bleiben erhalten | **PASS** |
| Installierter Build (Fälle 1–19) | **PASS** (12, 13, 17 nur dokumentierte beabsichtigte Unterschiede) |
| Echter Cloud-Aufruf mit Todesdatum | **NICHT VERIFIZIERT** (Pfad identisch mit dem live belegten, §21.7) |

**Restrisiken:**
- **Datum ohne erkennbaren Todeskontext bleibt lesbar.** Beispiele: „tödlicher Unfall am …“, eine bloße Liste ohne
  Kopfzeile, „Todestag“ als Jahrestag.
- **Ein lesbares Beerdigungs- oder Eröffnungsdatum grenzt das Todesdatum ein.** Das ist der Preis lesbarer Ereignisdaten
  (Owner-Entscheidung §23).
- **Ein Geburtsdatum in Fristnähe** wird wieder als Fristvorschlag gelistet. Im Versand ist es maskiert; das ist ein
  Produkt-, kein Datenschutzthema.
- **Ein echtes Schreibensdatum mit gleichem Wert** wie ein Todesdatum wird mitmaskiert, weil die Maskierung je Wert
  konsistent ist. Das ist sicher, kostet aber Lesbarkeit.
- **CSV-Zeilen mit Namen** werden weiter blockiert (vorbestehend, fail-closed).

Eine vollständige Leckfreiheit ist nicht belegt. Keine Echtdatenfreigabe; die Freigabebedingungen aus §23.7 (P0-08,
Anwenderhinweise, begleiteter Pilot) gelten unverändert. Die offene Entscheidung „Todesdaten“ aus §23.7 ist hiermit
umgesetzt.


## 25. Pilotvorbereitung: Restrisiken, Anwenderanweisung, P0-08-Voraussetzungen, Pilotbewertung (13.10.2026, `44f4cb0`)

Ausgangsstand `eebd8b5`. Nur synthetische Daten. Diese Bewertung ist **keine** Echtdatenfreigabe. Die Entscheidung trifft
die Kanzlei (PILOT_CHECKLIST.md J „Datenschutz-Freigabe“).

### 25.1 Restrisiko-Analyse (unabhängig, über das echte Gateway reproduziert)

| Bereich | Befund | Art | Maßnahme |
|---|---|---|---|
| Zusammengesetzte Nummernbezeichnungen | „Matrikelnummer 123456“, „Schülernummer“, „Häftlingsnummer“, „Mitarbeiter-Nr.“, „Bewohner-Nr.“: Wert im Klartext, die NER maskierte stattdessen die Bezeichnung | **Leck** | behoben (Stufe 1, §22) |
| Ausdrückliche Todesformulierungen | „Tod seines Vaters am“, „Tod von Frau X am“, „im Alter von 82 Jahren verstorben“, „im Hospiz Sonnenhof verstorben“, „verstarb am Sonntag, dem“, „am … eingetretenen Tod“, „died“, Traueranzeige „sanft entschlafen“, „für immer verlassen“; zusätzlich als „Mögliche Frist“ | **Leck** | behoben (§24-Regeln erweitert) |
| Geburtstag als Jahrestag | „An seinem 45. Geburtstag, dem 12.03.2025“ lesbar, das Geburtsdatum damit exakt ableitbar; die Variante mit „am“ wurde als Geburtsdatum mit falscher Altersangabe („1 vollendete Jahre“) gewertet | **Leck** und **Sachverhaltsfehler** | behoben (Kategorie „geburtstag“ ohne Altersangabe) |
| Fristvorschläge | Geburts- oder Todesdatum neben einem Fristwort als „Mögliche Frist“ (im Versand maskiert); Altvorschläge aus E-Mails ohne Abgleich mit dem Nachrichtentext (Tabellenzeile ohne Kopf, Geburtsdatum im Klartext) | Fehlvorschlag bzw. **Leck** | behoben (Schlüsselwort zählt nur ohne Datum dazwischen; Volltext der E-Mail) |
| `PromptContextBuilder` | gibt `due_date` als ISO aus; derzeit ohne Produktiv-Aufrufer | kein aktuelles Leck | vorsorglich gefiltert |
| Frühere KI-Antworten | Namen, Kontakt-, Konto-, Kennzeichen-, Geburts-/Todesdaten, die nur im Verlauf stehen, werden maskiert; bei Bezug im Anwaltstext derselbe Platzhalter | **kein Leck gefunden** | – |
| „tödlicher Unfall am“, „kam … ums Leben“, „verunglückte … tödlich“, „tot aufgefunden“ | Datum lesbar | Restrisiko | **bewusst nicht geändert**: das Unfall- bzw. Auffindedatum ist das juristisch zentrale Ereignisdatum (Haftung, § 844 BGB, Verjährung) |
| Kurze Nummern bis 4 Ziffern ohne Bezeichnung | „Zimmer 12“, „Spind 27“, „Personal 4711“, „Startnummer 815“ lesbar | Restrisiko | keine zuverlässige Regel ohne Beträge, Seiten, Paragraphen und Postleitzahlen zu treffen |
| Indirekte Rekonstruktion | Altersangabe plus „am Unfalltag genau 45“ oder „an seinem Geburtstag“; „eine Woche nach dem Erbfall, am …“; Beerdigungsdatum. Zeitfenster für das Geburts-/Todesdatum bis auf einen Tag eingrenzbar | Restrisiko | relative Fristfakten sind juristisch nötig (§ 1944 BGB); eine Maskierung wäre unzuverlässig und sachverhaltszerstörend |
| NER-Übermaskierung | „Ihres Vaters“, „Grün-Weiß“, „Ausschlagungsfrist“, „Haus 14“ als Person/Ort | Lesbarkeit, kein Leck | vorbestehend, dokumentiert |

**Messung (vorher `eebd8b5` → nachher), getrennt nach Leck, Fehlalarm, Blockade, Sachverhaltsverlust:**

| Messung | Ergebnis |
|---|---|
| Neue Regressionstests (`tests/test_privacy_pilot_residual_risks.py`) | alle reproduzierten Lecks behoben; Unfall-, Auffinde- und Kenntnisdaten lesbar (kein Sachverhaltsverlust); keine Altersangabe für Jahrestage |
| Todesdaten-, Datums-, Nummern-Korpus | 0/10, 0/12, 0/32 Klartext; unverändert 0, 1, 2 verdeckte lesbar zu haltende Werte (bewusst bzw. vorbestehend); 0 Blockaden |
| 916 synthetische Texte, Review-Sätze, Presidio-Audit, Batterie | **0 Änderungen**: keine neue Blockade, Rückfrage oder Maskierung |
| Review-Runde zu den Fixes | zwei Regressionen gefunden und behoben: „geboren, am …“ als Geburtsdatum; Blockade bei zweistelliger bezeichneter Nummer (jetzt ab 3 Ziffern) |
| Volle Testsuite (`44f4cb0`) | 4225 bestanden; 4 bekannte `test_design_refresh`-Fehler (fehlende Logo-Dateien, fremde Änderung); 1 zeitabhängiger Test `test_web_knowledge.py::test_apply_update_endpoint_starts_a_real_background_update` (fester `sleep(0.3)` auf einen Hintergrund-Thread, unter Last), einzeln 3×/3 und mit der ganzen Datei 45/45 bestanden, ohne Bezug zu dieser Änderung (dort seit `eebd8b5` keine Änderung) - **unabhängiges bestehendes Problem** |

### 25.2 Anwenderanweisung

Die Anweisung steht in `PILOT_PLAYBOOK.md` **§2.2a** („Datenschutz-Kurzanleitung“) und ist Pflichtpunkt der
Anwalt-Einweisung (`PILOT_CHECKLIST.md` I). Sie behandelt:
- Prüfung der erkannten Angaben in der Dokumentvorschau vor dem Versand;
- Rückfrage-Karte und ihre Optionen;
- Blockaden und Warnungen;
- zu viel bzw. zu wenig Maskiertes;
- Meldeweg (ohne Mandantendaten, Art. 33 DSGVO);
- Grenzen der Pseudonymisierung und Verantwortung der Anwender.

Eine vollständige Leckfreiheit wird ausdrücklich nicht zugesagt.

### 25.3 P0-08: offene Architekturentscheidung (weder implementiert noch aktiviert)

**Technische Feststellungen zum heutigen Direct-Key-Pfad (geprüft, ohne Schlüsselwerte auszugeben):**
- Der Schlüssel liegt im Klartext in `C:\ProgramData\Lexono\.env`. Die Datei ist für **alle lokalen Windows-Benutzer
  lesbar** (`VORDEFINIERT\Benutzer: Lesen`, vom Ordner geerbt); der Installer setzt keine engeren Rechte.
- Er ist in der Anwendung als `SecretStr` geführt, nicht in Protokollen, Backups (bewusst ausgeschlossen),
  Einstellungsseiten oder der Diagnose enthalten.
- Er wird nur an `https://api.anthropic.com` gesendet (Zielbindung §21, TLS 1.3 belegt §21.7).
- Rotation: keine Funktion in Lexono; Austausch durch Bearbeiten der `.env` und Neustart.
- Ein Kanzlei-PC mit diesem Schlüssel kann beliebige Anfragen auf das Konto der Kanzlei stellen. TLS schützt den
  Transport, **nicht** den Schlüssel auf dem Rechner.

**Voraussetzungen für (A), Lexono-Gateway:**
1. Gateway auf einem von Lexono bzw. der Kanzlei betriebenen Server.
2. Anthropic-Schlüssel nur dort.
3. Mandanten-Zugangsdaten (Argon2id) je Kanzlei, Widerruf getestet.
4. Ratenbegrenzung und inhaltsfreie Protokolle gegen einen echten Server geprüft.
5. TLS mit gültigem Zertifikat; Ziel-URL in der Kanzlei-Konfiguration fest.
6. Auftragsverarbeitung zwischen Kanzlei, Gateway-Betreiber und Anthropic geklärt.
7. Ausfall- und Notfallweg dokumentiert.
8. Eigene P0-Abnahme (LEXONO_MASTER_PRODUCT.md P0-08).

**Voraussetzungen für (B), ausdrückliche, dokumentierte Freigabe des Direct-Key-Pfads:**
1. Eigener Anthropic-Schlüssel nur für Lexono, mit Ausgabenlimit bzw. Workspace-Limit beim Anbieter.
2. **Zugriffsschutz:** Leserecht auf `C:\ProgramData\Lexono\.env` auf Administratoren, SYSTEM und die
   Lexono-Nutzerkonten beschränkt (heute: alle lokalen Benutzer); Rechner mit Festplattenverschlüsselung,
   gesperrtem Bildschirm und ohne fremde lokale Konten.
3. **Speicherung:** Schlüssel nicht in E-Mails, Tickets, Backups oder Kopien der `.env`; Backups der `.env` (falls
   vorhanden) mit gleichem Schutz.
4. **Rotation:** festgelegtes Intervall und Anlass (Personalwechsel, Verdacht), benannte verantwortliche Person,
   Vorgehen dokumentiert (neuen Schlüssel erzeugen, `.env` ersetzen, Lexono neu starten, alten Schlüssel beim Anbieter
   widerrufen, Funktionsprüfung).
5. **Überwachung:** Nutzung und Kosten beim Anbieter regelmäßig prüfen.
6. Auftragsverarbeitung/Drittlandübermittlung an Anthropic geprüft.
7. Schriftliche Entscheidung der Kanzlei mit Datum und Verantwortlichem, bezogen auf diesen Stand.

Eine Empfehlung des Agents ersetzt diese Entscheidung nicht.

> **Nachtrag 13.10.2026 (§26.4):** Die Kanzlei hat entschieden, den Schlüssel später im Gateway zu hinterlegen. Die
> lokale Schlüsselverwaltung wird nicht geändert; die Gateway-Prüfung folgt nach der Servereinrichtung.

### 25.4 Pilotbewertung

**Technische Nachweise:**

| Prüfpunkt | Status |
|---|---|
| Pre-Cloud-Grenze, Siegel, Fail-Closed (§20) | PASS |
| Echter Anthropic-Aufruf, TLS 1.3, Zielbindung (§21) | PASS |
| Lokale KI nur auf diesem Rechner (§21) | PASS |
| Kennzeichen und Nummern (§§21–22, §25.1) | PASS, mit dokumentierten Restlücken |
| Daten: Sachverhaltsdaten lesbar, Geburts- und Todesdaten geschützt (§§23–24, §25.1) | PASS, mit dokumentierten Restlücken |
| Grundbuch, IP-Adressen, Verlaufskennungen (§23) | PASS |
| Fristvorschläge ohne Geburts-/Todesdaten (§24, §25.1) | PASS |
| Lexono-Gateway (P0-08) | **NICHT VERIFIZIERT** (zurückgestellt) |
| Schlüsselverwaltung des Direct-Key-Pfads | **FAIL** gegenüber den Voraussetzungen (B): Datei für alle lokalen Benutzer lesbar, keine Rotation |
| Vollständige Leckfreiheit | **NICHT VERIFIZIERT**, und nicht belegbar |

**Fachliche und organisatorische Entscheidungen (offen, bei der Kanzlei):**
1. P0-08: (A) oder (B) mit den Voraussetzungen aus 25.3.
2. Auftragsverarbeitung/Übermittlung an Anthropic.
3. Meldeweg und Zuständigkeit für Datenschutzvorfälle (Art. 33 DSGVO).
4. Kenntnisnahme der Restrisiken (§§22–25), insbesondere der lesbaren Ereignisdaten (Unfall-, Auffinde-,
   Beerdigungsdatum) und der Möglichkeit indirekter Rekonstruktion.
5. Einweisung aller Pilotanwender (PILOT_PLAYBOOK.md §2.2a).

**Was weiterhin gegen Echtdaten spricht (Stand heute):**
- P0-08 ist nicht entschieden.
- Der Schlüssel ist für alle lokalen Benutzer lesbar; es gibt kein Rotationsverfahren.
- Die organisatorischen Punkte 2–5 sind nicht nachgewiesen.
- Die Restlecks aus §§22–25 sind dokumentiert, aber nicht beseitigt.

**Begleiteter Pilot mit synthetischen bzw. ausdrücklich freigegebenen Testakten: technisch vorbereitet.**

**Installierter Build (`44f4cb0`, Installer neu erstellt und installiert; Bundle enthält die neuen Funktionen):**
- **Fall 20 (neu):**
  - „Tod seines Vaters am …“ maskiert;
  - Matrikel- und Mitarbeiter-Nr. maskiert;
  - „45. Geburtstag, dem …“ als `[GEBURTSTAG]` ohne Altersangabe;
  - Antrags-, Frist- und Termindaten lesbar;
  - keine Geburts-/Todesdaten unter den Fristvorschlägen, echte Frist enthalten (**PASS**).
- **Fälle 18 und 19:** PASS.
- **Fälle 1–11 und 14–16:** PASS.
- **Fälle 12, 13, 17:** unverändert (dokumentierte beabsichtigte Unterschiede bzw. vorbestehende NER-Übermaskierung).
- **Echter Cloud-Aufruf mit diesem Stand:** NICHT VERIFIZIERT. Es ist derselbe Pfad wie der live belegte (§21.7).

| Prüfpunkt | Status |
|---|---|
| Volle Testsuite | PASS (nur bekannte bzw. unabhängige Fehler, siehe oben) |
| Installierter Build, Fälle 1–20 | PASS |

## 26. Datenschutz-Entscheidungsvorlage für den Pilotbetrieb (13.10.2026, Ausgang `02dc436`)

Nur synthetische Daten. Diese Vorlage ist **keine** Freigabe echter Mandantendaten und keine rechtliche Bewertung. Sie trennt
**technische Nachweise** (im Repository bzw. am installierten Build geprüft), **Annahmen** (nicht geprüft) und
**organisatorische Pflichten** (bei der Kanzlei). Verantwortliche Personen sind nicht benannt; die Spalte „Rolle“ nennt nur,
wer zu entscheiden hat. Eine `ROADMAP.md` existiert nicht; maßgeblich ist `FUTURE_ROADMAP.md` („Lexono Data Privacy
Framework“, Phasen P1–P3).

### 26.1 Bestandsaufnahme (unabhängig, Ausgangsstand geprüft)

- `HEAD` = `origin/main` = `02dc436`; zwischen dem installierten Build (`44f4cb0`) und `02dc436` nur Dokumentation.
- Volle Testsuite auf `02dc436`: 4225 bestanden, 5 fehlgeschlagen = 4 bekannte `test_design_refresh`-Fehler (fehlende
  Logo-Dateien, fremde Änderung) und der bekannte zeitabhängige Test `test_web_knowledge.py::
  test_apply_update_endpoint_starts_a_real_background_update` (§25.1).
- Unabhängige Prüfung von Löschung, Datenminimierung, Mandantentrennung, Feedback-Lernen und Protokollen; jeder Befund
  unten mit Fundstelle. Frühere Ergebnisse (§§20–25) wurden nicht ungeprüft übernommen: die Gateway-Regeln sind durch
  die Regressionstests der vollen Suite belegt, der installierte Build durch die Fälle 1–20 (§25.4).

### 26.2 Behobene Schwachstellen (reproduziert, minimal, mit Regressionstest)

Tests: `tests/test_privacy_pilot_decision.py` (alle fünf vor dem Fix rot, danach grün), Ergänzung in
`tests/test_web_chat.py`.

| # | Befund (reproduziert) | Fix |
|---|---|---|
| 1 | Eine gelöschte Akte (Soft-Delete, Datensatz bleibt aus Aufbewahrungsgründen) lieferte weiterhin Sachverhalt und Dokumente für Cloud-Anfragen (Chat, Entwurf, Prüfung) | `RuleBasedLocalAIProvider.prepare_draft_context` verweigert die Kontextvorbereitung; der Chat zeigt fail-closed eine blockierte Antwort, die Cloud erhält nichts |
| 2 | Abgelehnte Fristvorschläge gingen weiter als „Mögliche Frist (rejected)“ an die Cloud | `_build_argumentationspunkte` lässt `review_status == "rejected"` weg (Datenminimierung) |
| 3 | Der Audit-Eintrag zum Löschen einer Unterhaltung enthielt deren Titel (oft aus der ersten Nachricht) | Audit-Text ohne Titel; Entität und ID bleiben nachvollziehbar |
| 4 | Intake-Watcher protokollierte den vollen Pfad mit Dateinamen (Dateinamen enthalten oft Mandantennamen) | Pfad über die vorhandene `mask_path_like` maskiert |
| 5 | Die Einstellung „Automatische Löschfrist“ wirkte funktionsfähig, obwohl keine Löschung umgesetzt ist (Platzhalter) | Beschriftung „Noch ohne Wirkung … der Wert wird nur gespeichert“; keine Scheinfunktion mehr |

**Unabhängige Reviews (zwei Runden):**
- **Runde 1:**
  - Der Watcher gab den Pfad weiter im Traceback aus: der Fehlertext von `IntakeError` bzw. `OSError` enthält ihn. Der
    Test hatte nur die Nachricht geprüft. **Behoben:** Protokoll ohne Traceback, nur maskierter Pfad und Fehlertyp; der
    Test prüft jetzt die formatierte Ausgabe.
  - Die neue Sperre führte bei Entwürfen einer gelöschten Akte zu Serverfehlern (500), nämlich bei Prüfung, Neu-
    generierung und „Anmerkung übernehmen“; im letzten Fall blieb zusätzlich eine verwaiste Anmerkung zurück. Dasselbe
    galt für den Schriftsatz-Assistenten mit einer gelöschten Akte. **Behoben:** Prüfung vorab mit Hinweis.
  - Gelöschte Akten werden nicht mehr als Ziel angeboten, weder in den Auswahllisten von Schriftsatz,
    Dokumentgenerator noch Chat.
- **Runde 2:**
  - Die KI-Bearbeitung im Editor erzeugte noch einen Serverfehler und eine verwaiste Anmerkung. **Behoben:** Hinweis
    mit Status 422.
  - Der Dokumentgenerator nahm eine gelöschte Akte bei manipulierter Anfrage an; das war lokal und ohne Cloud-Aufruf.
    **Behoben.**
  - Bestätigt: Es gibt keinen weiteren Cloud-Pfad an `prepare_draft_context` vorbei. Alle drei Aufrufer von
    `gateway.prepare_request` laufen danach; der Chat ruft die Cloud nur über `DraftingService` auf.
- **Bewusst nicht geändert (Restpunkte, kein Cloud-Versand):**
  - Die Entwurfsliste zeigt weiterhin Entwürfe gelöschter Akten.
  - Der Chat zu einer gelöschten Akte zeigt die allgemeine Fehlermeldung statt „Akte gelöscht“.
  - Das E-Mail-Matching und „Akte zuordnen“ im Chat können eine gelöschte Akte treffen; nachfolgende KI-Aktionen
    werden dann verweigert.
- **Tests:** Jeder neue Test schlägt ohne den jeweiligen Fix fehl und besteht mit ihm.
  - neue Datei `tests/test_privacy_pilot_decision.py`;
  - Ergänzungen in `tests/test_web_drafts_prompt24.py`, `tests/test_web_draft_editor.py`, `tests/test_web_chat.py`.

### 26.3 Risikomatrix

Legende Art: **T** = technischer Nachweis, **A** = Annahme (nicht geprüft), **O** = organisatorische Pflicht.
Status: PASS / FAIL / NICHT VERIFIZIERT / OFFEN (Entscheidung ausstehend). Freigabe-Auswirkung je Pilotstufe
**A** (synthetische Daten), **B** (ausdrücklich freigegebene Testakten mit geklärtem Umfang), **C** (echte Mandantendaten).

| Risiko | Auswirkung | Nachweis | Restrisiko | Maßnahme | Rolle | Status | Freigabe |
|---|---|---|---|---|---|---|---|
| Pseudonymisierung vor der Cloud, Fail-Closed, Siegel | Klartext an Anthropic | T: §20, volle Suite, Fälle 1–20 im installierten Build | Erkennung ist regel- und modellbasiert, nie vollständig | Vorschau vor dem Versand prüfen (§2.2a) | Anwender | PASS | A, B: ja; C: Voraussetzung erfüllt |
| Geburts-/Todesdaten | Identifizierung über exaktes Datum | T: §§23–25, Korpus 0/10, 0/12 | Ereignisdaten (Unfall, Auffinden, Beerdigung) bewusst lesbar; indirekte Rekonstruktion möglich | Kenntnisnahme; Anwender prüfen Vorschau | Kanzleileitung | PASS mit Restrisiko | C: Kenntnisnahme nötig |
| Kurze Nummern ohne Bezeichnung (≤ 4 Ziffern) | Wiedererkennung im Kontext | T: §22, §25.1 | „Zimmer 12“, „Personal 4711“ lesbar | Kenntnisnahme | Kanzleileitung | PASS mit Restrisiko | C: Kenntnisnahme nötig |
| Fristvorschläge | Geburts-/Todesdatum als Frist; Leck über Ausschnitt | T: §24, §25.1, §26.2 Nr. 2 | Fristen bleiben Vorschläge, keine automatische Bestätigung | Anwender bestätigen jede Frist | Anwender | PASS | – |
| Datenminimierung: ganze Akte je Anfrage | Mehr Daten als nötig in der Cloud (pseudonymisiert) | T: bis 30 Dokumente/je 30 000 Zeichen, zusammen max. 90 000 Zeichen (`local_ai_provider.py`) | Umfang nicht frageabhängig | Produktentscheidung: Dokumentauswahl je Anfrage | Produktverantwortung | OFFEN | C: Kenntnisnahme nötig |
| Löschung: Akten/Dokumente nur Soft-Delete, Dateien bleiben; Chat-Löschung lässt Dokumente, Embeddings, Feedback-Statistik | Recht auf Löschung (Art. 17) technisch nicht vollständig umsetzbar | T: `matters_router.py`, `documents/lifecycle.py`, `chat_router.py` | Echte Löschung nur manuell; Audit-Einträge zu Akten/Mandanten enthalten Namen | Lösch-/Aufbewahrungskonzept der Kanzlei, danach technischer Löschpfad | Kanzleileitung, Datenschutz | OFFEN | C: **No-Go** ohne Löschkonzept |
| Gelöschte Akte in Cloud-Anfragen | Daten einer gelöschten Akte an die Cloud | T: §26.2 Nr. 1 | – | behoben | – | PASS | – |
| Automatische Löschfrist | Scheinfunktion | T: §26.2 Nr. 5 | Keine automatische Löschung | Konzept (Prompt 35, offen) | Produktverantwortung | PASS (ehrlich beschriftet), Funktion OFFEN | C: Teil des Löschkonzepts |
| Backups | Unverschlüsselte ZIPs ohne Rotation (`backup/service.py`) | T | Verlust/Diebstahl des Datenträgers | Verschlüsselter Speicherort, Aufbewahrung festlegen | Kanzlei-IT | OFFEN | B: Speicherort festlegen; C: **No-Go** ohne Regel |
| Mandantentrennung | Kontext einer Akte in einer anderen | T: Chats nutzerbezogen, Kontext aktenbezogen (Tests) | Wissensbasis (Korrektur §27.4: keine Schaltfläche, tatsächlicher Weg ist der manuelle Import; Namen anderer Akten werden seit §27 nicht mehr gesendet): „Als Wissen übernehmen“ kopiert einen rekonstruierten Entwurf kanzleiweit (`feedback/service.py`), wird aktenübergreifend gefunden | Nur bewusst und ohne Mandantenbezug übernehmen; Produktentscheidung: Prüfung vor Übernahme | Anwender, Produktverantwortung | OFFEN | C: Anweisung nötig |
| Feedback-Lernen | Rückschluss auf Inhalte | T: HMAC mit installationseigenem Schlüssel, nichts an die Cloud | Statistiken werden nie gelöscht | Teil des Löschkonzepts | Produktverantwortung | PASS mit Restrisiko | – |
| Protokolle | Dateinamen/Pfade, Fehlermeldungen | T: Watcher maskiert (§26.2 Nr. 4), Download gefiltert | Rotierende Logdateien auf dem Rechner ungefiltert; `str(exc)` in Fehlerzeilen; A: Zugriffsprotokoll mit Suchparametern | Zugriff auf das Datenverzeichnis beschränken | Kanzlei-IT | PASS mit Restrisiko | C: Zugriffsschutz nötig |
| API-Schlüssel auf dem Kanzlei-PC | Missbrauch des Kontos, Kosten | T: `.env` für alle lokalen Benutzer lesbar, keine Rotation (§25.3) | bis zur Gateway-Einrichtung bestehend | **Entscheidung 13.10.2026: Schlüssel später im Gateway; lokale Verwaltung bleibt unverändert** (26.4) | Kanzleileitung | OFFEN (zurückgestellt) | A: vertretbar mit Ausgabenlimit; B: Ausgabenlimit nötig; C: **No-Go** bis Gateway geprüft |
| Lexono-Gateway (P0-08) | Schlüssel- und Zugriffsschutz | – | nicht eingerichtet, nicht geprüft | Prüfung nach Servereinrichtung (26.4) | Kanzleileitung, Betreiber | NICHT VERIFIZIERT | C: **No-Go** |
| Echter Cloud-Aufruf mit genau diesem Build | Abweichung Test ↔ Betrieb | T: Live-Aufruf §21.7 auf früherem Stand, gleicher Pfad | aktueller Build nicht live geprüft | Ein synthetischer Live-Aufruf vor Stufe B | Betreuer Pilot | NICHT VERIFIZIERT | B: nachholen |
| Auftragsverarbeitung/Drittland (Anthropic) | Rechtsgrundlage | – | – | Prüfung durch die Kanzlei | Kanzleileitung, Datenschutz | OFFEN | B: abhängig vom Umfang; C: **No-Go** ohne |
| Einweisung (§2.2a), Meldeweg, Art. 33 | Fehler unbemerkt | O: Anleitung vorhanden (§25.2) | Einweisung nicht nachgewiesen | Einweisung durchführen und protokollieren | Kanzleileitung | OFFEN | B, C: nötig |
| Vollständige Leckfreiheit | – | nicht belegbar | – | – | – | NICHT VERIFIZIERT | wird nicht zugesagt |

### 26.4 P0-08: Entscheidung vom 13.10.2026 (ersetzt die Auswahl (A)/(B) aus §25.3)

- Der Anthropic-Schlüssel soll **später im Lexono-Gateway** hinterlegt werden (Richtung (A) aus §25.3).
- P0-08 bleibt zurückgestellt: das Gateway wird jetzt **weder implementiert noch konfiguriert noch aktiviert**.
- Die lokale API-Schlüsselverwaltung wird **nicht geändert** (keine Änderung an `.env`, Rechten oder Rotation).
- Gateway-Funktion, Sicherheit und Integration werden **nach der Servereinrichtung separat geprüft**. Eine solche Prüfung
  hat **nicht** stattgefunden.
- Offene Voraussetzung für Stufe C, Abschlussnachweis: Prüfprotokoll gegen den echten Server mit den Punkten 1–8 aus
  §25.3 (A) und eigener P0-08-Abnahme in `LEXONO_MASTER_PRODUCT.md`.
- Bis dahin liegt der Schlüssel im Direct-Key-Pfad auf dem Kanzlei-PC (Feststellungen §25.3). Die Voraussetzungen (B)
  aus §25.3 gelten nur noch, falls die Kanzlei diese Entscheidung ausdrücklich ändert.

### 26.5 Pilotstufen

**Stufe A – synthetische Daten**
- Voraussetzungen: installierter Build; nur synthetische Akten; Anthropic-Ausgabenlimit beim Anbieter.
- Nachweise: volle Suite, Fälle 1–20 im installierten Build, §26.2.
- Restrisiken: Schlüssel auf dem Rechner (Missbrauch des Kontos, keine Mandantendaten betroffen).
- Entscheidung: Kanzleileitung bestätigt, dass nur synthetische Daten verwendet werden.

**Stufe B – ausdrücklich freigegebene Testakten**
- Voraussetzungen:
  1. Umfang schriftlich festgelegt: welche Akten, Herkunft, ob Personenbezug enthalten ist, Zeitraum, beteiligte Personen.
     Nachweis: unterschriebene Liste.
  2. Einweisung §2.2a aller Beteiligten. Nachweis: Teilnahmeprotokoll.
  3. Meldeweg benannt. Nachweis: Name/Funktion in PILOT_CHECKLIST J.
  4. Ein synthetischer Live-Aufruf mit genau diesem Build. Nachweis: Datum, Build-Commit, Ergebnis im Protokoll.
  5. Backups an festgelegtem, zugriffsgeschütztem Ort. Nachweis: Ort und Zugriffsliste.
  6. Enthalten die Testakten Personenbezug: Prüfung der Übermittlung an Anthropic. Nachweis: Vermerk der Kanzlei.
- Restrisiken: wie Stufe A, dazu §26.3 (Ereignisdaten, kurze Nummern, indirekte Rekonstruktion, ganze Akte je Anfrage).
- Entscheidung: Kanzleileitung, gegebenenfalls mit Datenschutzbeauftragter/m.

**Stufe C – echte Mandantendaten: derzeit No-Go**
- Voraussetzungen (alle offen):
  1. Gateway eingerichtet und geprüft (26.4).
  2. Auftragsverarbeitung/Drittland geprüft.
  3. Lösch- und Aufbewahrungskonzept einschließlich Backups.
  4. Zugriffsschutz auf `C:\ProgramData\Lexono` (Logs, Datenbank, Dateien).
  5. Anweisung zur Wissensübernahme.
  6. Kenntnisnahme der Restrisiken.
  7. Stufe B ohne offene Datenschutzbefunde abgeschlossen.
- Nachweise: je Punkt ein schriftlicher Vermerk bzw. Prüfprotokoll.
- Entscheidung: Kanzleileitung und Datenschutzbeauftragte/r. Ein Go für A oder B ist kein Go für C.

### 26.6 QA und Nachweise

**Tests**

| Prüfung | Ergebnis |
|---|---|
| Neue bzw. ergänzte Regressionstests | jeder ohne Fix rot, mit Fix grün |
| Betroffene Testdateien (Chat, Entwurf, Editor, Schriftsatz, Dokumentgenerator, Watcher, Einstellungen, Privacy) | bestanden |

**Volle Testsuite.** Der Gesamtlauf wurde bei etwa 89 % wegen Speicherknappheit des Systems abgebrochen. Er ist danach
nicht als Ganzes neu gelaufen; die Abdeckung ergibt sich so:
- **Bis 89 %:** nur die 4 bekannten `test_design_refresh`-Fehler (fehlende Logos, fremde Änderung), dazu 2 Fehler im
  neuen Watcher-Test.
- **Diese 2 Fehler** kamen nicht aus dem Fix, sondern aus der Testisolation: Alembics `fileConfig`
  (`test_migrations.py`) deaktiviert bestehende Logger. Gegenprobe: mit `test_migrations.py` davor schlug der Test
  vorher fehl und besteht jetzt; der Test setzt den Logger nun ausdrücklich.
- **Restliche Dateien ab `test_web_inbox.py`:** gezielt nachgeholt, 535 bestanden. Das schließt
  `test_web_knowledge.py` ein; der dort bekannte zeitabhängige Test bestand diesmal.

**Installierter Build (Stand dieser Änderungen)**
- Installer neu erstellt und still installiert. Das Bundle enthält die neuen Funktionen (Bytecode-Prüfung, Vorlage).
- **Fall 21 (neu), QA-Datenbank, synthetische Akte:**
  - Gegenprobe: die Entwurfsprüfung bei aktiver Akte erzeugt einen Cloud-Aufruf.
  - Nach dem Löschen der Akte:
    - Prüfung → Hinweis „Die Akte wurde gelöscht …“, kein Serverfehler, kein Cloud-Aufruf;
    - Chat → blockierte Antwort, kein Cloud-Aufruf;
    - die Akte wird nicht mehr als Ziel angeboten.
  - Danach wurde die Akte zurückgesetzt. **PASS**
- **Fall 20 (Regression):** PASS.
- **Chat bei aktiver Akte:** über die Oberfläche geprüft, Antwort über den Fang-Server. Zwei frühere Leerläufe gingen
  auf parallele, abgebrochene Anfragen des Prüfskripts zurück; über den Rohstream und erneut über die Oberfläche war
  das Verhalten korrekt.
- **Normale Instanz** wiederhergestellt: nur Port 8000, Health 200.
- **Echter Anthropic-Aufruf mit diesem Build:** NICHT VERIFIZIERT; Voraussetzung für Stufe B (26.5).

### 26.7 Go/No-Go je Pilotstufe (Stand dieses Commits)

| Stufe | Technisch | Organisatorisch | Ergebnis |
|---|---|---|---|
| **A** synthetische Daten | PASS (Suite, installierter Build, Fälle 1–21) | Bestätigung „nur synthetisch“, Ausgabenlimit: OFFEN | **Go möglich**, sobald die Kanzlei die zwei Punkte bestätigt |
| **B** freigegebene Testakten | PASS mit Restrisiken (§26.3); Live-Aufruf mit diesem Build NICHT VERIFIZIERT | Umfang, Einweisung, Meldeweg, Backup-Ort, ggf. Übermittlungsprüfung: OFFEN | **No-Go bis zu den Nachweisen aus 26.5 B**; danach Entscheidung der Kanzlei |
| **C** echte Mandantendaten | Gateway NICHT VERIFIZIERT; Löschpfad, Backups, Zugriffsschutz OFFEN | AVV/Drittland, Löschkonzept, Anweisung Wissensübernahme: OFFEN | **No-Go** |

Diese Vorlage behauptet **keine** vollständige Leckfreiheit, **keine** Rechtskonformität und **keine**
Echtdatenfreigabe.


## 27. Datenminimierung, Löschung, Protokolle und Wissensbasis (13.10.2026, Ausgang `47ef827`)

Nur synthetische Daten. Diese Prüfung ist **keine** Freigabe echter Mandantendaten und keine rechtliche Bewertung. Es gelten
weiter die Pilotstufen aus §26.5: A nur synthetisch nach den Kanzleibestätigungen, B No-Go bis zu den Nachweisen, C No-Go.
P0-08 (Gateway) bleibt zurückgestellt; die lokale Schlüsselverwaltung ist unverändert.

Drei unabhängige Bestandsanalysen (Cloud-Inhalte je Funktion, Löschlebenszyklus, Protokolle/Wissensbasis) wurden mit
synthetischen Skripten gegen den echten Code reproduziert; die Befunde unten sind dort belegt.

### 27.1 Datenminimierung: was je KI-Funktion an die Cloud geht

**Ursache für „ganze Akte je Anfrage“:** `LocalAIProvider.prepare_draft_context(matter_id, db)` kennt nur die Akte, nicht
die Dokumente einer Anfrage. `_build_sachverhalt` nimmt bis zu 30 nicht gelöschte Dokumente mit Text, neueste zuerst, je
Dokument bis 30 000 Zeichen, zusammen höchstens 90 000. Angehängte Dokumente (`required_document_ids`) dienen nur als Gate
(blockiert, wenn nicht lesbar), nicht zur Auswahl. Das ist im Code ausdrücklich so beschrieben (`drafting/service.py`).

| Funktion | Sachverhalt | Fristhinweise | Quellen | Weiteres |
|---|---|---|---|---|
| Chat (Antwort/Entwurf) | „Akte: Titel“ + alle Aktendokumente als `[Dokumenttyp] Auszug` | alle Fristen außer abgelehnten und Geburts-/Todesdaten | Rechtsrecherche + bis zu 10 freigegebene Wissenseinträge | Nachricht; Verlauf bis 10 Nachrichten/12 000 Zeichen |
| Dokumentgenerator, Neugenerierung, Anmerkung übernehmen | ganze Akte | wie oben | wie oben | Anweisung; der bisherige Entwurf wird nicht gesendet |
| Editor-KI-Bearbeitung | ganze Akte | wie oben | wie oben | Anweisung + markierter Text (nicht der übrige Entwurf) |
| Entwurfsprüfung | nur der Entwurf | keine | nur Quellentitel | Aktendaten nur lokal für die Platzhalter |

Nicht gesendet werden Dateinamen, Upload- und Dokumentdaten sowie das Fälligkeitsdatum einer Frist (nur deren Textstelle);
der Dokumenttyp wird gesendet. Inhalte anderer Akten wurden im Aktenkontext nicht gefunden (Tests
`test_context_never_contains_data_from_other_matter`, `test_review_never_touches_other_matter`) – **aber** über die
Wissensbasis (27.4).

**Behoben (sicher, ohne Sachverhaltsverlust, mit Test):**

| # | Befund (reproduziert) | Fix |
|---|---|---|
| M1 | Doppelt hochgeladenes Dokument (gleicher Text) ging zweimal hinaus und nahm anderen Dokumenten Budget | nur das neueste Exemplar senden; alle IDs gelten weiter als einbezogen (keine neue Blockade bei angehängtem Duplikat) |
| M2 | Frist aus einem gelöschten Dokument ging weiter an die Cloud, obwohl dessen Text nicht mehr gesendet wird | weglassen (wie gelöschtes Dokument, §26-Regel für gelöschte Akten und abgelehnte Fristen) |

**Nicht geändert – Produktentscheidung nötig (Optionen, kein pauschales Kürzen):**

| # | Befund | Option | Risiko |
|---|---|---|---|
| M3 | Frage zu einem angehängten Dokument sendet auch alle anderen Aktendokumente | angehängte Dokumente vollständig, übrige nur gekürzt oder nur mit Typ | Fragen, die Dokumente verknüpfen („passt die Rechnung zum Vertrag?“), werden schlechter; Chronologie über Dokumente geht verloren |
| M4 | Editor-Bearbeitung einer Markierung sendet die ganze Akte | Akte gekürzt, dafür Entwurfsumfeld | schlechtere Formulierung ohne Aktenbezug |
| M5 | allgemeine Frage in einer Akten-Unterhaltung sendet die Akte | Akte nur bei Aktenbezug | der Aktenbezug ist ohne Inhalt nicht sicher erkennbar (Fund 13.10.2026, `chat/service.py`) – falsche Antworten möglich |
| M6 | erledigte/stornierte Fristen werden gesendet; Fälligkeitsdatum nicht | Status mitsenden bzw. erledigte weglassen | Verlust der Fristhistorie |

Empfehlung: M3 als erste Stufe (angehängte Dokumente voll, übrige mit Anfang/Ende und markierten Stellen wie Beträge,
Daten, Fristen), mit A/B-Messung der Antwortqualität auf synthetischen Akten vor einer Umstellung.

### 27.2 Löschung: Lebenszyklus je Datenart (Stand nach diesem Commit)

| Datenart | Löschung | Bleibt physisch | Danach noch erreichbar | Backup/Wiederherstellung | Zugriff |
|---|---|---|---|---|---|
| Akte | weich (`deleted_at`) | Datensatz und alle Kinddaten | **jetzt nicht mehr:** Dokumentansicht/-download, globale Suche, REST-API, KI-Kontext (§26). Weiter: Entwürfe (Liste/Detail/Export), Posteingang | im DB-Backup; Wiederherstellung setzt alten Stand zurück | angemeldete Nutzer, CSRF |
| Mandant | endgültig, nur ohne Akten (auch gelöschte zählen) | Audit-Eintrag mit Name | – | Wiederherstellung bringt ihn zurück | `PERM_CLIENT_DELETE` |
| Dokument | weich, wiederherstellbar | Datensatz, Datei, Embedding | nicht über Ansicht/Download/Suche/KI | Dateien nur aus `intake`/`mail_attachments` gesichert | angemeldete Nutzer |
| Unterhaltung | endgültig mit Nachrichten und Rückfrage-Entscheidungen | Anhänge (Dokument + Datei) bleiben in der Akte | Anhänge über Akte, Suche, Download (gewollt, Test) | `chat_uploads/` **nicht** im Backup | nur eigene Unterhaltungen |
| Entwurf | kein Löschen (nur Ablehnen/Verwerfen) | alles | Listen, Detail, Exporte, API (außer gelöschter Akten) | im DB-Backup | angemeldete Nutzer |
| Embedding | nie | Vektor | nicht abrufbar (gelöschte Dokumente gefiltert) | im DB-Backup | – |
| Rückfrage-Entscheidung | mit der Unterhaltung | aktenbezogene bleiben | – | im DB-Backup | – |
| Feedback-Statistik | nie | HMAC des Begriffs | – | im DB-Backup | – |
| Wissenseintrag | nur deaktivieren | kopierter Text ohne Aktenbezug | kanzleiweit (27.4) | im DB-Backup | Lesen: alle Angemeldeten (`/api/knowledge`) |
| Audit-Eintrag | nie | Titel, Az., Mandantenname bei Akten-/Mandantenlöschung | Audit-Ansicht | im DB-Backup | – |
| Fehlerzeile, E-Mail, Postausgang | kein Löschpfad | alles | Fehlerseite (maskiert), Posteingang | im DB-Backup | – |
| Protokolle | Rotation 5 × 5 MB; `app.log` ohne Rotation | lokal ungefiltert | Download gefiltert (nur aktive Datei) | nicht gesichert | Dateisystem |
| Backup | ZIP unverschlüsselt, keine Rotation | DB, `intake`, `mail_attachments`, Einstellungen | – | Wiederherstellung überschreibt die DB, Verzeichnisse nur additiv; `*.pre-restore-*.bak` bleibt unverschlüsselt | Admin; Wiederherstellung nur per Kommandozeile |
| „Löschfrist“ (`retention_days`) | **keine Wirkung**: wird nur gespeichert und angezeigt, kein Löschlauf liest sie | – | – | – | Admin |

**Behoben (reproduziert, mit Test `tests/test_privacy_deletion_access.py`):**
- L1: Dokumente einer gelöschten Akte waren ansehbar und herunterladbar (Ansicht, Seitenbild, Download) → 404.
- L2: Die globale Suche fand Dokumente und E-Mails gelöschter Akten (mit Aktentitel) → gefiltert; nicht zugeordnete
  E-Mails bleiben auffindbar.
- L3: REST-API listete gelöschte Akten, deren Dokumente und Entwürfe → gefiltert bzw. 404.

### 27.3 Protokolle

**Behoben (mit Test):**
- P1: Datenbankfehler schrieben die gespeicherten Werte (`[parameters: ('…', 'erika.beispiel@…')]`) ins Log →
  `hide_parameters=True`; die SQL-Anweisung bleibt für die Fehlersuche sichtbar.
- P2: Das Zugriffsprotokoll schrieb Suchbegriffe (`?q=Erika%20Beispiel`) in `app.log` → Query-Teil wird durch
  `?[entfernt]` ersetzt.
- P3: Der Audit-Eintrag eines wiederhergestellten Intake-Fehlers enthielt den vollen Pfad → maskiert wie beim Fehler.
- P4: Die Warnung einer fehlgeschlagenen Rechtsquellensuche enthielt den Traceback samt Suchbegriff → nur Fehlertyp.

**Offen:** rotierte Logdateien und `app.log` sind lokal ungefiltert; `app.log` rotiert nicht; Daten- und Logpfade enthalten
den Windows-Benutzernamen; bereits geschriebene alte Zeilen sind nicht bereinigt.

### 27.4 Wissensbasis („Als Wissen übernehmen“)

- Die Schaltfläche „Als Wissen übernehmen“ existiert in der Oberfläche **nicht**; `promote_to_knowledge` hat keinen Aufrufer
  außer Tests. Tatsächlicher Weg: manueller Import unter Wissen (Admin/Anwalt) mit frei eingefügtem Text, Freigabe durch
  dieselben Rollen. Frühere Hinweise (§26.3, PILOT_CHECKLIST.md J, Stufe C) sind entsprechend korrigiert.
- Ein freigegebener Eintrag wird bei **jeder** Akte desselben Rechtsgebiets in die Cloud-Anfrage übernommen. Der Gateway kennt
  nur die Namen der eigenen Akte. **Reproduziert:** „Im Fall Zabelhuber …“ (Mandant einer anderen Akte) ging im Klartext
  hinaus. **Behoben:** Einträge, die Mandanten- oder Beteiligtennamen einer anderen Akte (voller Name oder Nachname bei
  Personen; Rechtsformen ausgenommen) enthalten, werden für diese Anfrage nicht verwendet (fail-closed, es entfällt nur der
  Eintrag). In der eigenen Akte bleibt der Eintrag nutzbar; dort maskiert der Gateway den eigenen Namen.
- **Grenzen:** Namen, die in keiner Akte als Mandant oder Beteiligter erfasst sind (z. B. Zeugen, Dritte im Text), Adressen
  und andere Merkmale fallen nicht unter diesen Filter; dort greifen weiter Erkennung und Restrisiko-Scan des Gateways.
- **Offen:** Einträge haben keinen Aktenbezug und überdauern die Löschung der Ausgangsakte; `/api/knowledge` zeigt allen
  Angemeldeten auch nicht freigegebene Einträge.

**Unabhängiges Review**

Keine Regressionen gefunden. Behoben wurden danach:
- **Wissensfilter umgehbar:** „Zabelhubers“ (Genitiv) und „ZABELHUBER“ wurden nicht erkannt. Jetzt werden auch
  Großschreibung und Genitiv erkannt. Beliebige Kleinschreibung bewusst nicht, sonst schlösse ein Nachname wie „Lange“
  jedes „lange“ aus.
- **Zu viel ausgeschlossen:** Bei „Amtsgericht München“, „Stadt Köln“ oder „Bundesagentur für Arbeit“ wurde das letzte
  Wort als Nachname behandelt. Für Organisationen, Gerichte, Behörden und als Anwalt erfasste Beteiligte zählt jetzt nur
  der volle Name. „Nachname, Vorname“ wird richtig gelesen.
- **Freigabe:** Ein Entwurf einer gelöschten Akte konnte noch freigegeben und in den Postausgang übergeben werden. Das
  wird jetzt verweigert.
- **Bewusst offen (lokal, kein Cloud-Versand):**
  - Entwurfsliste, -detail und Export gelöschter Akten.
  - Posteingang-Detail für E-Mails gelöschter Akten.
  - Das Duplikat zählt weiter gegen die Grenze von 30 Dokumenten; es wird aber nur einmal gesendet.
  - Wissenseinträge werden ohne Hinweis an die Anwender ausgelassen.
  - Kleingeschriebene Namen werden nicht erkannt.
  - Namen, die zugleich häufige Wörter sind („Kraft“ als Gegner), schließen einen Eintrag aus, der dieses Wort
    großgeschrieben enthält. Es entfällt dann nur der Eintrag (fail-safe).
  - Die Datenbank des Gateway-Codes (`gateway/`, P0-08, zurückgestellt) ist nicht geändert.

### 27.5 Lösch- und Aufbewahrungskonzept (Vorschlag; Fristen legt die Kanzlei fest)

Gesetzliche Aufbewahrungsfristen werden hier **nicht** festgelegt. Rollen sind Funktionen, keine benannten Personen.

| Nr. | Maßnahme | Entscheidung/Rolle | Abschlussnachweis |
|---|---|---|---|
| K1 | Aufbewahrungsfrist je Datenkategorie (Akte, Dokument, Unterhaltung, Entwurf, E-Mail, Audit, Protokoll, Backup) festlegen | Kanzleileitung mit Datenschutzbeauftragter/m | schriftliche Tabelle mit Frist und Rechtsgrundlage je Kategorie |
| K2 | Endgültiger Löschpfad für Akten nach Fristablauf (Datensätze, Dateien, Embeddings, Entscheidungen, Entwürfe, Anhänge, Wissenseinträge mit Bezug), mit inhaltsfreiem Löschnachweis | Produktverantwortung | Test je Tabelle: nach Löschlauf keine Zeile/Datei mehr; Prüfprotokoll mit synthetischer Akte |
| K3 | „Löschfrist“-Einstellung erst dann als wirksam darstellen, wenn K2 mit Zeitsteuerung umgesetzt und getestet ist | Produktverantwortung | Test: Akte älter als Frist wird gelöscht, jüngere bleibt |
| K4 | Backups verschlüsseln, Speicherort und Rotation festlegen; Löschung wirkt auf Backups erst mit deren Ablauf (dokumentieren) | Kanzlei-IT | Ort, Verschlüsselung, Aufbewahrungsdauer schriftlich; Testwiederherstellung |
| K5 | Wiederherstellung: Hinweis, dass gelöschte Daten zurückkehren; danach erneuter Löschlauf; `pre-restore`-Kopie entfernen | Kanzlei-IT, Produktverantwortung | Ablaufbeschreibung + Test |
| K6 | Unterhaltungsanhänge, Schriftsatz-Uploads, Kanzleiprofil-Dateien ins Backup oder bewusst ausschließen | Produktverantwortung | Backup-Inhaltsliste |
| K7 | Audit-Einträge ohne Namen (nur IDs) oder Frist für Audit festlegen | Kanzleileitung, Produktverantwortung | Test/Stichprobe |
| K8 | Wissenseinträge: Aktenbezug speichern, bei Aktenlöschung deaktivieren; Lesen nicht freigegebener Einträge beschränken | Produktverantwortung | Migration + Test |
| K9 | Zugriff auf `C:\ProgramData\Lexono` (DB, Dateien, Protokolle, `app.log`) auf berechtigte Konten beschränken; `app.log` rotieren | Kanzlei-IT | ACL-Auszug ohne Inhalte; Rotation belegt |

K1 ist Voraussetzung für K2/K3. K1–K9 sind Voraussetzungen für Stufe C (§26.5 C 3–4).

### 27.6 QA und Nachweise

**Tests**

| Prüfung | Ergebnis |
|---|---|
| Neue Regressionstests | rot ohne Fix, grün mit Fix |
| Betroffene Testdateien | bestanden |
| Volle Testsuite | **4261 bestanden**, 1 übersprungen, 6 xfailed, 1 xpassed |

- Neue Regressionstests:
  - `tests/test_privacy_minimisation_and_logs.py`: 16 Tests;
  - `tests/test_privacy_deletion_access.py`: 9 Tests;
  - Freigabe-Sperre in `tests/test_web_drafts_prompt24.py`.
- Fehlgeschlagene Tests in der vollen Suite (5):
  - 4 bekannte `test_design_refresh`-Fehler (fehlende Logos, fremde Änderung);
  - 1 neuer Test, der nur im Gesamtlauf fehlschlug: `test_migrations.py` (Alembic) deaktiviert bestehende Logger,
    derselbe Effekt wie in §26.6. Gegenprobe mit `test_migrations.py` davor: vorher rot, jetzt grün.
- Der bekannte zeitabhängige Test bestand.

**Installierter Build (Stand dieser Änderungen)**
- Installer neu erstellt und installiert. Das Bundle enthält die neuen Funktionen (Bytecode- und Konstantenprüfung).
- **Fall 22 (neu)**, QA-Datenbank, synthetische Akten und Wissenseinträge, Fang-Server statt Anthropic: **PASS**.
  - Ein freigegebener Eintrag „Im Fall Zabelhuber …“ (Mandant einer anderen QA-Akte) ging nicht in die Anfrage.
  - Der neutrale Eintrag ging hinaus (Gegenprobe).
  - Das dreifach vorhandene Mahnschreiben ging einmal hinaus: 2 statt 4 Dokumenteinträge.
  - Nach dem Löschen der Akte:
    - Download → 404;
    - nicht in globaler Suche und REST-API;
    - Gegenprobe bei aktiver Akte: 200 bzw. gefunden.
  - Zustand danach zurückgesetzt; Einträge deaktiviert.
- **Negativbefund aus einem Zwischenlauf:** Der Download lieferte 200, weil die WebView2 eine zuvor geladene Datei aus dem
  HTTP-Cache bediente (mit `cache: no-store`: 404). Das ist ein Restrisiko, kein Serverfehler: einmal angesehene Dateien
  können lokal im WebView2-Cache liegen.
- **Fall 20 (Regression):** PASS.
- **Normale Instanz** wiederhergestellt: nur Port 8000, Health 200.
- **Echter Anthropic-Aufruf mit diesem Build:** NICHT VERIFIZIERT; das ist weiterhin eine Voraussetzung für Stufe B.

### 27.7 Bewertung

| Punkt | Status |
|---|---|
| Ursache „ganze Akte je Anfrage“ ermittelt, Inhalte je Funktion dokumentiert | PASS |
| Sichere Minimierung: Duplikate, Fristen gelöschter Dokumente | PASS |
| Anfragebezogene Dokumentauswahl (M3–M6) | NICHT UMGESETZT, Produktentscheidung |
| Pseudonymisierung, Fail-Closed und Gates unverändert (volle Suite, Fälle 20, 22) | PASS |
| Gelöschte Akten: Dokumente, Suche, API, KI-Kontext, Freigabe | PASS |
| Endgültige Löschung, wirksame Löschfrist | FAIL (nicht vorhanden; Konzept 27.5) |
| Protokolle: SQL-Werte, Suchbegriffe, Pfade, Tracebacks | PASS für die gefundenen Stellen |
| Lokale Logdateien gefiltert bzw. zugriffsgeschützt | FAIL / offen (K9) |
| Wissensbasis: Namen anderer Akten | PASS mit Grenzen (27.4) |
| Wissensbasis: Aktenbezug, Löschung | FAIL / offen (K8) |
| Vollständige Leckfreiheit | NICHT VERIFIZIERT |

Die Pilotstufen bleiben unverändert (§26.7): A nach den Kanzleibestätigungen, B No-Go bis zu den Nachweisen, C No-Go.


## 28. Risikobasierter Plan von Stufe B Richtung Stufe C und Marktreife (13.10.2026, Ausgang `5050d59`)

Nur synthetische Daten. Dieser Plan ist **keine** Freigabe. Stufe B bleibt No-Go, bis die Nachweise aus §26.5 B
vorliegen; Stufe C bleibt No-Go, bis technische, vertragliche und organisatorische Voraussetzungen nachgewiesen und
Freigaben dokumentiert sind. P0-08 (Gateway) bleibt zurückgestellt, die lokale Schlüsselverwaltung ist unverändert.
Gesetzliche Fristen, verantwortliche Personen und Freigaben werden hier nicht festgelegt; Rollen sind Funktionen.

### 28.1 Bestand (gegen den Code geprüft, nicht nur gegen die Doku)

Unabhängige Analyse der Kandidaten mit synthetischen Skripten:
- **Browser-/WebView2-Cache:** keine einzige Antwort mit Akten-, Dokument- oder Chatinhalt trug `Cache-Control`; der
  Download sendete `ETag`/`Last-Modified` (gemessen: `cache-control: None` auf Aktenseite, Dokumentansicht, Seitenbild,
  Download, Chat, Entwürfe, PDF-Export, API). Die WebView2 läuft laut pywebview-Quelltext im InPrivate-Modus mit einem
  temporären Profil unter `%TEMP%`, das beim Schließen gelöscht wird (nicht am installierten Build verifiziert); innerhalb
  einer Sitzung lieferte sie nach dem Löschen der Akte eine Datei aus dem Cache (§27.6).
- **Endgültige Löschung:** nicht vorhanden; Akten nur weich gelöscht (dokumentierte Aufbewahrungsentscheidung). Betroffen
  wären rund 25 Tabellen und die Dateien in `intake/`, `mail_attachments/`, `chat_uploads/`, `schriftsatz_uploads/`;
  SQLite erzwingt keine Fremdschlüssel, jede Löschung müsste ausdrücklich erfolgen. Kein Code löscht Dokumentdateien.
- **Weitere Reste für Stufe B:** Originale im überwachten Scan-Ordner (Intake kopiert nur); `chat_uploads/` nicht im Backup;
  `*.pre-restore-*.bak` unverschlüsselt neben der DB; Export-ZIPs in `%TEMP%\lexono_dashboard_exports` bei Absturz.
- Datenminimierung, Wissensbasis, Backups, Protokolle: Stand §27.

### 28.2 Umgesetzt: keine Zwischenspeicherung sensibler Antworten

**Priorität und Begründung:**
- einziger gemessener Restpfad, der **keine offene Kanzleientscheidung** voraussetzt;
- wirkt sofort für Stufe B: nach dem Löschen oder Sperren bleiben keine angesehenen Dokumente im Cache abrufbar;
- klein, klar abgegrenzt, ohne Einfluss auf Cloud-Payloads, Pseudonymisierung, Gates oder Sachverhalt.

Die endgültige Löschung (W2) wäre für „nach Stufe B wieder entfernbar“ wichtiger, kehrt aber eine dokumentierte
Produktentscheidung um und hängt von K1/K7 ab – nach CLAUDE.md wird sie daher als Entscheidung vorgelegt, nicht
eigenmächtig gebaut.

**Änderung:** `app/main.py` – reine ASGI-Middleware: jede Antwort außer `/dashboard/static/` erhält
`Cache-Control: no-store` und `Pragma: no-cache`; `ETag` und `Last-Modified` entfallen. Streams (SSE), Hintergrundaufgaben
nach dem Senden und Range-Anfragen bleiben unverändert; statische Dateien bleiben cachebar.

**Getrennt geprüft:**

| Aspekt | Ergebnis |
|---|---|
| Leck (lokal) | behoben: kein Cache-Eintrag für sensible Antworten; installierter Build: Download nach dem Löschen 404 **ohne** `no-store` im Abruf |
| Cloud-Payload | unverändert (keine Änderung an Gateway/Kontext) |
| Fehlalarm/Blockade | keine: alle Seiten, Downloads, Streams funktionieren (volle Suite, Fälle 20, 23) |
| Sachverhaltsverlust | keiner |
| Leistung | statische Dateien unverändert cachebar; Seiten werden ohnehin dynamisch erzeugt |

**Unabhängiges Review:** keine blockierenden Befunde; SSE weiterhin inkrementell, Hintergrundaufgaben laufen, Range 206,
304 für statische Dateien, keine Pfadumgehung. Gering: unbehandelte 500-Antworten (generischer Text ohne Aktendaten)
erhalten kein `no-store`, weil Starlettes Fehler-Middleware außen liegt.

### 28.3 Roadmap (Arbeitspakete nach Risiko und Abhängigkeit)

Legende Rolle: **K** Kanzleileitung, **DSB** Datenschutzbeauftragte/r (falls bestellt), **IT** Kanzlei-IT, **P**
Produktverantwortung/Entwicklung. Status: ERLEDIGT / OFFEN / ENTSCHEIDUNG OFFEN.

**Vor Stufe B (nächstes Zwischenziel)**

| WP | Inhalt | Abnahmekriterium / Nachweis | Tests | Rolle | Status |
|---|---|---|---|---|---|
| B1 | Keine Zwischenspeicherung sensibler Antworten | `no-store` auf allen nicht-statischen Antworten; Download nach Löschen 404 im installierten Build | `test_privacy_no_store_headers.py`, Fall 23 | P | **ERLEDIGT** (§28.2) |
| B2 | Synthetischer Live-Aufruf mit genau dem installierten Build | Protokoll nach 28.4 | Fall 20 gegen echte API | P, K (Zustimmung zum Versand synthetischer Daten) | OFFEN |
| B3 | Umfang der Testakten | unterschriebene Liste: Akten, Herkunft, Personenbezug, Zeitraum, Beteiligte | – | K | ENTSCHEIDUNG OFFEN |
| B4 | Einweisung §2.2a, Meldeweg Art. 33 | Teilnahmeprotokoll, benannte Funktion | – | K | OFFEN |
| B5 | Backup-Ort zugriffsgeschützt | Ort, Zugriffsliste | – | IT | OFFEN |
| B6 | Übermittlung an Anthropic bei Personenbezug geprüft | Vermerk | – | K, DSB | ENTSCHEIDUNG OFFEN |
| B7 | Aufräumplan nach Stufe B | Checkliste (PILOT_CHECKLIST J) abgearbeitet und protokolliert: Akten gelöscht, Scan-Ordner-Originale, `chat_uploads/`, `schriftsatz_uploads/`, `*.pre-restore-*.bak`, Export-Reste in `%TEMP%`, Backups der Phase | manuelle Prüfung | IT, P | OFFEN (bis W2 nur manuell) |

**Vor Stufe C (technisch)**

| WP | Inhalt | Abnahmekriterium | Tests | Rolle | Abhängigkeit | Status |
|---|---|---|---|---|---|---|
| W1 | Anfragebezogene Dokumentauswahl (§27.1 M3; **Chat mit Anhang umgesetzt, §29**) | angehängte Dokumente voll, übrige gekürzt; keine Verschlechterung der Antwortqualität auf synthetischen Akten (A/B-Messung, Fristen/Beträge/Chronologie vollständig) | Kontext- und Payload-Tests, Qualitätsbenchmark | P, K (Entscheidung) | – | ENTSCHEIDUNG OFFEN |
| W2 | Endgültige Löschung einer Akte (Datensätze aller ~25 Tabellen, Dateien, Embeddings, Entscheidungen, Chats/Anhänge), inhaltsfreier Löschnachweis | nach Löschlauf keine Zeile/Datei mehr (je Tabelle getestet); Wiederherstellungshinweis | je Tabelle + Dateien | P | K1, K7 | ENTSCHEIDUNG OFFEN |
| W3 | Löschfrist wirksam (Zeitsteuerung von W2) | Akte älter als Frist gelöscht, jüngere bleibt | zeitgesteuerter Test | P | W2, K1 | OFFEN |
| W4 | Backups verschlüsselt, rotiert; vollständiger Inhalt (inkl. `chat_uploads/` o. bewusster Ausschluss); Wiederherstellung ohne Rest-`.bak` | Testwiederherstellung, Schlüsselverwahrung dokumentiert | Backup-/Restore-Tests | IT, P | Kanzleientscheidung Schlüssel/Aufbewahrung | ENTSCHEIDUNG OFFEN |
| W5 | Aktenbezogenes Wissen (K8): Herkunftsakte speichern, bei Löschung deaktivieren; `/api/knowledge` nur freigegebene Einträge für Nicht-Admins | Migration + Tests | Wissens-/API-Tests | P | – | OFFEN |
| W6 | Dateirechte `C:\ProgramData\Lexono` (DB, Dateien, Protokolle, `app.log`), Rotation `app.log` | ACL-Auszug ohne Inhalte; Rotation belegt | Installer-Test | IT, P | berührt `.env` → gemeinsam mit P0-08 entscheiden | ENTSCHEIDUNG OFFEN |
| W7 | Prüfung ausgehender Payloads ausbauen: Wissensfilter auch für Zeugen/Dritte (Namen aus Dokumenten anderer Akten), Hinweis bei ausgelassenen Einträgen | Korpus-Messung: 0 fremde Namen, keine neue Blockade | Privacy-Korpus | P | – | OFFEN |
| W8 | Entwürfe/Posteingang gelöschter Akten ausblenden, 500-Antworten `no-store` | Tests | Web-Tests | P | – | OFFEN |
| W9 | P0-08 Gateway nach Servereinrichtung prüfen (Funktion, Sicherheit, Integration) | Prüfprotokoll gegen echten Server, P0-08-Abnahme | Gateway-Integrationstests | K, P, Betreiber | Server | ZURÜCKGESTELLT |

**Vor Stufe C (vertraglich/organisatorisch, alle bei der Kanzlei)**

| Nr. | Voraussetzung | Nachweis | Status |
|---|---|---|---|
| O1 | Auftragsverarbeitung/Drittlandübermittlung (Anthropic, ggf. Gateway-Betreiber) | geprüfte Verträge, Vermerk | ENTSCHEIDUNG OFFEN |
| O2 | Aufbewahrungsfristen je Datenkategorie (K1) | schriftliche Tabelle | ENTSCHEIDUNG OFFEN |
| O3 | Datenschutz-Folgenabschätzung bzw. Begründung, warum nicht erforderlich | Dokument | ENTSCHEIDUNG OFFEN |
| O4 | Verzeichnis der Verarbeitungstätigkeiten ergänzt | Eintrag | OFFEN |
| O5 | Berufsrechtliche Prüfung (Verschwiegenheit, Einbindung Dienstleister) | Vermerk | ENTSCHEIDUNG OFFEN |
| O6 | Stufe B ohne offene Datenschutzbefunde abgeschlossen | Abschlussbericht | OFFEN |

**Marktreife (nach Stufe C):** Mehr-Kanzlei-Trennung (FUTURE_ROADMAP P5), Bedrohungsmodell und Datenfluss-Register (P1),
kontinuierliche Sicherheitsvalidierung (P6), externe Prüfung (Penetrationstest), Installer mit festen Dateirechten.

### 28.4 Plan: synthetischer Live-Aufruf als Nachweis für Stufe B (B2, nicht ausgeführt)

1. Voraussetzung: Zustimmung der Kanzlei zum Versand **synthetischer** Daten an Anthropic; gültiger Schlüssel (lokal,
   unverändert); installierter Build = Commit dieses Abschnitts.
2. Normale Instanz ohne `ANTHROPIC_BASE_URL`/QA-Variablen; synthetische Akte aus Fall 20 (Nachlassfall).
3. Ablauf: Chatfrage mit synthetischem Dokument; Rückfrage-Karte bedienen; Antwort abwarten.
4. Nachweise: Datum/Uhrzeit, Build-Commit, Modell, HTTP-Status, Platzhalter im Ausgang (über das lokale API-Protokoll,
   nur Hash/Kennzahlen), Rückführung der Platzhalter in der Antwort, keine Klartextwerte aus PROTECT im Protokoll.
5. Ergebnis in PILOT_CHECKLIST J (B) eintragen; Fehlschlag dokumentieren, nicht wiederholen ohne Ursache.

### 28.5 QA und Bewertung

| Punkt | Status |
|---|---|
| Neuer Regressionstest `tests/test_privacy_no_store_headers.py` (11): ohne Fix 10 rot, mit Fix grün | PASS |
| Volle Testsuite: 4273 bestanden; nur die 4 bekannten `test_design_refresh`-Fehler (fehlende Logos, fremde Änderung); der bekannte zeitabhängige Test bestand | PASS |
| Build, Installation | PASS |
| Installierter Build, Fall 23 (neu): Download bei aktiver Akte 200 mit `no-store` ohne ETag; nach Löschen 404 ohne `no-store` im Abruf; Aktenseite 404; CSS weiter cachebar | PASS |
| Installierter Build, Fall 20 (Regression, Chat-Stream über Fang-Server) | PASS |
| Echter Anthropic-Aufruf mit diesem Build (B2) | NICHT VERIFIZIERT (geplant, 28.4) |
| WebView2-Profil wird beim Schließen gelöscht | NICHT VERIFIZIERT (aus pywebview-Quelltext abgeleitet) |
| Endgültige Löschung, Backup-Verschlüsselung, Dateirechte | FAIL / offen (W2, W4, W6) |
| Vollständige Leckfreiheit, Rechtskonformität | NICHT VERIFIZIERT, wird nicht behauptet |

Stufen unverändert: A nach den Kanzleibestätigungen; **B No-Go** bis B2–B7; **C No-Go** bis W1–W9 (soweit entschieden)
und O1–O6 nachgewiesen und Freigaben dokumentiert sind.


## 29. Datenminimierung pro KI-Anfrage: nur angehängte Dokumente (13.10.2026, Ausgang `41f7226`)

Nur synthetische Daten. Umsetzung von W1 (§28.3) in der kleinsten nachvollziehbaren Form; keine Freigabe. Stufe B
bleibt No-Go bis zu den Nachweisen, Stufe C No-Go. P0-08 und die lokale Schlüsselverwaltung sind unverändert.

### 29.1 Datenfluss bis zur Cloud-Payload (unabhängig nachverfolgt)

- Einziger Einstieg ist `DraftingService._prepare_and_gate` → `prepare_draft_context(matter_id)`: bisher **immer die ganze
  Akte** (bis 30 Dokumente/90 000 Zeichen). Angehängte Dokumente dienten nur als Gate.
- **Ausdrückliche Dokumentauswahl gibt es nur im Chat:** Anhang der aktuellen Nachricht, „Mit Dokument chatten“ (legt
  den Anhang an die erste Nachricht). Schriftsatz-Assistent, Neugenerierung, Anmerkung übernehmen, Editor-KI und
  Dokumentgenerator haben keine Auswahl und senden weiter die Akte (Schriftsatz-Assistent bewusst, §27.1).
- Nebenpfade geprüft:
  - Rückfrage-Vorschau und erneutes Senden nach der Entscheidung nutzen dieselbe Auswahl.
  - Lokale Sofortantworten (Normen, letzte Akte) gehen nicht in die Cloud.
  - Quellen und Wissen werden aus Aktenmetadaten gebildet, nicht aus Dokumenttext.
  - Weiter aktenweit sind die Fristhinweise (Textstelle der Frist, auch aus anderen Dokumenten) und der bisherige
    Gesprächsverlauf (frühere Antworten können andere Dokumente zitieren).

### 29.2 Umgesetzte Regel

- **Hat die aktuelle Chatnachricht Anhänge**, geht von den Aktendokumenten nur deren Text in den Sachverhalt, immer nur
  aus dieser Akte und nur nicht gelöschte Dokumente.
- Aktenweit bleiben: Fristhinweise, Quellen, bekannte Namen (Platzhalter bleiben konsistent) und Aktentitel.
- Die Antwort enthält im Hinweisbereich (nicht Teil des Schreibens, nicht im späteren Verlauf):
  „Für diese Anfrage wurde von den Aktendokumenten nur der Text des angehängten Dokuments an die Cloud-KI übermittelt;
  der Text von N weiteren Dokumenten dieser Akte wurde nicht berücksichtigt (Fristhinweise der Akte und der bisherige
  Gesprächsverlauf schon). …“
  Inhaltsgleiche Duplikate des Anhangs zählen nicht mit.
- **Ohne Anhang** in der aktuellen Nachricht, auch bei Folgefragen mit „Dokument“ oder „Anlage“: unverändert die ganze
  Akte, kein Hinweis.
- Pseudonymisierung, Rückfrage, Restrisiko-Scan, Siegel und Fail-Closed sind unverändert. Es entfällt nur Eingabetext
  vor dem Gateway.

**Review-Korrektur:** Die erste Fassung engte auch Folgefragen mit Dokumentwort auf den früheren Anhang ein. Dadurch
fehlte bei „Welche Dokumente der Akte erwähnen eine Kaution?“ das betreffende Schreiben, und der Rat im Hinweis war nicht
befolgbar. Seitdem schränken nur Anhänge der **aktuellen** Nachricht ein. Außerdem wurde der Hinweis präzisiert:
„Fristhinweise und Gesprächsverlauf schon“ statt „nur … übermittelt“, und Duplikate zählen nicht mit.

### 29.3 Messung (getrennt)

| Messung | Ganze Akte (bisher) | Gezielt (neu) |
|---|---|---|
| Testakte (4 Dokumente, Anhang = Rechnung): lokaler Sachverhalt / Payload in Zeichen | 360 / 749 | 111 / 497 |
| Analyse-Akte (5 Dokumente, Rechnung angehängt): Sachverhalt / Payload | 1326 / 1770 | 292 / 732 |
| Installierter Build, QA-Akte (Abnahmeprotokoll, 3× Mahnschreiben, Anhang): Payload | 4310 | 3709 |
| Fakten des Anhangs (Betrag, Zahlungsdatum) | vorhanden | vorhanden |
| Frist der Akte (aus anderem Dokument) | vorhanden | vorhanden |
| Fakten anderer Dokumente (Vertragspreis, Altschreiben, Mahnbetrag) | gesendet | nicht gesendet |
| Rückfragen / automatische Maskierungen / Blockaden | 0 / 0 / 0 | 0 / 0 / 0 |

Zum installierten Build: Der Unterschied ist dort kleiner, weil der Gesprächsverlauf dieser QA-Unterhaltung aktenweit
mitgeht.

- **Leck:** Die Pseudonymisierung ist unverändert, nur weniger Eingabetext. Ausgeschlossene Dokumente erreichen die
  Cloud nur über Fristhinweise und den Verlauf; das steht im Hinweis.
- **False Positives / Blockaden:** keine neuen. Weniger Text bedeutet tendenziell weniger Rückfragen. Ein angehängtes
  Dokument außerhalb der 30 neuesten wird jetzt einbezogen statt blockiert.
- **Sachverhaltsverlust:** gewollt bei angehängten Fragen, mit sichtbarem Hinweis. Für Fragen über mehrere Dokumente gilt
  die Regel: ohne Anhang fragen oder alle betreffenden Dokumente anhängen.
- **Antwortqualität:** mit echtem Modell NICHT VERIFIZIERT. Der Fang-Server antwortet nicht inhaltlich.

### 29.4 QA

| Punkt | Status |
|---|---|
| `tests/test_privacy_request_scope.py` (7 Tests): ohne Fix 4 rot (die übrigen sichern unverändertes Verhalten), mit Fix grün | PASS |
| `tests/test_chat_document_readiness.py`: bewusst geändert, Testdouble nimmt `document_ids` an | PASS |
| Volle Testsuite: 4280 bestanden, nur die 4 bekannten Logo-Fehler | PASS |
| Unabhängiges Review: Befunde zur Folgefrage, zum Hinweistext und zur Duplikatzählung behoben; keine Lücke bei Gates und Platzhaltern | PASS |
| Build und Installation | PASS |
| Installierter Build, Fall 24 (Anhang gezielt mit Hinweis, Folgefrage ohne Anhang mit ganzer Akte) | PASS |
| Installierter Build, Fall 20 (Regression) | PASS |
| Antwortqualität mit echtem Modell | NICHT VERIFIZIERT |
| Gezielte Auswahl für Editor, Neugenerierung, Schriftsatz | NICHT UMGESETZT (keine Auswahl vorhanden; Produktentscheidung) |
| Vollständige Leckfreiheit | NICHT VERIFIZIERT |

Fall 24 im Detail:
- **Anhang:** Rechnungsdaten gesendet, Abnahmeprotokoll und Mahnschreiben nicht im Sachverhalt, Hinweis „4 weitere
  Dokumente“ sichtbar.
- **Folgefrage ohne Anhang:** ganze Akte, kein Hinweis.

**Grenzen:**
- Eine automatische Relevanzauswahl ohne Anhang wurde bewusst nicht gebaut: unbelegt und mit Risiko für Fakten.
- Fristhinweise und Verlauf bleiben aktenweit.
- Spätere aktenbezogene Verwaltung von Dokumenten, Chats, Entwürfen und Wissen (W2, W5) ist davon unabhängig. Die Regel
  greift nur auf Dokumente derselben Akte zu.

## 30. Qualitätsbenchmark der Chat-Dokumentauswahl und Entscheidungsvorlage Editor/Neugenerierung (13.10.2026, Ausgang `89b547d`)

Nur synthetische Daten. Kein externer Modellaufruf: Der Live-Lauf ist vorbereitet, aber **nicht ausgeführt**, weil die
Freigabe (ausschließlich synthetische Daten **und** festgelegtes Ausgabenlimit beim Anbieter) nicht vorliegt.
Produktivcode ist unverändert; die Chatregel aus §29 gilt weiter. Stufe B bleibt No-Go, Stufe C No-Go.

### 30.1 Benchmark (versioniert)

- **Datensatz:** `benchmarks/chat_scope_v1/cases.json`
  - Version `v1`, Fälle `C01`–`C11` mit festen IDs.
  - Eine synthetische Akte: 5 Dokumente plus ein Duplikat, eine Frist, Mandant, Gegnerin, Adresse und IBAN.
  - Je Fall: Frage, Anhänge, benötigte Referenzfakten, erwartete Auslassungen in B, erwarteter Hinweis.
  - Mess- und Bewertungsregeln sind **vor** der Auswertung im Datensatz festgelegt (`rules`), mit Nennern und Grenzen.
- **Skript:** `scripts/bench_chat_scope.py`.
  - Läuft über den echten Produktionspfad `ChatService.send_message` → `DraftingService` → `ClaudePrivacyGateway`.
  - Gemessen wird der Payload, den der Schreib-Provider **unmittelbar vor dem Cloud-Aufruf** erhält: nach
    Pseudonymisierung, Rückfrage, Restrisiko-Prüfung und Siegel, einschließlich Verlauf.
  - Ergebnis maschinenlesbar in `benchmarks/chat_scope_v1/results_offline.json`, mit Commit, abweichenden Dateien,
    Hash des Datensatzes und Hashes der payload- und promptbestimmenden Quellen.
- **Varianten:**
  - **A:** aktueller Code ohne die §29-Einschränkung; der Kontext-Provider ignoriert die Auswahl. Das ist kein Nachbau
    eines früheren Commits; die §27-Regeln bleiben aktiv.
  - **B:** aktuelle Regel.
- **Abgedeckt:**
  - Einzeldokument (C01);
  - Mehrdokumentfrage mit einem bzw. mit allen Anhängen (C02, C03);
  - doppelter Anhang (C04);
  - Frist aus anderem Dokument (C05);
  - Chronologie (C06);
  - Information nur in nicht angehängtem Dokument (C07);
  - Folgefragen ohne Anhang, auch mit dem Wort „Dokumente“ (C08, C09);
  - Verlauf, der Fakten anderer Dokumente trägt (C10);
  - Namen in der Frage (C11).
- **Tests:** `tests/test_bench_chat_scope.py` (19). Sie prüfen:
  - die Invarianten von B;
  - Folgefragen mit ganzer Akte;
  - die Duplikatzählung;
  - Lecks über die Frage;
  - die Normalisierung von Beträgen und Daten;
  - Zahlgrenzen und das Lesen der Hinweiszahl;
  - die Bewertungsregeln für den Live-Lauf;
  - die **Live-Sperre**: ohne beide Bestätigungsflags kein Provider, kein Aufruf.

### 30.2 Ergebnisse offline (Informationsverfügbarkeit, nicht Antwortqualität)

| Kennzahl | A ganze Akte | B Chatregel |
|---|---|---|
| Fälle / Fehler | 11 / 0 | 11 / 0 |
| benötigte Fakten im Payload | 21/21 | 17/21 |
| davon unerwartet fehlend | – | 0 |
| fehlend ohne sichtbaren Hinweis | – | 0 |
| Hinweis wie erwartet / Hinweiszahl korrekt | – | 11/11 / C04 korrekt (Duplikat nicht gezählt) |
| nicht benötigte Fakten im Payload (Minimierung) | 100/100 | 47/100 |
| sensible Klartextbegriffe im Payload | 0/66 | 0/66 |
| Payload gesamt (Zeichen) | 12 548 | 8 295 |
| Laufzeit lokal, Median je Fall (ms) | 260 | 162 |

Die genauen Zeichenzahlen und Laufzeiten stehen in der Ergebnisdatei des jeweiligen Laufs; Laufzeiten schwanken.

**Die 4 fehlenden Fakten in B** sind genau die vorab erwarteten Auslassungen, jeweils mit Hinweis:
- C02: Vertragspreis;
- C06: Vertragsdatum und Fertigstellungstermin;
- C07: Kaution 2019.

**Fristen:** C05 bleibt in B vollständig, da Fristhinweise aktenweit sind.

**Folgefragen ohne Anhang:** C08 und C09 senden in A und B die ganze Akte, ohne Hinweis.

**Verlauf:** C10 zeigt, dass frühere Antworten Fakten anderer Dokumente mitführen; in B gingen drei solcher Beträge nur
über den Verlauf hinaus. Das ist der dokumentierte Restkanal.

**Grenzen:**
- Offline antwortet ein Fake-Provider. Daher ist nur messbar, **welche Informationen verfügbar waren**, nicht ob die
  Antwort richtig, vollständig oder frei von unbelegten Aussagen ist. Das ist NICHT VERIFIZIERT.
- Lokale KI ist aus, wie im Standard; Wissensbasis und Recherche sind leer.
- Die Leckprüfung deckt Dokumenttext und Frage ab; der Verlauf enthält nur vorgefertigte Antworten.
- Eine einzige synthetische Akte. Aus den Zahlen folgt keine allgemeine Aussage zur Antwortqualität.

**Nebenbefund:** Der Aktentitel „Werklohn Estrich …“ wird als „Werklohn [PERSON_01] …“ pseudonymisiert. Das ist eine
NER-Übermaskierung (vorbestehend), betrifft Lesbarkeit, ist kein Leck.

**Unabhängiges Review:** Produktionspfad, Reproduzierbarkeit (zwei Läufe identisch bis auf Zeiten) und Live-Sperre
bestätigt. Behoben wurden:
- nach dem Schreibaufruf blockierte Antworten zählen als Fehler;
- Modellname aus `claude_model_name`;
- Herkunftsangaben ergänzt;
- Live-Ergebnisse mit Zeitstempel, nie überschrieben, Zwischenstand nach jedem Fall;
- Betrags- und Datumsformate normalisiert, Zahlgrenzen beachtet („14.250,00“ ≠ „4.250,00“);
- Nenner ausgewiesen;
- Fall mit Namen in der Frage ergänzt.

### 30.3 Live-Lauf (vorbereitet, NICHT ausgeführt)

**Freigabe nötig:** Der Owner bestätigt ausdrücklich, dass (1) ausschließlich synthetische Daten verwendet werden und
(2) beim Anbieter ein Ausgabenlimit festgelegt ist. Die App-interne Budgetprüfung greift im Benchmark nicht
(In-Memory-DB); das Anbieterlimit ist die einzige echte Grenze.

**Ablauf nach Freigabe:**
1. `python scripts/bench_chat_scope.py --live --confirm-synthetic-only --confirm-spend-limit`
   - 22 Aufrufe: 11 Fälle × 2 Varianten, je nur der letzte Turn.
   - Ergebnis: `results_live_<Zeitstempel>.json` mit Tokens je Aufruf als Kostengrundlage.
2. Installierter Build:
   - normale Instanz ohne QA-Variablen, QA-Datenbank mit der synthetischen Akte;
   - Fall 24 ohne Fang-Server;
   - Payload-Nachweis über das lokale API-Protokoll (nur Hash/Kennzahlen);
   - Antwort und Hinweis prüfen.
3. Dokumentieren:
   - Modell (`claude_model_name`), Commit, Code-Hashes;
   - Tokens und Kosten laut Anbieter;
   - Fehler;
   - die automatische Bewertung (`score_answer`). Sie ist **vorläufig**: Normalisierung von Beträgen und Daten,
     benötigte Fakten, unbelegte Werte, kein LLM-Selbsturteil.
4. Fachliche Prüfung der Antworten durch eine Anwältin bzw. einen Anwalt. Erst danach gelten Qualitätsaussagen als
   validiert. Kausalaussagen sind wegen des Aufbaus (eine Akte, n = 1 je Fall) nicht belastbar.

### 30.4 Entscheidungsvorlage: Dokumentauswahl für Editor, Neugenerierung, „Anmerkung übernehmen“ (nicht umgesetzt)

**Ist-Zustand:** Alle drei senden die ganze Akte (unabhängig nachverfolgt):
- **Editor-KI:** dazu nur die Markierung, nicht der übrige Entwurf.
- **Neugenerierung:** nur die Akte, ohne Entwurfstext.
- **Anmerkung übernehmen:** dazu der Anmerkungstext.

Es gibt **keinen gespeicherten Nachweis**, welche Dokumente ein Entwurf nutzte (kein Feld, keine Verknüpfungstabelle).

**Gemessen** (synthetisch, 5 Dokumente; Payload in Zeichen; die beiden eingeengten Varianten sind nur simuliert):

| Umfang | Editor | Neugenerierung | Anmerkung |
|---|---|---|---|
| ganze Akte (heute) | 1969 | 1440 | 1497 |
| nur Rechnung | 1081 | 552 | 609 |
| keine Dokumente | 912 | 383 | 440 |

- **Vollständige Platzhalterprüfung:** Die Editor-Zwecke und die Neugenerierung verlangen sie. Mit ganzer Akte
  blockierten im synthetischen Versuch alle drei, weil Platzhalter aus nicht benötigten Dokumenten in der Antwort
  fehlten. Weniger Dokumente bedeuten weniger Blockaden. Das ist nicht live reproduziert.
- **Unbeteiligtes Dokument:** Ein unbeteiligtes Dokument mit Adresse blockierte im Versuch alle drei Aktionen vor dem
  Cloud-Aufruf.

**Optionen:**

| Option | Datenschutz | Qualität / Vollständigkeit | Aufwand |
|---|---|---|---|
| O1 ganze Akte (heute) | keine Verbesserung | Kontext vollständig, aber häufige Blockaden | 0 |
| O2 Stil-Aktionen (Präzisieren, Kürzen, Alternativen) ohne Aktendokumente | groß | kaum Risiko: Stilarbeit an der Markierung | klein |
| O3 Neugenerierung/Anmerkung erben die Dokumente des Ausgangsentwurfs (neue Verknüpfungstabelle, Migration; ältere Entwürfe: Rückfall auf ganze Akte) | gut | Risiko, wenn eine Anmerkung ein anderes Dokument meint; Hinweis nötig | mittel |
| O4 Dokumentauswahl in jeder Aktion | präzise | abhängig von der Bedienung | mittel–hoch |
| O5 O2 + O3, dazu „ganze Akte / Dokumente ändern“ als Ausweg | groß | ausgewogen | mittel |

**Empfehlung:** O5 in Stufen, zuerst O2 (kleinste Änderung, Datenschutz und Blockaden zugleich), dann O3 mit
gespeicherter Verknüpfung, die Auswahl nur als Ausweg. Der Schriftsatz-Workflow bleibt unverändert (bewusst ganze Akte).

**Offene Entscheidungen (Owner):**
1. Welche Editor-Aktionen ohne Dokumente laufen: nur die drei Stil-Aktionen oder auch „Rechtliche Prüfung“?
2. Gehen ohne Dokumente Aktentitel und aktenweite Fristen weiter mit?
3. Ältere Entwürfe ohne gespeicherte Dokumentliste: Rückfall auf ganze Akte oder auf keine Dokumente?
4. Neue Verknüpfungstabelle (Migration) oder Rekonstruktion aus dem Chat (unsicher)?
5. Wortlaut und Platz des Hinweises bzw. des Auswegs.
6. Soll der Editor zusätzlich den vollständigen Entwurf senden (heute nur die Markierung)?
7. Platzhalterprüfung für Editor-Aktionen streng lassen oder allein auf weniger Dokumente setzen?

### 30.5 Status

| Punkt | Status |
|---|---|
| Benchmark versioniert, Regeln vorab, maschinenlesbar, reproduzierbar | PASS |
| B: keine unerwarteten Auslassungen, kein Auslassen ohne Hinweis, Folgefragen mit ganzer Akte | PASS |
| Sensible Klartextbegriffe im Payload (Dokumenttext, Frage) | PASS (0/66) |
| Antwortqualität (Korrektheit, unbelegte Aussagen) | NICHT VERIFIZIERT: Live-Lauf ohne Freigabe nicht ausgeführt |
| Kosten | NICHT VERIFIZIERT (kein Live-Aufruf) |
| Volle Testsuite: 4290 bestanden, nur die 4 bekannten Logo-Fehler | PASS |
| Installierter Build | nicht erforderlich: kein Produktivcode geändert; Fall 24 (§29) gilt weiter |
| Editor/Neugenerierung | Entscheidungsvorlage; nicht umgesetzt |

### 30.6 Nachtrag: Benchmark v1.1 – Quellen und Fallback-Pfad (13.10.2026)

Der Auftrag wurde unverändert erneut erteilt. Gegenüber v1 fehlten **Quellen** (Wissensbasis leer) und **Fallbacks**
(lokale Vorabanalyse aus). Ergänzt, ohne Produktivcode zu ändern; bestehende Fall-IDs sind unverändert.

**Neu in v1.1:**
- **C12:** freigegebener synthetischer Wissenseintrag KB1 über den echten Pfad `search_knowledge_base`.
- **Varianten A_lokal/B_lokal:** lokale Vorabanalyse aktiv, mit einem Echo-Fake statt Ollama. Die lokale Zusammenfassung
  fließt über `extend_with_local_note` in die Fristhinweise der Cloud-Anfrage ein; die Antwortprüfung ist aktiv.
- **Wissensfakten getrennt ausgewiesen:** Sie gehen unabhängig von der Dokumentauswahl in jede Anfrage.

| Kennzahl | A | B | A_lokal | B_lokal |
|---|---|---|---|---|
| benötigte Fakten | 23/23 | 19/23 | 23/23 | 19/23 |
| nicht benötigte Dokumentfakten | 110/110 | 50/110 | 110/110 | 50/110 |
| sensible Klartextbegriffe | 0/72 | 0/72 | 0/72 | 0/72 |
| Fehler | 0/12 | 0/12 | 0/12 | 0/12 |
| Payload gesamt (Zeichen) | 15 245 | 10 506 | 24 341 | 14 863 |

- **Fehlende Fakten in B:** weiterhin nur die vier erwarteten Auslassungen (C02, C06, C07), jeweils mit Hinweis.
- **Quelle KB1:** in allen 48 Läufen im Cloud-Payload (`anonymisierte_quellenverweise`), in A und B gleich.
- **Lokale Zusammenfassung:** stand in allen 24 lokalen Läufen nachweislich im Cloud-Payload. Mit lokaler Vorabanalyse
  steigt die Payload, weil die Zusammenfassung den Sachverhalt wiederholt (Echo-Fake). Ausgeschlossene Dokumente kamen
  nicht zurück.

**Grenzen von v1.1:**
- Der Echo-Fake sieht nur den bereits eingeschränkten Payload. Belegt ist damit, dass der Fallback-Pfad keinen eigenen
  Zugriff auf Aktendokumente hat; das Verhalten eines echten Ollama-Modells ist nicht gemessen.
- Der Zweig „Zusammenfassung lohnt nicht“ (nur Erreichbarkeitsprüfung) wird bei diesen kurzen Sachverhalten nicht erreicht.
- Das Test-Embedding findet KB1 in jedem Fall; das ist keine Relevanzmessung.

**Unabhängiges Review v1.1:** Produktionspfad für Fallback und Quelle bestätigt. Umgesetzt wurden:
- Nachweis der Zusammenfassung im Payload statt nur „aufgerufen“;
- Wissensfakten aus der Minimierungskennzahl herausgenommen (vorher verzerrt: 47 % → 50 %);
- Kennung KB1 statt K1 (Kollision mit K1 aus §27.5).

**Tests:** `tests/test_bench_chat_scope.py` (22). Volle Testsuite vor dieser Änderung: 4302 bestanden, nur die 4 bekannten
Logo-Fehler. Danach wurden nur Benchmark-Dateien geändert.

**Live-Lauf:** weiterhin **nicht ausgeführt**, weil die Freigabe fehlt (§30.3). Die Antwortqualität bleibt NICHT
VERIFIZIERT.

## 31. Adversarialer Pseudonymisierungs-Benchmark und Stilfeld (13.10.2026, Ausgang `a1a9709`)

Nur synthetische Daten. Kein externer Modellaufruf: Der Live-Lauf aus §30 wurde **nicht** freigegeben. Die Antwort auf
die Freigabefrage war ein neuer Auftrag, keine Bestätigung von „nur synthetisch“ und „Ausgabenlimit“. Stufe B bleibt
No-Go, Stufe C No-Go.

### 31.1 Benchmark

- **Datensatz:** `benchmarks/pseudonymization_v1/cases.json`, 63 Feldfälle mit festen IDs.
  - Gruppen: Namen, Organisationen, Orte/Adressen, Kontakt, Bank, Kennungen, persönliche Daten, Fakten-Negativfälle,
    Pfade, Adversarial.
  - Dokumenttypen: Brief, E-Mail, OCR, Tabelle, Notiz, Chat, Wissen, Frist, Formular, Vorlage.
  - Je Fall festgelegt: zu schützende Werte mit Typ, zu erhaltende Fakten, neutrale Werte, Konsistenzgruppen, erwartetes
    Ergebnis (erlaubt / blockiert / beides).
  - 13 Fälle sind als **bekannte Grenze** markiert; sie zählen voll in die Kennzahlen.
- **Skript:** `scripts/bench_pseudonymization.py`. Die Messregeln (Recall, Precision, Treffer, Leck, Fakten, Konsistenz,
  Blockaden, Grenzen) stehen im Datensatz, bevor ausgewertet wird.
  - **Feldfälle:** echtes `ClaudePrivacyGateway.prepare_request`, je Payload-Feld (Sachverhalt, Anmerkungen, Verlauf,
    Quellen, Fristhinweise, Stil, Vorlage).
  - **End-to-End-Fälle E01–E06 (inkl. E05b):** echter Chat-/Drafting-Pfad; gemessen am Payload, den der
    Schreib-Provider unmittelbar vor dem Cloud-Aufruf erhält.
  - **Ergebnis:** `results.json` mit Commit, Hashes, Paketversionen (Presidio 2.2.364, spaCy 3.8.16,
    de-core-news-lg 3.8.0).
- **Tests:**
  - `tests/test_bench_pseudonymization.py`: keine Lecks außerhalb dokumentierter Grenzen, Stilfeld, keine unnötigen
    Blockaden, Faktenerhalt, Abgleichsregeln, End-to-End inklusive Fail-Closed;
  - `tests/test_privacy_style_field.py`.

### 31.2 Ergebnisse (Feldfälle; exakte, aktuelle Zahlen in `results.json`)

| Typ | Referenzwerte | geschützt (Recall) | Precision | Klartext im Payload |
|---|---|---|---|---|
| PERSON | 37–38 | ≈0,89 | ≈0,78–0,81 | 4 |
| KENNUNG | 22 | 0,955 | 1,0 | 1 (+1 Teil) |
| ADRESSE | 5 | 1,0 | 1,0 | 0 |
| IBAN | 5 | 0,8 | 1,0 | 1 |
| TELEFON | 5 | 1,0 | 1,0 | 0 |
| EMAIL | 3 | 0,667 | 1,0 | 1 |
| ORT | 3 | 0,667 | 0,571 | 1 |
| PERS_DATUM | 3 | 1,0 | 1,0 | 0 |
| ORGANISATION | 2 | 1,0 | 1,0 | 0 |
| BIC, URL | je 1 | 1,0 | 1,0 | 0 |

Recall gilt je Referenztyp, Precision je vorhergesagtem Typ mit typunabhängigem Treffer; ein F1 je Typ steht in
`results.json` und ist eine Orientierung.

- **Klartext im Payload: 8 Werte, ausschließlich in Fällen mit dokumentierter Grenze:**
  - OCR-Ziffern im Namen „Kar1 Brenn3r“;
  - kleingeschriebenes „sauer“;
  - „Der Richter Zweigelt“;
  - Ort im Grundbuchtext;
  - E-Mail mit OCR-Leerzeichen;
  - gesperrte IBAN (OCR);
  - „Startnummer 815“;
  - gesperrter Name „K a r l  B r e n n e r“.

  Außerhalb dieser Grenzen: 0.
- **Fehlalarme: ca. 10.**
  - „Betreff“, „Mobil“, „Bad“, „Nullbreite“, „Platzhalter-Trick“ als Person;
  - „Az“, „Ausschlagungsfrist“, „Hauptbahnhof“ als Ort;
  - „Urt“ und „VIII“ in einer BGH-Fundstelle.
  - Davon kosten **Fakten**: das Aktenzeichen „VIII ZR 1/21“ und „Hauptbahnhof“ (66/68 Fakten erhalten).
  - Beträge, Fristen und Normen blieben in allen Faktenfällen erhalten.
- **Blockaden:** keine unnötigen und keine fehlenden. Tabellen mit Namen sind „beides zulässig“.
- **Platzhalterkonsistenz – FAIL (P05):** In einem Text werden „Herr Brenner“ zu `[MANDANT_01]`, „Herrn Brenner“ zu
  `[PERSON_01]` (Anrede im Platzhalter) und „Brenners“ zu `[PERSON_02]`. Die Cloud-KI sieht drei Personen statt einer;
  Sachzusammenhänge können verfälscht werden. Ursache: Ein längerer NER-Treffer mit Anrede oder Genitiv setzt sich gegen
  den bekannten Namen durch. **Nicht behoben:** Ein Fix ändert Span-Grenzen in der Pseudonymisierung und ist ein eigenes
  Arbeitspaket.
- **Nach Dokumenttyp:**
  - OCR: 3 von 5 Fällen mit Leck, alle als Grenze bekannt;
  - Brief: 5 von 44;
  - E-Mail, Chat, Notiz, Tabelle, Wissen, Frist, Formular, Vorlage: 0.
- **Nach Pfad:** Alle Felder schützen jetzt; das Stilfeld erst nach dem Fix (31.3).
- **Reidentifizierbarkeit:** nachgewiesen nur bei vollem Klartext, das sind die 8 Werte oben. Kombinationen aus
  Quasi-Identifikatoren wurden nicht untersucht (NICHT VERIFIZIERT).

**End-to-End (7/7 wie erwartet, 0 von 30 Referenzwerten im Klartext, Fakten 4/4):**
- **E01 Anhang:** Name, Adresse, IBAN, E-Mail, Telefon und Dateiname geschützt.
- **E02 Verlauf:** Name und Telefon aus einer früheren Nachricht geschützt.
- **E03 Wissenseintrag:** Name und IBAN geschützt, Betrag erhalten.
- **E04 Klarname aus der lokalen Vorabanalyse:** blockiert, kein Cloud-Aufruf.
- **E05/E05b NER-Ausfall** (Pseudonymisierung bzw. nur Restrisiko-Scan): blockiert, kein Cloud-Aufruf (Fail-Closed).
- **E06 Fristhinweis mit Namen** (E-Mail-Pfad): geschützt.

### 31.3 Behoben: Stilangabe lief an der Pseudonymisierung vorbei (W05)

- **Befund** (Bestandsaufnahme, im Benchmark reproduziert): `gewuenschter_stil` ist Freitext aus dem
  Schriftsatz-Formular. „Stil von RA Wiebke Sonnenfeld, IBAN DE89…“ ging im Klartext an die Cloud-KI.
- **Fix** (`app/privacy/gateway.py`):
  - Die Stilangabe läuft als markierter Unterabschnitt im selben Pseudonymisierungsdurchlauf, mit gleichen Platzhaltern
    und gleichem Restrisiko-Scan.
  - Der Marker wird aus Eingaben entfernt; eine Feldmanipulation über die Stilangabe ist ausgeschlossen (Test).
  - Die Rückfrageprüfung behält die Feldgrenze.
- **Folgefix nach Review** (`app/drafting/service.py`):
  - Platzhalter, die nur in der Stilangabe stehen, werden bei der Antwortprüfung weder verlangt noch als Leck gewertet.
    Ein Fehlalarm („Kanzleistil Hamburg“ → Personen-Platzhalter) blockierte sonst jeden Entwurf.
  - Vor dem Fix gingen diese Werte unmaskiert hinaus; gegenüber damals ist das keine Lockerung.
  - Platzhalter, die auch in anderen Feldern stehen, bleiben voll geprüft (Gegenprobe-Test).
  - Die Rekonstruktion nutzt weiter alle Zuordnungen.
- **Messung:** Vor dem Fix 10 Klartextwerte, davon 2 außerhalb bekannter Grenzen (W05); danach 8, alle innerhalb.
  Übliche Stilangaben („förmlich, sachlich“ u. a., 14 Varianten im Review) bleiben unverändert.

### 31.4 QA

| Punkt | Status |
|---|---|
| Regressionstests Stilfeld (7) und Benchmark-Tests (11): ohne Fix rot, mit Fix grün | PASS |
| Volle Testsuite: 4319 bestanden, nur die 4 bekannten Logo-Fehler | PASS |
| Zwei unabhängige Reviews, Befunde behoben (31.5) | PASS |
| Build und Installation | PASS |
| Installierter Build, Fall 25a (Name und IBAN im Stilfeld des Schriftsatz-Assistenten, abgefangener Payload: „Stil wie bei RA [PERSON_03], IBAN [IBAN_01]“) | PASS |
| Installierter Build, Fall 25b (Fehlalarm „Hamburg“ ohne Blockade) und Regression Fall 20 | NICHT VERIFIZIERT: Lauf vom System wegen Speichermangels abgebrochen, nicht neu gestartet; im Unit-Test belegt |
| Platzhalterkonsistenz (P05) | FAIL (dokumentiert, offen) |
| Vollständige Leckfreiheit, Reidentifizierbarkeit über Kombinationen | NICHT VERIFIZIERT |

Nach dem Speicherabbruch läuft die normale Lexono-Instanz nicht; der Lauf hatte sie vorab beendet. Sie ist bei Bedarf
neu zu starten.

### 31.5 Unabhängige Reviews und Grenzen

**Review 1** (Bestandsaufnahme) lieferte die Grundlage und die Stilfeld-Lücke.

**Review 2** (Fix und Benchmark) fand keinen blockierenden Fehler. Behoben wurden:
- unnötige Entwurfsblockade durch Fehlalarm im Stilfeld;
- Feldgrenze im Rückfrage-Durchlauf;
- Leck-Prüfung auf Feldwerten statt JSON (Zeilenumbrüche hatten die Wortgrenze verfehlt);
- Abgleich mit Wortgrenzen;
- Fail-Closed-Test am NER-Ausfallpunkt plus Fall „nur Restrisiko-Scan fällt aus“;
- Herkunftsangaben.

**Grenzen:**
- Handverfasster Korpus, ein Annotator, nicht fachlich validiert.
- Precision hängt von der Vollständigkeit der Annotation ab.
- Keine Aussage über reale Verteilungen.
- Ein echtes Ollama-Modell und ein echter Anthropic-Aufruf sind nicht geprüft.

**Offen:**
- P05 (Platzhalterkonsistenz bei Anrede/Genitiv);
- Fundstellen-Übermaskierung („VIII ZR“);
- OCR-Schreibweisen;
- Kleinschreibung;
- Rollenwort plus Nachname.

## 32. P05 – konsistente Pseudonymisierung grammatikalischer Namensvarianten (13.10.2026, Ausgang `1a20c1e`)

Nur synthetische Daten, kein externer Modellaufruf. Stufe B und Stufe C bleiben bis zu ihren gesonderten Freigaben
gesperrt.

### 32.1 Ursache (belegt durch Rohtreffer je Detektor)

Beispiel: „Herr Brenner und Herrn Brenner sind dieselbe Person; Brenners Anspruch ist verjährt.“ Bekannter Mandant „Karl
Brenner“, von der Produktion um den Nachnamen ergänzt.

| Stufe | Befund |
|---|---|
| Erkennung | Bekannte Namen: „Brenner“ dreimal (auch in „Brenners“). NER: „Herr Brenner“, „Herrn Brenner“, „Brenners“ |
| Überlappung (`detectors._resolve_overlaps`) | Früherer bzw. längerer Treffer gewinnt: „Herrn Brenner“ (Anrede im Bereich) und „Brenners“ (Genitiv) verdrängen den bekannten Namen |
| Zuordnung (`pseudonymizer._key_and_canonical`) | Die Alias-Regel greift nur für unveränderte, einwortige Nachnamen; „Herrn Brenner“ und „Brenners“ bekamen eigene Schlüssel |
| Ergebnis | `[MANDANT_01]`, `[PERSON_01]`, `[PERSON_02]`. Dasselbe ohne bekannte Namen: „Gernot Hahn“ / „Herrn Hahn“ / „Hahns“ ergab drei Platzhalter |

Die Ursache liegt also in den **Textbereichen** (Anrede und Genitiv im Treffer) und der darauf aufbauenden
**Zuordnung**, nicht in der Ersetzungsreihenfolge.

**Abgewogene Lösungen:**
- **A – Bereiche verankern (gewählt):**
  - vor der Überlappungsauflösung Anrede abtrennen, Genitiv auf den Kernnamen verankern, nur bei eindeutiger Verankerung;
  - Bereiche werden nur verkleinert;
  - Anrede und Genitiv bleiben lesbar.
- **B – Zusammenlegen in der Zuordnung:**
  - hätte die Kategorie überschreiben müssen;
  - Anrede und Genitiv wären im Platzhalter verschwunden;
  - invasiver, der Text verändert sich stärker.

### 32.2 Korrektur

- **`app/privacy/detectors.py` – `_anchor_person_variants`, aufgerufen in `detect_all` vor `_resolve_overlaps`:**
  - Personentreffer verlieren eine führende Anrede (Herr/Herrn/Frau/Hr./Hrn./Fr.).
  - Ein Genitiv (-s, -'s, -’s) wird auf den Kern verankert, beides **nur**, wenn der Kern eindeutig verankert ist:
    - entweder bekannter Name genau einer Kategorie;
    - oder Nachname genau eines mehrteiligen Personentreffers im selben Text.
  - Gleiche Nachnamen verschiedener Personen sind nie verankert. Unverankerte Namen bleiben unverändert; „Klaus“ wird
    nie zu „Klau“.
  - Ein verankerter Treffer übernimmt die Kategorie des bekannten Namens.
- **`app/privacy/pseudonymizer.py`:**
  - Unveränderte Treffer behalten ihren (Kern-)Wert in `_merge_literal_spans`.
  - Der Genitiv bleibt hinter dem Platzhalter stehen (`[MANDANT_01]s`); die Rückführung ergibt „Karl Brenners“.
  - Die ersetzten Genitivformen werden als `PseudonymMapping.variants` festgehalten.
- **`app/privacy/security_check.py` (nur verschärft):** Die Antwortprüfung wertet zusätzlich als Leck:
  - „Anrede [+ Dr./Prof.] + Nachname“ bei mehrteiligen Personennamen;
  - die tatsächlich ersetzten Genitivformen.

  Damit ist sie mindestens so streng wie vor P05, als diese Formen eigene Zuordnungen hatten. Bewusst **keine**
  allgemeine Regel „Nachname + s“, denn das Review zeigte Fehlblockaden („des Richters“, „des Kochs“).
- **Unverändert:** Presidio, Restrisiko-Scan, Final Payload Gate, Fail-Closed und die Pseudonymisierung aller Felder.

### 32.3 Tests

`tests/test_privacy_name_variant_consistency.py` (29 Tests, synthetisch). Ohne Fix 7 rot; die Negativfälle waren schon
vorher grün und sichern gegen falsche Zusammenführung. Abgedeckt:
- „Herr/Herrn Brenner“ und „Brenners“;
- weitere Varianten (Hr./Hrn., apostrophierter Genitiv, Anrede vor Vollnamen);
- ohne bekannte Namen;
- **zwei Personen gleichen Nachnamens, auch bekannte – getrennt**;
- Vornamen auf -s;
- unverankerte Namen;
- Wiederholung und Reihenfolge;
- wörtliche Platzhalter;
- Gültigkeit je Anfrage;
- Erhalt von Beträgen, Fristen, Normen und Aktenzeichen;
- Rückführung mit Genitiv;
- keine Fehlblockade normaler Genitive;
- Leck-Prüfung der Varianten;
- Fail-Closed bei NER-Ausfall;
- **End-to-End über den echten Chat-Pfad** mit Payload vor dem Cloud-Aufruf.

### 32.4 Benchmark vorher/nachher (unveränderte Fälle und Regeln, gleicher Datensatz-Hash)

| Kennzahl | vorher (`46fee47`) | nachher |
|---|---|---|
| geschützte Werte | 80/88 | 80/88 |
| Klartext im Payload | 8 (alle bekannte Grenzen) | 8 (unverändert) |
| Fehlalarme | 10 | 10 |
| Fakten erhalten | 66/68 | 66/68 |
| unnötige / fehlende Blockaden, Fehler | 0 / 0 / 0 | 0 / 0 / 0 |
| **Platzhalterkonsistenz** | **1/2** | **2/2** |
| PERSON Recall / Precision / F1 | 0,895 / 0,806 / 0,848 | 0,895 / 0,794 / 0,841 |
| End-to-End | 7/7, 0 Lecks | 7/7, 0 Lecks |

- **Änderung:** einzig P05 (drei Zuordnungen → eine).
- **PERSON-Precision:** Der leichte Rückgang ist ein Zähleffekt der Kennzahl. Drei korrekte, distinkte Vorhersagen
  wurden zu einer, die Fehlalarme blieben gleich; es gibt keinen neuen Fehlalarm.

### 32.5 Unabhängiges Review und Grenzen

**Review:** kein Klartextleck; Bereiche werden nur am Anfang (Anrede) verkleinert, nie freigelegt.

Behoben aus dem Review:
- Fehlblockaden durch die zunächst allgemeine Genitiv-Regel der Leck-Prüfung;
- Genitivverlust in der Rückführung;
- Leck-Prüfung erweitert um Titel und Großschreibung.

**Bekannte, nicht durch P05 entstandene Grenzen:**
- „Frau Brenner“ (eine andere Person) wird bei einem bekannten Mandanten „Karl Brenner“ über den Nachnamen dem Mandanten
  zugeordnet. Das ist vorbestehend durch die Nachnamen-Ergänzung der bekannten Namen.
- NER-Fehlalarme am Satzanfang mit -s, z. B. „Langes Zögern“ bei Mandant „Peter Lange“, werden jetzt dem Mandanten
  statt einer anonymen Person zugeordnet. Das ist kein Leck, aber die Sachverhaltsdeutung verschiebt sich; vorher
  ebenfalls als Person maskiert.
- „Mandant Karl Brenner teilt mit …“ wird vom Restrisiko-Scan als „Ort“ blockiert. Das ist vorbestehend, unabhängig von
  P05.
- Versalien-Genitiv („BRENNERS“) bleibt unverändert unvollständig zugeordnet (vorbestehend).

### 32.6 QA und Abnahme

| Abnahmekriterium | Status | Nachweis |
|---|---|---|
| 1. Sichere Namensvarianten konsistent | PASS | 29 Tests; Benchmark-Konsistenz 2/2; installierter Build Fall 26 |
| 2. Keine spekulative Zusammenführung | PASS | Tests „gleicher Nachname“ (unbekannt und bekannt), Vornamen auf -s, unverankert; Review mit weiteren Fällen |
| 3. Datenschutzprüfungen und Fail-Closed unverändert wirksam | PASS | 1503 Privacy-Tests; Leck-Prüfung nur verschärft; Fail-Closed-Tests; Benchmark: Lecks 8 → 8, alle bekannte Grenzen |
| 4. Benchmark und Regressionstests ausgewertet | PASS | 32.4; volle Testsuite 4348 bestanden, nur die 4 bekannten Logo-Fehler |
| 5. Tatsächliche Payload-Prüfung | PASS | Unit-End-to-End über den Chat-Pfad; installierter Build Fall 26 (Payload am Fang-Server) |

**Fall 26** (QA-Datenbank, Akte mit Mandant „Quirin Zabelhuber“, Anhang mit „Herr Zabelhuber“, „Herrn Zabelhubers“,
„Zabelhubers“): Payload „Herr [MANDANT_01] zahlte am 02.03.2026 die Miete von 940,00 EUR. Herrn [MANDANT_01]s
Mieterhöhung ist strittig; [MANDANT_01]s Widerspruch folgt bis 30.04.2026.“ Kein Klarname, Fakten erhalten (**PASS**).

**Negativbefund aus dem ersten Lauf:** Die Unterhaltung war versehentlich einer Schnellentwurf-Akte ohne Mandanten
zugeordnet. Ohne bekannten Namen und ohne Vollnamen im Text wurden „Herr Zabelhuber“ und „Herrn Zabelhubers“ als zwei
Personen maskiert. Das ist **gewollt**: Gleiche Nachnamen allein begründen keine Identität. Kein Klartext.

**Weitere Prüfungen:**
- Fall 20 (Regression): PASS.
- Build und Installation: PASS. Vorher geprüft: 8,3 GB frei.
- Normale Instanz wiederhergestellt.

Ein erfolgreicher Test beweist weder vollständige Leckfreiheit noch rechtliche Datenschutzkonformität.


## 33. Nach P05: gleicher Nachname, Genitiv gewöhnlicher Wörter, juristische Fundstellen (14.10.2026, Ausgang `6dac5c5`)

Nur synthetische Daten, kein externer Modellaufruf. Stufe B und Stufe C bleiben gesperrt. Eine `AGENTS.md` existiert
nicht; maßgeblich sind `CLAUDE.md` und `.agentic/`.

### 33.1 Ausgangslage (über das echte Gateway reproduziert)

| Risiko | Befund | Ursache | neu durch P05? |
|---|---|---|---|
| A gleicher Nachname | Mandant „Karl Brenner“: „Seine Nachbarin Frau Brenner ist nicht verwandt“ → `Frau [MANDANT_01]` | Nachnamen-Ergänzung bekannter Namen („Brenner“) ordnet jede Anrede + Nachname dem Mandanten zu, unabhängig vom Geschlecht | nein, vorbestehend |
| B Genitiv | Mandant „Peter Lange“: „Langes Zögern“ → `[MANDANT_01]s Zögern`; zusätzlich „Peter Langes Anspruch“ → `[PERSON_01]` statt `[MANDANT_01]` | NER-Fehltreffer „Langes“ + P05-Verankerung; im P05-Code zählte der Treffer sich selbst als Namensträger | ja (beides P05) |
| B' Adjektiv | Mandantin „Ute Groß“: „Großes Interesse“ → `[MANDANT_01]es Interesse` | bekannte Namen werden ohne Wortgrenze gesucht (für Genitiv und Komposita gewollt) | nein |
| C Fundstellen | „BGH, Urt. v. … – VIII ZR 1/21“ → `BGH, [PERSON_01]. v. … – [PERSON_02] ZR 1/21`; „BVerfG“ als Ort | NER-Fehltreffer auf Abkürzungen und römische Senatsnummern | nein |

### 33.2 Korrektur (`app/privacy/detectors.py`, alles vor der Überlappungsauflösung in `detect_all`)

**A – gleicher Nachname, andere Person:**
- Steht vor dem bloßen Nachnamen eine Anrede, deren Geschlecht dem bekannten Vollnamen **widerspricht**, wird der
  Treffer eine eigene Person: eigener Platzhalter, weiterhin maskiert.
- Belege für das Geschlecht des Vollnamens: Anrede oder Rollenwort direkt davor im Text, dazu eine kleine Liste
  eindeutiger Vornamen.
- Mehrdeutige Vornamen (Andrea, Simone) sind nicht enthalten. Widersprechen sich die Belege, gilt das Geschlecht als
  unbekannt.
- Ohne eindeutigen Beleg bleibt die bisherige Zuordnung. Es wird nie eine Identität **erzeugt**, nur eine unbelegte
  aufgehoben.

**B – Genitiv:**
- Ein Genitiv ohne Anrede wird nur verankert, wenn spaCy das Wort als Eigenname (PROPN) taggt: „Brenners“, „Hahns“ ja;
  „Langes“ (ADJ) nein. Ein nicht verankerter Treffer bleibt eine eigene, maskierte Person.
- Mehrteilige Genitive („Peter Langes“) werden nur verankert, wenn der Kern **exakt** ein bekannter oder im Text
  vorhandener Vollname ist. Der Selbstbezug-Fehler ist behoben.
- Führende Zahlen- und Datumstoken werden von Personentreffern abgeschnitten. Aus „01.03.2026. Langes“ wurde vorher eine
  Person; das Datum ging verloren, und seit P05 wurde der Rest einer anderen Person zugeordnet.
- Ein bekannter Name, der Wortanfang eines **Adjektivs** mit reiner Adjektivendung ist („Großes“, „Kurzes“; spaCy ADJ),
  wird verworfen.
- Gegenproben: „Brennersche Erbengemeinschaft“, „Brennerhaus“ und „Groß' Anspruch“ bleiben maskiert.

**C – Fundstellen:**
- Nie als Person, Ort oder Organisation maskiert werden:
  - eine römische Senatsnummer unmittelbar vor Registerzeichen und Nummer;
  - Abkürzungen mit Punkt (Urt., Beschl., Vgl., Rn., Az.);
  - Kürzel der Bundesgerichte.
- Aktenzeichen im Format der Bundesgerichte (römisch oder arabisch + ZR/ZB/AZR/BvR/StR …) werden jetzt als
  `AKTENZEICHEN` **erkannt**. Vorher deckte bei römischen Nummern nur der zufällige Personentreffer einen Teil ab.
- Lesbar bleiben sie nur in diesen Zitierformen:
  - „<Bundesgericht>, Urt./Beschl. v. <Datum> – <Az>“;
  - „<Bundesgericht>[,] <römisches Az>“ (Stand §22: „Vgl. BGH VI ZR 123/19“).
- Auch dann bleiben sie maskiert, wenn in den 120 Zeichen davor oder 80 danach ein Hinweis auf das eigene Verfahren
  steht: unser, eigene, Mandant, Revision, Beschwerde, eingelegt, anhängig, Az., Zeichen, Verfahren.
- Aktenzeichen unterer Instanzen, eigene Aktenzeichen, arabische Kurzformen und Reporter-Formen („BGH NJW …
  (I ZR 23/20)“) bleiben maskiert.

**Leistung:** Die Wortarten werden höchstens einmal je Text ermittelt, das Geschlecht je Vollname zwischengespeichert.
Das Review hatte gemessen, dass die erste Fassung spaCy einmal je Treffer aufrief: über 100 s bei 20.000 Zeichen ohne
Satzzeichen. Danach entsprach die Laufzeit HEAD (15,7 s gegenüber 16,0 s). Fällt spaCy aus, wird nichts verworfen und
nichts verankert; das ist die sichere Richtung.

### 33.3 Tests

`tests/test_privacy_identity_genitive_citations.py` (synthetisch; vor dem Fix 13 von 32 rot, die Gegenproben schon
vorher grün):
- A positiv/negativ: andere Person mit gleichem Nachnamen, gleiche Anrede, Gegner-Kategorie, unbekanntes Geschlecht,
  mehrdeutiger Vorname, widersprüchliche Belege.
- B: Adjektive („Langes Zögern“, „Langes Warten“, „Großes Interesse“, „Kurzes Zögern“), echte Genitive, Ableitungen und
  Komposita, Datum im Namenstreffer.
- C: lesbare Bundes-Fundstellen; Namen neben Fundstellen maskiert; eigene, unterinstanzliche und Kurzform-Aktenzeichen
  maskiert; alle Review-Reproduktionen.
- Wortarten höchstens einmal je Text.
- Leck-Prüfung der Antwort bei getrennter Person.
- **End-to-End über den echten Chat-Pfad** mit Payload vor dem Cloud-Aufruf.

Bewusst geändert ist ein P05-Test: „Frau Brenner? Nein: Herrn Karl Brenner gehört das Haus“ erwartete die Zuordnung zum
Mandanten, obwohl der Satz das Gegenteil sagt.

### 33.4 Benchmark vorher/nachher (unveränderte Fälle und Regeln, gleicher Datensatz-Hash)

| Kennzahl | vorher (`6dac5c5`) | nachher |
|---|---|---|
| geschützte Werte | 80/88 | 80/88 |
| Klartext im Payload | 8 (alle bekannte Grenzen) | 8 (unverändert) |
| **Fehlalarme** | **10** | **7** |
| **Fakten erhalten** | **66/68** | **67/68** („VIII ZR 1/21“) |
| Platzhalterkonsistenz | 2/2 | 2/2 |
| unnötige / fehlende Blockaden, Fehler | 0 / 0 / 0 | 0 / 0 / 0 |
| PERSON Recall / Precision / F1 | 0,895 / 0,794 / 0,841 | 0,895 / 0,844 / 0,868 |
| ORT Recall / Precision / F1 | 0,667 / 0,571 / 0,615 | 0,667 / 0,667 / 0,667 |
| End-to-End | 7/7, 0 Lecks | 7/7, 0 Lecks |

Geändert haben sich nur P16 (Fundstelle) und I02 („Az.“). Auf den Probesätzen beider Reviews ist kein Fall schlechter als
HEAD. Zwölf Fälle, die auf HEAD Aktenzeichen im Klartext sendeten, sind jetzt maskiert.

### 33.5 Unabhängige Reviews

**Review 1** fand drei Schwächen der ersten Fassung, alle behoben und als Regressionstests abgesichert:
- zwei neue Lecks (eigene Bundesverfahrens-Aktenzeichen, römische Aktenzeichen nach „Az.“);
- quadratische Laufzeit;
- Geschlechtsfehler bei mehrdeutigen Vornamen.

**Review 2** fand zwei weitere Punkte, ebenfalls behoben und getestet:
- ein neues Leck: arabische Kurzform „BAG 2 AZR 55/25“;
- ein durch Abkürzungen abgeschnittenes Hinweisfenster.

Ohne neue Exposition geprüft wurden das Abschneiden von Zahlen-Token (IDs, Geburtsdaten), Namen und Adjektive sowie die
Fail-Closed-Richtung bei spaCy-Ausfall.

### 33.6 Bewertung und Grenzen

**Datenschutz – verbessert:**
- Kein neues Klartextleck auf Benchmark und Review-Probesätzen.
- Mehrere vorbestehende Aktenzeichen-Lecks sind geschlossen.
- Eine unbelegte Identitätsgleichsetzung („Frau Brenner“ = Mandant) wird bei widersprechendem Geschlecht aufgehoben.
- Leck-Prüfung, Restrisiko-Scan und Fail-Closed sind unverändert.

**Juristische Inhaltsqualität – verbessert:**
- Bundes-Fundstellen bleiben lesbar.
- „Großes Interesse“ und „Langes Zögern“ werden nicht mehr dem Mandanten zugeordnet.
- Fristdaten in Namenstreffern bleiben lesbar.
- Fehlalarme 10 → 7, Fakten 66 → 67 von 68.

**Bewusste Grenzen bzw. vorbestehend:**
- **Zitierform ohne Hinweiswort bleibt lesbar** (z. B. „Termin BGH VIII ZR 99/25“, wie schon vor §33). Eigene
  Bundesverfahren in reiner Zitierform sind damit nicht sicher erkennbar.
- Reporter-Formen sind konservativ maskiert.
- „Langes“ bleibt als NER-Fehltreffer eine eigene, maskierte Person (`[PERSON_01] Zögern`), statt lesbar zu sein.
- Ohne Geschlechtsbeleg bleibt „Frau Brenner“ dem Mandanten zugeordnet.
- **Vorbestehende Fehlblockaden** im Restrisiko-Scan:
  - „Herr Peter Lange rügt Feuchtigkeit seit …“ blockiert („person“);
  - „Mandant Karl Brenner teilt mit …“ blockiert („ort“, §32).
- **Vorbestehender Absturz (Review):** Ein bekannter Name, der Teil eines internen Markers ist (z. B. Mandant „Alt“),
  lässt `prepare_request` fail-closed abbrechen. Kein Leck, aber jede Anfrage dieses Mandanten wird abgelehnt.
- Kein Beweis vollständiger Leckfreiheit und keine rechtliche Konformität.

### 33.7 QA

| Prüfung | Status |
|---|---|
| Neue Regressionstests (`test_privacy_identity_genitive_citations.py`, 52) und P05-Tests | PASS |
| Privacy- und Benchmark-Tests (1555) | PASS |
| Volle Testsuite: 4400 bestanden, nur die 4 bekannten Logo-Fehler | PASS |
| Benchmark vorher/nachher (33.4) | PASS |
| Zwei unabhängige Reviews, alle Befunde behoben | PASS |
| Build und Installation (vorher geprüft: 8,2 GB frei) | PASS |
| Installierter Build, Fall 27 (Akte mit Mandant „Quirin Zabelhuber“, Anhang mit „Frau Zabelhuber … nicht verwandt“, „Großes Interesse“, „BGH, Urt. v. … – VIII ZR 1/21“) | PASS |
| Installierter Build, Fall 20 (Regression) | PASS |
| Normale Instanz wiederhergestellt | PASS |
| Vollständige Leckfreiheit, rechtliche Konformität | NICHT VERIFIZIERT |

**Fall 27, abgefangene Payload:** „Herr [MANDANT_01] klagt seit 02.03.2026 auf Minderung von 940,00 EUR. Seine
Nachbarin Frau [PERSON_01] ist nicht verwandt und Zeugin. [PERSON_02] Interesse an einer Einigung besteht. Vgl. BGH, Urt.
v. 12.10.2022 – VIII ZR 1/21; Widerspruch bis 30.04.2026.“

**Negativbefund aus dem ersten Lauf (Prüfskript):** Die erste Fassung erwartete „Großes“ lesbar. In dieser Akte heißt
aber niemand „Groß“; die Maskierung als eigene Person ist ein vorbestehender NER-Fehltreffer. Die §33-Regel betrifft nur
Kollisionen mit bekannten Namen. Die Prüfung wurde auf das eigentliche Kriterium („nicht dem Mandanten zugeordnet“)
korrigiert.


## 34. Realistic Legal Benchmark & E2E (`benchmarks/legal_e2e_v1`) und drei Geburtsdatum-Lecks (14.10.2026, Ausgang `0b8ad4e`)

Nur synthetische Daten, kein externer Modellaufruf, keine API-Kosten. Keine Änderung an Gateway, Privacy Gates,
Fail-Closed oder API-Key-Verwaltung. Kein neuer Agent: die Verantwortung liegt bei Agent I (`agents/qa/AGENT.md`).

### 34.1 Aufbau

| Baustein | Inhalt |
|---|---|
| A Quellenregister (`source_register.json`) | 7 öffentliche Quellen (Gesetze/Rechtsprechung im Internet, BVerfG, openJur, ZJS-Übungsfälle, Vis Moot, Soldan Moot) mit Herkunft, URL, Art, Umfang. **BLOCKIERT:** Nutzungsrechte und URLs nicht geprüft (externer Abruf; die Einstufung nach § 5 UrhG/Lizenz trifft eine verantwortliche Person). Kein Eintrag wird verwendet. |
| B Korpus (`cases/`) | 4 vollständig synthetische Akten: Mietrecht (LE-D001), Arbeitsrecht (LE-D002), Verkehrsunfall (LE-T001), Erbrecht (LE-T002); 20 Dokumente als TXT/DOCX/PDF und 4 reine Bild-Scans; Widersprüche, Aktenzeichen, Fristen, reale Normen. |
| C Referenzdaten | Fakten, Chronologie, Fristen, Normen, Widersprüche, `protect`/`keep`, Aufgaben. Dev (`LE-D`) und Test (`LE-T`) getrennt; Testfälle nicht zur Regelabstimmung. Der Validator (`scripts/legal_bench/corpus.py`) weist erfundene Werte, unbelegte Zitate, fehlende Schutzwerte, ungeprüfte Quellen und Split-Fehler zurück (Negativtests). |
| D E2E (`scripts/legal_bench/e2e.py`) | echte Pfade in einer gemeinsamen DB: `IntakeService` → `DocumentProcessingService` (Extraktion, OCR mit vendored Tesseract, Klassifikation, Fristen) → `DocumentSearchService` (Produktions-Embedding offline) → `ChatService.send_message` → `DraftingService.create_draft` → `EditorService.autosave_draft` → DOCX/PDF-Export. Der Schreib-Provider erfasst den Payload unmittelbar vor der Cloud-Grenze und sendet nichts. |
| E Auswertung | getrennte Kennzahlen je Split; kritische Befunde einzeln (Klartext im Payload, Daten fremder Akten, Suchtreffer fremder Akten, Platzhalterreste im Export, persönliches Datum als Frist), nie gemittelt; Herkunft (Commit, Korpus-Hash, Code-Fingerabdrücke). Regressionstest `tests/test_legal_benchmark_e2e.py`. |

### 34.2 Befunde und Korrekturen

| Befund | Ursache | Korrektur |
|---|---|---|
| OCR 0/17 Werte im ersten Lauf | Runner: Tesseract ohne `TESSDATA_PREFIX` (das Installer-Bundle setzt ihn, der Dev-Lauf nicht) | Runner wie das Bundle konfiguriert; kein Produktfehler |
| **Leck 1 (Benchmark, LE-D002 Q2):** Geburtsdatum aus dem Arbeitsvertrag im Klartext im Payload eines Chats mit nur der Abmahnung als Anhang | Fristausschnitt zur Frist „Beginn 15.01.2019“ begann mit „14.07.1988, …“; „geb.“ lag außerhalb des Ausschnitts, der Vertrag stand nicht im Sachverhalt | `remove_unlabelled_personal_dates` (`app/deadlines/extractor.py`): ein laut Quelltext persönliches Datum, das im Ausschnitt ohne Bezeichnung steht, wird entfernt (Ziffergrenzen, auch Rand-Bruchstücke wie „Juli 1988“); gilt auch, wenn es zugleich das Datum des Vorschlags ist |
| **Leck 2 (unabhängiges Review, CONFIRMED):** lange Dokumente: Schlüsselstellen-Auswahl trennte „geb.“ vom Datum („geb. […] 14.07.1988“), Namen maskiert, Datum im Klartext | Segmentierung nach „geb.“ bzw. Zeilenumbruch, Kopf/Ende-Schnitt | dieselbe Funktion auf den gekürzten Dokumentauszug (`_document_excerpt`); bezeichnete Daten bleiben für die Maskierung im Gateway |
| **Leck 3 (Re-Review, CONFIRMED):** Daten mit Leerzeichen („geb. 14. Juli 1988“, „14. 07. 1988“, „3. März 2024 … verstorben“) wurden von der Segmentierung zerteilt („14. […] Juli 1988“); andere Schreibweisen desselben Datums („14.7.88“ neben „geb. 14.07.1988“) blieben stehen, weil die wertgleiche Maskierung nur die exakte Schreibweise kennt | Segmentierung an Satzpunkten; Entfernung nur des ganzen Wertes | `_document_excerpt` ersetzt Geburts-/Todesdaten vor Segmentierung und Schnitt durch ein Token ohne Leerraum und setzt sie danach wieder ein; die Entfernung erfasst jede Schreibweise (numerisch, Kurzjahr, mit Leerzeichen, Monatsname/-abkürzung); Rand-Bruchstücke nur einmal je Seite und nicht vor Wortzeichen („1990er“ bleibt); Erkennung je Quelltext zwischengespeichert (vorher ≈ 95 ms je Fristvorschlag bei 200k Zeichen) |
| Bezeichnetes Geburtsdatum im Mittelteil langer Dokumente wurde nach der Korrektur sicher entfernt statt maskiert (drittes Review, kein Leck) | Segmentierung schnitt nach „geb.“ | kein Schnitt nach „geb.“/„gest.“; Datum bleibt bezeichnet und wird im Gateway als `[GEBURTSDATUM_nn]` maskiert |
| Leckprüfung des Runners nur auf volle Werte (Review) | — | zusätzlich Nachname (auch Genitiv), erstes Wort von Organisationen, Datumsvarianten (Kurzjahr, ohne Nullen, Monatsname), wortgrenzengenau |
| Suche: nicht importiertes erwartetes Dokument fiel still weg (Review) | — | zählt als Fehltreffer |

### 34.3 Ergebnis (Lauf auf finalem Code; Datei `benchmarks/legal_e2e_v1/results.json` mit Herkunft)

| Kennzahl | Dev (2 Fälle) | Test (2 Fälle) |
|---|---|---|
| **kritisch** | **keine** | **keine** |
| Klartext eigener Schutzwerte im Payload / Daten fremder Akten | 0 / 0 | 0 / 0 |
| benötigte Werte im Payload | 15/15 | 14/14 |
| Extraktion (Referenzwerte im Text) / davon OCR | 68/68 / 9/9 | 53/53 / 13/13 |
| Fristen (nur Datumsgleichheit) | 5/5 | 2/2 |
| zusätzliche Fristvorschläge (alle bekannte Falldaten: Ereignis-/Dokumentdaten) | 15 | 12 |
| Suche Top-1 | 2/2 | 2/2 |
| Schriftsatz gespeichert, Bearbeitung in DOCX und PDF | 2/2 | 2/2 |
| Dokumenttyp vergeben (nicht „Unbekannt“; Korrektheit nicht bewertet) | 8/10 | 0/9 |

Vor der Korrektur: 1 kritischer Befund (Leck 1) im Dev-Split. Laufzeit ≈ 55 s für 4 Fälle.

### 34.4 Bewertung und Grenzen

- **Datenschutz – verbessert:** drei Wege, auf denen ein Geburts-/Todesdatum seine Bezeichnung verlor oder zerteilt wurde und im Klartext
  hinausging, sind geschlossen und per Test belegt. Gateway, Restrisiko-Scan und Fail-Closed unverändert.
- **Qualitätsbefunde (nicht behoben, nicht auf Testfällen abgestimmt):** viele Fristvorschläge sind bloße
  Ereignisdaten (27 bei 7 echten Fristen); die Klassifikation vergibt für Gutachten, Protokoll, E-Mail, Testament und
  Bescheid keinen Typ.
- **Grenzen:** 4 kleine Fälle, kurze Dokumente (der lange Fall steht nur im Unit-Test); Fristen nur nach Datum, nicht
  nach Art/Quelle; Widersprüche und Chronologie werden noch nicht gegen Modellantworten geprüft (offline gibt es keine
  Antwort); Personennamen-Konsistenz (z. B. Mutter und Sohn „Wehrhahn“ in LE-T002) wird nicht gemessen. Telefonnummern
  liegen in vergebenen Mobilfunkbereichen und die Testfirmennamen könnten realen Firmen ähneln (Review, PLAUSIBLE) –
  bei der nächsten Korpusversion durch eindeutig fiktive Werte ersetzen.
- **Bekannte Nebenwirkungen der Korrektur (Review, kein Leck):** Zahlen, die wie eine andere Schreibweise eines
  persönlichen Datums aussehen („Version 4.7.88“ bei Geburtsdatum 04.07.1988), werden im Auszug ebenfalls entfernt;
  die Zeichenangaben im Auszugsmarker zählen die Datumstoken statt der Originalzeichen (Abweichung wenige Zeichen).
  Erste Erkennung je langem Quelltext ≈ 1 s (danach zwischengespeichert).
- **NICHT VERIFIZIERT:** Antwortqualität und Kosten eines echten Modells (Live-Lauf nur nach Freigabe), vollständige
  Leckfreiheit, rechtliche Konformität.

### 34.5 QA

| Prüfung | Status |
|---|---|
| Korpus-Validator inkl. Negativtests (`test_legal_benchmark_corpus.py`) | PASS |
| E2E-Regressionstest (`test_legal_benchmark_e2e.py`, 2 Fälle, Test-Embedding) | PASS |
| Neue Datenschutz-Regressionstests (`test_privacy_death_dates_and_deadlines.py`); Leck 2 und 3 ohne Korrektur reproduziert | PASS |
| Volle Testsuite: 4425 bestanden, nur die 4 bekannten Logo-Fehler (fremde Löschung der Logo-Dateien) | PASS |
| Benchmark-Lauf alle 4 Fälle: keine kritischen Befunde | PASS |
| Drei unabhängige Reviews; alle bestätigten Lecks behoben, verbleibende Punkte ohne Leck in 34.4 | PASS |
| Build und Installation von `8f2a804` (Bundle enthält beide Korrekturen, per Archivprüfung) | PASS |
| Installierter Build, Fall 28: langes DOCX (Auszug mit Schlüsselstellen), Mandant „Quirin Zabelhuber“, „geb. 14. Juli 1961“ mitten im Text und „(14.7.61)“ an anderer Stelle | PASS |
| Installierter Build, Fall 27 (Regression §33) | PASS |
| Normale Instanz wiederhergestellt (nur Port 8000, Health 200, kein Debug-Port, Fang-Server beendet) | PASS |
| Vollständige Leckfreiheit, rechtliche Konformität, Antwortqualität echter Modelle | NICHT VERIFIZIERT |

**Fall 28, abgefangene Payload (Auszug):** „[…] Mieter ist Herr [MANDANT_01], geb. [GEBURTSDATUM_01], wohnhaft in
[ORT_01]; […] er mindert um 940,00 EUR. […] Die Vermieterin bestaetigt den Eingang am 3.4.2026; […] der Mieter (…)
verlangt Antwort bis 30.04.2026.“ Das bezeichnete Datum bleibt bei „geb.“ und wird maskiert, die unbezeichnete
Kurzform wird entfernt, Beträge und Fristen bleiben lesbar. Hinweis zum QA-Werkzeug: der Fang-Server zählt nach einem
Neustart wieder ab `payload_001`; das Prüfskript erkannte die überschriebene Datei deshalb nicht als neu und meldete
zunächst „kein Cloud-Aufruf“ – die Payload wurde direkt ausgewertet, ältere Payloads vor Fall 27 archiviert.

### 34.6 Etappe 4/5: Informationserhalt, Fehlalarme der Maskierung, Korpus v2

**Neue Messungen (Runner):** Für jeden Schriftsatz (ganze Akte als Kontext) prüft der Runner, ob nach der
Pseudonymisierung die zu erhaltenden Werte, die Chronologiedaten, die Normen und beide Seiten jedes Widerspruchs im
Payload stehen. Außerdem liest er die Zuordnungen des Gateway-Ergebnisses (Platzhalter → Original; der Gateway selbst
bleibt unverändert) und listet jedes maskierte Original, das kein geschützter Referenzwert ist, als
Fehlalarm-Kandidaten mit Kategorie. Das misst die Verfügbarkeit der Information für das Modell, nicht die
Antwortqualität.

**Korpus v2:** v1 schrieb Umlaute als „ae/oe/ue/ss“. Das ist unrealistisch und erhöhte die Fehlalarme der
NER-Erkennung (Gateway-Probe: „Kündigungsschutz“ bleibt lesbar, „Kuendigungsschutz“ wird `[PERSON_01]`). Alle vier Fälle
haben deshalb jetzt echte Umlaute (kuratierte Wortliste, nur ganze Wörter; Fallversion 2). Nur die Eingaben ändern sich,
nicht die Regeln.

| Kennzahl (Lauf auf Korpus v2) | Dev | Test |
|---|---|---|
| kritisch / Klartext im Payload | keine / 0 | keine / 0 |
| erhaltene Werte `keep` / Chronologie / Normen | 13/13 / 13/13 / 6/6 | 13/13 / 11/11 / 3/3 |
| Widersprüche mit beiden Seiten im Payload | 3/4 | 1/2 |
| Fehlalarm-Kandidaten (v1 → v2; Zählung vor der strengeren Zuordnung) | 14 → 9 | 20 → 17 |
| Kandidaten mit strengerer Zuordnung (allein maskierte Orts-/Adressteile sichtbar), manuell eingeordnet | 13: 6 plausibel richtig bzw. Ort-Policy (Orte, Firma, Rechnungsnummer), **7 gewöhnliche Wörter** | 22: 10 plausibel richtig bzw. Ort-Policy (Orte, Straßen, Gutachten-/Aktenzeichen), **12 gewöhnliche Wörter** |
| Suche Top-1 (v1 → v2) | 2/2 → 1/2 (LE-D002: Kündigung vor der gescannten Abmahnung, Top-5 weiter getroffen) | 2/2 |

**Befund – Fehlalarme zerstören Kernfakten (nicht behoben, Owner-Entscheidung):**

- **Gewöhnliche Wörter werden als Person/Ort maskiert.** Die Ampelphase in LE-T001 wird zu „bei [PERSON_nn]“ und
  „bei [ORT_nn]“, und damit verschwindet der Kernwiderspruch des Falls (die Nummern der Platzhalter variieren je
  Aufgabe). Ebenso betroffen:
  - LE-D002: „des Schichtleiters“ wird `[PERSON_nn]`, sodass die zweite Widerspruchsseite fehlt.
  - LE-T002: „Testament“ und „Alleinerbin“.
  - LE-D001: „Schimmel“ und „Schlafzimmer“.
  - Weitere: „Unfallmitteilung“, „Tagebuchnummer“, „Reparaturdauer“, „Schadenabteilung“, „Mein Sohn“,
    „Gesprächsnotiz“, „Bruttomonatsvergütung“.
  - Kontextabhängig: „Schimmel“ und „Schlafzimmer“ bleiben in einem Einzelsatz lesbar und werden nur im ganzen Dokument
    maskiert.
- **Plausibel richtig maskiert:** Firmenname, Rechnungs-/Gutachtennummer, Straße und das eigene Aktenzeichen. Teilweise
  zerlegt wird das Aktenzeichen „Kfz-Schadensgutachten Nr. G-26“ → `[ORT_nn]-0533`.
- **Zielkonflikt:** Eine Person kann tatsächlich „Grün“, „Rot“ oder „Schimmel“ heißen. Jede Lockerung kann ein Leck
  erzeugen. Deshalb gibt es hier keine eigenmächtige Regeländerung (CLAUDE.md: bei fachlicher Unklarheit Optionen
  vorlegen).

| Option | Inhalt | Datenschutzrisiko | Nutzen |
|---|---|---|---|
| F1 | unverändert lassen, Befund dokumentieren | keins | keiner; Kernfakten gehen weiter verloren |
| F2 | NER-Treffer nach Artikel/Präposition nicht als Person werten, außer mit Anrede/Titel, Vorname oder bekanntem Namen | **mittel**: Nachnamen ohne Anrede nach Präposition sind in Notizen üblich („Gespräch mit Becker“, „laut Schneider“) | hoch für die gemessenen Fälle |
| F3 | Liste juristischer und alltäglicher Fachbegriffe (Testament, Alleinerbin, Kündigungsschutz, Unfallmitteilung, …), die nie allein Person/Ort sind | gering (nur Begriffe, die als Name praktisch nicht vorkommen) | mittel; Liste muss gepflegt werden |
| F4 | statt automatischer Maskierung eine Rückfrage für unsichere NER-Einzeltreffer | gering: eine falsch beantwortete oder aus Ermüdung weggeklickte Rückfrage kann einen echten Namen freigeben | hoch, aber mehr Rückfragen im Alltag |

**Empfehlung:** F3 + F2, erst nach Freigabe. Die Wortliste und die Regeln dürfen nur aus den Dev-Fällen und
`pseudonymization_v1` entstehen. Die hier genannten Testfall-Beispiele (LE-T001 „Grün“/„Rot“, LE-T002 „Testament“/
„Alleinerbin“) sind für diese Frage nicht mehr unabhängig. Die Abschlussmessung braucht deshalb **neue** Testfälle.
Abnahmekriterium wie in §31–§33: keine neuen Lecks, Konsistenz unverändert.

**QA Etappe 4/5:**
- Benchmark-Tests: 16 bestanden. Es wurde kein App-Code geändert, daher keine volle Suite.
- Ein unabhängiges Review hat die Zahlen 14→9, 20→17, 3/4, 1/2 und die Such-Verschiebung reproduziert. Es hat
  bestätigt, dass der Runner den Gateway nur beobachtet und die Umlaut-Umstellung keine Referenzwerte verändert hat.
- Umgesetzte Korrekturen aus dem Review:
  - strengere Zuordnung der Kandidaten;
  - Risiko von F2 und F4 ehrlicher eingestuft;
  - Testfall-Beispiele für eine künftige Wortliste ausgeschlossen;
  - Platzhalternummern entfernt.

### 34.7 Etappe 6: langer Vertrag (LE-D003), neuer unabhängiger Testfall (LE-T003), Firmenname ohne Rechtsform

**Korpus:**
- LE-D003 (Dev, Baurecht): Bauvertrag mit rund 50.000 Zeichen. Damit greift im E2E der Dokumentauszug mit
  Schlüsselstellen. Das Geburtsdatum steht mit Leerzeichen („geb. 3. März 1975“) in einer Anlage mitten im Vertrag.
- LE-T003 (Test, Bußgeldverfahren): neu angelegt, nachdem die Testfälle LE-T001/T002 für die Fehlalarm-Frage nicht mehr
  unabhängig waren (§34.6). Er enthält einen gescannten Bescheid, einen Tatzeit-Widerspruch und einen abgelaufenen
  Eichschein.
- Der PDF-Renderer setzt einen Absatz, der nicht mehr auf die Seite passt, jetzt auf eine neue Seite. Vorher hätte
  `insert_textbox` ihn stillschweigend nicht geschrieben.
- Die Leckprüfung des Runners bildet Datumsvarianten jetzt für jede Schreibweise des Schutzwerts.

**Befunde:**

| Befund | Einordnung | Maßnahme |
|---|---|---|
| **Leck 4 (LE-D003, kritisch):** Aktentitel „Haselhorst ./. Holzbau Wendeburg“ → „… Holzbau [BETEILIGTER_01] …“. Die Partei hieß „Holzbau Wendeburg GmbH“; bekannt war nur der volle Name, maskiert wurde nur „Wendeburg“ (zufällig auch Nachname eines Beteiligten). | Teil eines Firmennamens im Klartext | `_build_known_entities` indiziert bekannte Namen zusätzlich ohne Rechtsform (mindestens zwei Wörter oder ein großgeschriebenes Wort ab 4 Zeichen; „Bau AG“ → nicht „Bau“). Tests in `test_privacy_organisation_without_legal_form.py`. Danach keine kritischen Befunde. |
| Langer Vertrag: die Fakten aus der Vertragsmitte (Vergütung, Fertigstellung, Vertragsstrafe) kommen im Auszug an (Q3: 3/3); das Geburtsdatum mit Leerzeichen erscheint nicht im Klartext | bestätigt §34.2 im E2E | – |
| **LE-T003: alle drei Cloud-Anfragen blockiert** („Nach Pseudonymisierung weiterhin erkennbare Muster: person“). Der Restrisiko-Scan erkennt „Messbeamter“ als Person; im ersten Lauf wurde schon „Polizeiobermeister“ als Person maskiert. | Fail-Closed wirkt, kein Leck. Für die Nutzung ist die Akte aber unbrauchbar (Fehlblockade). Gleiche Ursache wie §34.6 (NER bei Funktions-/Berufsbezeichnungen). | Nicht geändert: Privacy-Gates dürfen nicht abgeschwächt werden, und es ist ein Testfall. Teil der Owner-Entscheidung F1–F4. |
| Interner Auszugsmarker „… von 50222 Zeichen …“ wird als Adresse maskiert („[ADRESSE_nn] dieses Dokuments“) | Fehlalarm an einem Systemtext, kein Leck | dokumentiert |
| Klassifikation: Test-Split 2 von 13 Dokumenten mit Typ (Gutachten, Protokoll, E-Mail, Testament, Bescheid ohne Typ; Bußgeldbescheid als „Einspruch“) | Qualität | dokumentiert |

**Lauf auf finalem Stand (6 Fälle):**

| Kennzahl | Dev (3) | Test (3) |
|---|---|---|
| kritisch | keine | keine |
| Klartext im Payload / fremde Akte | 0 / 0 | 0 / 0 |
| Aufgaben ohne Cloud-Anfrage (blockiert) | 0 | 3 (alle LE-T003) |
| Extraktion / OCR | 95/95 / 12/12 | 82/82 / 27/27 |
| benötigte Werte im Payload | 23/23 | 14/14 (nur T001/T002; T003 blockiert) |
| Fristen (Datum) | 8/8 | 3/3 |
| Suche Top-1 / Top-5 | 2/3 / 3/3 | 3/3 / 3/3 |
| Schriftsatz und Export mit Bearbeitung | 3/3 | 2/3 (LE-T003 blockiert) |

**QA:**
- Volle Suite nach allen Korrekturen: 4439 bestanden, nur die 4 bekannten Logo-Fehler.
- Unabhängiges Review der Änderung „Firmenname ohne Rechtsform“. Bestätigt: es kommt nur Maskierung hinzu.
  Gefunden und behoben:
  - **neu, CONFIRMED:** Beide Namensformen bekamen verschiedene Platzhalter. Ein Entwurf mit nur einem davon wäre mangels
    voller Abdeckung abgelehnt worden. Jetzt ordnet `_canonicalize_alias` die Form ohne Rechtsform dem vollen Namen zu,
    über das gemeinsame `strip_legal_form` in `app/privacy/pseudonymizer.py`.
  - **vorbestehend, CONFIRMED:** Das letzte Wort „GmbH“ wurde als Nachname indiziert. Dadurch wurde jedes „GmbH“, auch
    in „§ 13 GmbHG“, zum Gegner-Platzhalter, also ein Bedeutungsfehler und kein Leck. Namen mit Rechtsform bekommen
    jetzt keine Nachnamen-Variante mehr.
  - Einzelwort-Reste („Sparkasse AG“ → „Sparkasse“) werden nicht indiziert; sie träfen als Teilstring zu viel.
  - Weitere Rechtsformen: „GmbH & Co KG“ ohne Punkt, „… & Co. KGaA“, „Ltd.“, „i. L.“, „Aktiengesellschaft“,
    „haftungsbeschraenkt“.
  - Tests: 12 in `test_privacy_organisation_without_legal_form.py`; mit den angrenzenden Pseudonymisierungs- und
    Gateway-Tests zusammen 125 grün.

**Nachtrag installierter Build `6b27b48`:**
- Bundle enthält `strip_legal_form` (Archivprüfung).
- **Fall 29:** Akte „QA-SYNTH Zabelhuber Mietsache“ mit synthetischer Gegnerin „Holzbau Wendeburg GmbH“, nur für den
  Lauf in die QA-Datenbank eingefügt und danach wieder entfernt. Das Anhang-DOCX enthält „… ./. Holzbau Wendeburg“,
  „Die Holzbau Wendeburg GmbH verlangt 23.600,00 EUR bis zum 30.04.2026“ und „§ 13 GmbHG“. Ergebnis **PASS**:
  - kein Teil des Firmennamens im Klartext;
  - genau ein `[GEGNER_nn]`;
  - „§ 13 GmbHG“ bleibt lesbar;
  - Fakten erhalten.
  - Nebenbefund ohne Leck: Die NER fasst „… ./. Holzbau Wendeburg“ über den Zeilenumbruch mit dem Folgesatz zu einem
    eigenen `[ORGANISATION_nn]` zusammen, der getrennt von `[GEGNER_nn]` geführt wird. Außerdem wird „Mietsache“ im
    Aktentitel als Person maskiert (Fehlalarm, §34.6).
- **Regression Fall 28:** PASS.
- Normale Instanz wiederhergestellt: nur Port 8000, Health 200, kein Debug-Port, Fang-Server beendet.

### 34.8 Etappe 7: Quellenintegrität der Modellantwort (Messung, Befund, Entscheidungsvorlage)

**Messung:**
- Für jeden Schriftsatz-Fall erzeugt der Runner zusätzlich einen Entwurf mit einer simulierten Modellantwort. Diese
  enthält eine erfundene Norm („§ 9999 BGB“) und eine erfundene Entscheidung („BGH, Urt. v. 01.01.2020 - I ZR 999/19“).
- Gezählt wird, ob Lexono die Fundstellen im gespeicherten Entwurf als ungeprüft markiert (Abschnitt „Offene
  Prüfpunkte“). Die Probe läuft offline, es wird nichts gesendet.
- Gemessen wird Lexonos lokale Absicherung, nicht das Modell.

**Ergebnis: 0 von 10 erfundenen Fundstellen markiert** (5 Fälle; LE-T003 blockiert wie bekannt). Beide stehen
unverändert im Entwurf. Ursache:
- Lexono prüft Fundstellen einer Modellantwort lokal nicht.
- Der Schutz beruht auf der Anweisung an das Modell und auf der Recherchebewertung („nicht ausreichend belegt“) vor
  dem Aufruf.
- Die Gesetzesbibliothek (`app/laws`) könnte Normen prüfen, Entscheidungen dagegen nur gegen freigegebene Quellen.
- Das berührt die Grundregeln „Niemals Rechtsquellen, Fundstellen oder Zitate erfinden“ und „Unsicherheit explizit
  markieren“. Eine erfundene Fundstelle fällt heute nur auf zwei Wegen auf:
  - durch eigene Prüfung der Kanzlei;
  - über die optionale Cloud-Prüfung „Entwurf prüfen“ (`ReviewEngine`). Diese sucht modellbasiert nach
    `unbelegte_rechtsbehauptung` und `fehlende_quelle` gegen freigegebene Quellen. Sie muss bewusst gestartet werden,
    hängt vom Modell ab und ist hier nicht gemessen (NICHT VERIFIZIERT).

**Entscheidungsvorlage (nicht umgesetzt; betrifft die Hinweisdichte jedes Entwurfs):**

| Option | Inhalt | Nutzen | Kosten/Risiko |
|---|---|---|---|
| Q1 | Normen gegen die installierte Gesetzesbibliothek prüfen. Nicht gefundene Normen werden als offener Prüfpunkt gelistet („§ 9999 BGB nicht in der Gesetzesbibliothek gefunden“). Nicht installierte Gesetze werden als „nicht lokal prüfbar“ geführt. | erkennt erfundene oder falsch nummerierte Normen deterministisch | Paragraphen mit Buchstaben, Absätze und Gesetzesabkürzungen sauber zuordnen; Bibliothek muss gepflegt sein |
| Q2 | zusätzlich jede Entscheidungs-Fundstelle (Gericht + Aktenzeichen), die nicht in freigegebenen Quellen oder im Kanzleiwissen steht, als „nicht verifiziert“ listen (lokal und deterministisch; überschneidet sich mit der modellbasierten, optionalen Prüfung „Entwurf prüfen“) | macht erfundene Rechtsprechung ohne zusätzlichen Cloud-Aufruf sichtbar | Hinweis bei nahezu jeder Rechtsprechungs-Fundstelle (Lärm), bis eine Entscheidungsdatenbank angebunden ist |
| Q3 | Entwurf mit nicht verifizierbaren Fundstellen blockieren | strengste Absicherung | unpraktikabel; widerspricht dem Entwurfscharakter |

**Empfehlung:** Q1 sofort nach Freigabe (deterministisch, geringe Hinweisdichte), dazu Q2 als zusammengefasster
Einzelhinweis („n Rechtsprechungs-Fundstellen nicht lokal verifiziert“). Der Benchmark misst die Wirkung direkt
(`erfundene_fundstellen_markiert`).

### 34.9 Etappe 8: Laufzeit und Payload-Größe

Der Runner fasst jetzt je Split zusammen:
- Laufzeit je Stufe (Dokument-Import/-Verarbeitung, Chat, Schriftsatz, Suche): Median und Maximum, lokal gemessen,
  **ohne Modell-Latenz**. Der Schriftsatz enthält Export und Quellenintegritäts-Probe.
- Payload-Größe in Zeichen je Aufgabenart.

Kosten werden bewusst **nicht geschätzt**. Lexonos Preisschätzung (`app/cost_control/pricing.py`) braucht Tokenzahlen
des Anbieters, und eine Zeichen-pro-Token-Annahme wäre ein erfundener Wert. Kosten bleiben NICHT VERIFIZIERT bis zu
einem freigegebenen Live-Lauf.

Lauf (6 Fälle, Dev-Werte):

| Kennzahl | Median | Maximum |
|---|---|---|
| Dokument (Import + Verarbeitung) | ≈ 1,2 s | ≈ 1,6 s |
| Chat | ≈ 1,8 s | ≈ 13 s (langer Vertrag) |
| Schriftsatz | ≈ 3,6 s | ≈ 23 s (langer Vertrag) |
| Payload Chat | ≈ 4.100 Zeichen | ≈ 32.500 Zeichen |

Test-Split: Chat-Median ≈ 0,9 s. Der Schriftsatz-Wert (n = 3) enthält den blockierten LE-T003. Die Payload-Größe der Schriftsätze steht in `results.json`.

Gesamtlauf ≈ 110 s. Die Maxima entstehen beim langen Vertrag (Payload ≈ 32.500 Zeichen). Die Ursache ist nicht
einzeln gemessen. Mögliche Anteile sind die Namenserkennung über den großen Payload, der Dokumentauszug und die
Erkennung persönlicher Daten im Quelltext (§34.2); das ist NICHT VERIFIZIERT.

**QA Etappe 7/8:**
- Benchmark-Tests: 9 bestanden.
- Unabhängiges Review bestätigt:
  - Es gibt keine lokale Fundstellenprüfung von Modellantworten. Die 0/10 wurden reproduziert.
  - Die Probe beeinflusst keine anderen Messungen.
  - Die Laufzeitwerte wurden reproduziert.
- Umgesetzt: „Entwurf prüfen“ in §34.8 genannt, Überschneidung mit Q2 ergänzt, Probe mit eigener Fehlerbehandlung.

### 34.10 Etappe 9: automatische Aktenzuordnung eingehender Nachrichten (Mandantentrennung)

**Messung (Runner):**
- Eingehende Nachrichten laufen durch den echten `MatterAssignmentService` mit den Produktionsschwellen (automatisch ab
  0,85, Prüfung ab 0,4), und zwar gegen alle Akten der Benchmark-Datenbank.
- Dafür bekommen die Akten zwei optionale Angaben: das Kanzlei-Aktenzeichen (`matter.reference_number`; der Validator
  prüft, dass es in einem Dokument steht) und die Mandanten-E-Mail.
- Proben:
  1. jede E-Mail des Korpus;
  2. „Az. <eigenes Aktenzeichen>“ von unbekanntem Absender;
  3. „Ihr Zeichen: <eigenes Aktenzeichen>“;
  4. **Konflikt:** die bekannte Adresse von Mandant A nennt das Aktenzeichen von Akte B.
- Kritisch ist jede automatische Zuordnung zu einer fremden Akte.

| Probe | vorher | nachher |
|---|---|---|
| **Konflikt: automatisch zur fremden Akte** | **6/6** (Nachricht von Mandant A ohne Prüfung in der Akte von Mandant B) | **0/6**, jeweils „needs_review“ mit Begründung |
| Az. → richtige Akte | 4/4 | 4/4 |
| „Ihr Zeichen:“ → erkannt | 0/4 | 0/4 (unverändert, siehe unten) |
| Korpus-E-Mails von bekannten Mandantenadressen | 5/5 „no_match“ | unverändert |
| automatische Fremdzuordnung ohne Konflikt | 0 | 0 |

**Korrektur (`app/matching/matcher.py`):** Keine automatische Zuordnung, wenn der beste Kandidat kein
Absender-Signal hat, der Absender aber als Kontakt einer anderen Akte bekannt ist. Die Entscheidung ist dann
„needs_review“; der Vorschlag bleibt sichtbar. Die Regel verringert nur die Automatik und lockert keine bestehende
Prüfung (Ambiguität, Klassifikation). Tests in `test_matching_matcher.py`; der Benchmark zählt eine Fremdzuordnung im
Konfliktfall jetzt als kritisch.

**Review (unabhängig):**
- Bestätigt:
  - Die Regel verringert nur die Automatik.
  - Ein Absender, der in beiden Akten bekannt ist, und Gegenanwälte, die in beiden Akten stehen, werden nicht blockiert.
  - `needs_review` landet korrekt bei Zuordnung, Mail-Abruf und Vorschlagskarte.
- Mehr Prüfungen entstehen in einem häufigen Fall: Ein Kontakt, der nur in Akte B gespeichert ist, schreibt mit dem
  Aktenzeichen von Akte A (Gegenanwalt, Versicherer). Das ist die sichere Richtung.
- **Gefunden und behoben – UI-Lücke:** Die Vorschlagskarte im Posteingang zeigte trotz Konflikt die fremde Akte mit
  „Übereinstimmung: 90 %“ und Ein-Klick-„Übernehmen“, ohne Warnung. Jetzt nennt sie die Akte des Absenders und bittet
  um Prüfung (`_build_match_suggestion`, `message_detail.html`; Tests in `test_web_inbox.py`).

**Offen (Owner-Entscheidung, nicht geändert, weil das die Automatik ausweiten würde):**
- „Ihr Zeichen“/„Unser Zeichen“ werden nicht als Aktenzeichen erkannt; erkannt werden nur „Az.“ und „Aktenzeichen“.
  → Entschieden und umgesetzt in §39 (P1.2): „Ihr Zeichen“ wird erkannt, „Unser Zeichen“ bewusst nicht.
- Eine E-Mail von der bekannten Mandantenadresse erreicht allein 0,3 Punkte, also nicht einmal die Prüfschwelle von
  0,4. Sie erscheint deshalb ohne Zuordnungsvorschlag. → §39: jetzt immer ein Vorschlag zur Prüfung, nie eine
  Automatik.

**QA Etappe 9:**
- Matching-, Inbox- und Benchmark-Tests grün.
- Volle Suite: 4446 bestanden, nur die 4 bekannten Logo-Fehler.

**Nachtrag installierter Build `200cdd6`:**
- Ablauf: eine synthetische Nachricht wurde in die QA-Datenbank eingefügt und danach wieder entfernt. Absender war die
  bekannte Mandantenadresse der Akte „Kaufpreisdifferenz Pilot (QA)“, im Text stand das Aktenzeichen der Akte
  „QA-SYNTH Mietstreit Nordlicht“.
- Ergebnis **PASS**: Die Nachricht ist nicht zugeordnet. Die Vorschlagskarte zeigt Nordlicht mit 90 % und darunter die
  gut lesbare Warnung, die die Akte des Absenders nennt; „Übernehmen“ bleibt möglich (Screenshot im QA-Ordner).
- Normale Instanz wiederhergestellt (Health 200, kein Debug-Port).

### 34.11 Etappe 10: Dokument einer fremden Akte als Chat-Anhang

**Probe:** Je Fall fragt ein Chat in Akte A nach einem Dokument der Akte B, das per Dokument-ID angehängt ist. Das
simuliert eine manipulierte oder vertauschte ID; `ChatService.record_user_message` selbst prüft die Akte nicht.

**Ergebnis:** In 6/6 Fällen wurde die Anfrage **blockiert**, ohne Cloud-Aufruf. Kein Wert der fremden Akte stand im
Payload. Fail-Closed greift, weil `assess_documents` (`app/documents/readiness.py`) den Anhang als `OTHER_MATTER` einstuft und das Dokument-Gate des Chats dann sperrt (im Code geprüft). Der Benchmark zählt einen Wert
der fremden Akte im Payload als kritisch; dazu gibt es einen Regressionstest. Keine Produktänderung.

### 34.12 Etappe 11: OCR bei schlechten Scans, Paragraphenzeichen

**Probe:**
- Jedes Scan-Dokument des Korpus wird zusätzlich als schlechter Büro-Scan erzeugt (`render_degraded_scan`). Die
  Verschlechterung ist deterministisch (fester Seed): 110 dpi, 1,2° Schräglage, Rauschen, JPEG-Qualität 35.
- Die Scans laufen durch den echten Import- und Verarbeitungspfad in einer eigenen Probe-Akte, nach allen anderen
  Messungen.
- Gezählt werden die Referenzwerte im OCR-Text. Dev und Test werden getrennt ausgewiesen; die Werte werden nicht mit
  den sauberen Scans gemittelt.

**Befund:** Dev 12/12, Test 25/27. Die beiden fehlenden Werte waren **beide Normzitate** des gescannten Bußgeldbescheids
(LE-T003: „§ 24 StVG“, „§ 25 StVG“). Tesseract liest das Paragraphenzeichen bei schlechter Vorlage als „$“
(„($ 24 StVG)“). Ein Dev-Gegenstück zeigt dasselbe für „§ 626 Abs. 1 BGB“, „§ 622 BGB“ und „§ 91 ZPO“. Normzitate in
gescannten Schriftsätzen und Bescheiden waren damit für Suche, Fristen- und Zitaterkennung unbrauchbar.

**Korrektur (`app/documents/ocr.py`, `normalize_paragraph_signs`):**
- „$“ bzw. „$$“ wird nur dann zu „§“ bzw. „§§“, wenn eine Paragraphennummer mit Gesetzesabkürzung folgt. Dazwischen
  sind Abs./S./Satz/Nr./Ziff./lit. und Aufzählungen wie „535, 536“ erlaubt.
- Dollarbeträge („Preis $ 500“, „USD $ 20 Gebühr“), Währungscodes („$ 20 EUR“), „US$“ und vorhandene „§“ bleiben
  unverändert.
- Entwickelt an allgemeinen Zitierformen und dem Dev-Gegenstück, nicht am Testfall. Tests in
  `test_ocr_paragraph_sign.py` (echte OCR auf schlechtem Scan; ohne Korrektur reproduziert „$ 626“, „$ 622“, „$ 91“).

**Ergebnis:**
- Abschlussmessung schlechte Scans: Dev 12/12, **Test 27/27**.
- Saubere Scans unverändert: 12/12 und 27/27.
- Keine kritischen Befunde.

**Grenzen:**
- Nur eine Verschlechterungsstufe ist gemessen.
- Handschrift, Stempel, Tabellen und mehrspaltige Layouts fehlen im Korpus (NICHT VERIFIZIERT).
- QA: OCR-Tests 34 grün (nach Review-Korrektur); volle Suite 4471 bestanden, nur die 4 bekannten Logo-Fehler. Ein erster Suite-Lauf wurde vom
  eigenen Speicherwächter wegen paralleler Benchmark-Last abgebrochen; der Lauf wurde ohne Parallel-Last wiederholt.
- **Review (unabhängig):** Laufzeit unkritisch (500k Zeichen in ≤ 0,11 s, linear). Gefunden und behoben:
  - Falschtreffer vor großgeschriebenen Wörtern („$ 50 PayPal“, „$ 2 AG“, „$ 5 GmbH“, „$ 10 CAD“, „$ 10 EURO“).
  - **Datenschutzrisiko (PLAUSIBLE):** Ein falsches „§“ vor einem Namen („$ 500 DeLuca“) hätte den Namen über
    `drop_statute_abbreviations` als Gesetzesabkürzung lesbar gelassen.
  - Korrektur: Die Abkürzung muss wie ein Gesetz enden (G/O/B/V); Rechtsformen und Währungswörter sind ausgeschlossen.
  - Weiterhin nicht erfasst (Grenze): römische Absätze („$ 823 I BGB“), „lit. a“-Ketten, „ff.“, „$§“ und andere
    Fehllesungen des Zeichens („S 24“, „8 24“; Häufigkeit nicht gemessen).

**Nachtrag installierter Build `61509ab` (Fall 30):**
- Ablauf: Ein schlechter Scan (`render_degraded_scan`, synthetischer Bußgeldbescheid) wurde als Chat-Anhang in der
  QA-Instanz mit OCR verarbeitet und der Payload am Fang-Server ausgewertet.
- „§ 24 StVG“ und „§ 25 StVG“ kommen korrekt an, kein „$“ im Payload.
- Das Prüfskript meldet FAIL, aber nur wegen eines Buchstabenfehlers der OCR: „OWiG“ wurde als „OWIG“ gelesen. Das
  Paragraphenzeichen davor ist korrekt.
- Bewertung: PASS für die Korrektur, verbleibender OCR-Buchstabenfehler dokumentiert.
- Normale Instanz wiederhergestellt (Health 200, kein Debug-Port, Fang-Server beendet).

### 34.14 Etappe 12: Tabellen im Korpus, Geburtsdaten aus einer Tabellenspalte (Leck 7)

**Korpus:**
- Dokumente können eine Tabelle haben (`table`). In DOCX wird sie als echte Word-Tabelle gerendert, sonst als
  Tabellenzeilen „Zelle | Zelle“ (gleiche Darstellung wie die DOCX-Extraktion).
- Neuer Dev-Fall LE-D004 (Erbengemeinschaft): Erbentabelle „Erbe | Geburtsdatum | Erbquote“, vier Personen mit gleichem
  Nachnamen, Todesdatum, Widerspruch zum Verkehrswert.

**Leck 7 (kritisch, reproduziert):**
- Die drei Geburtsdaten der Tabelle standen in **jedem** Payload der Akte im Klartext (Chat, Chat mit Anhang,
  Schriftsatz).
- Ursache: Die Pseudonymisierung erkennt die Spalte „Geburtsdatum“ nur zeilenweise (§24). `_normalize_document_text`
  macht für den Sachverhalt aus Zeilenumbrüchen Doppel-Leerzeichen, wodurch die Tabelle unkenntlich wird.
- Roher Text: 3/3 erkannt; normalisierter Text: 0/3.

**Korrektur (`_document_excerpt`):** Für kurze und lange Dokumente gilt jetzt der **rohe** extrahierte Text als
Quelle. Dort erkannte Geburts- und Todesdaten, die im Auszug ohne Bezeichnung stehen, werden in jeder Schreibweise
entfernt (`remove_unlabelled_personal_dates`). Bezeichnete Daten bleiben für die Maskierung im Gateway.

**Ergebnis:**
- Dev-Split ohne kritische Befunde; benötigte Werte 29/29, `keep` 25/25.
- Regressionstest `test_birthdates_from_a_table_column_never_reach_the_payload` deckt kurze und lange Dokumente ab.
- Die Lücke betraf jede Akte mit einer Tabelle mit Geburtsdatum-Spalte, auch vor diesem Benchmark.

**Review (unabhängig), gefunden und behoben:**
- **Regression (CONFIRMED):** Zunächst galt nur der rohe Text als Quelle. Der erkennt Bezeichnung und Datum auf
  getrennten Zeilen nicht („geboren am“ / „14.07.1988“), der normalisierte schon; im langen Dokument wäre ein solches
  Datum ohne seine Bezeichnung im Klartext geblieben. Jetzt zählen beide Quellen (`_without_unlabelled_personal_dates`),
  mit eigenem Test.
- **Stille Auslassung (CONFIRMED):** Entfernte Daten wurden zu „…“. Jetzt erscheint sichtbar „(Datum entfernt)“
  (CLAUDE.md: Unsicherheit markieren).
- **Bekannte Nebenwirkung, sichere Richtung:** Ein anderer Termin am selben Tag wie ein Todes- oder Geburtsdatum wird
  ebenfalls entfernt (z. B. „Termin am 9. Januar 2026“ bei Todestag 09.01.2026). Der Fristvorschlag bleibt lokal
  erhalten.
- **Andere Pfade:** Keine weitere Stelle sendet normalisierten Dokumenttext. `promptlayer/builder.py` hat laut Code
  keinen Produktiv-Aufrufer (nicht erneut geprüft). Wissensbasis-Ausschnitte haben keine eigene
  Geburtsdatum-Behandlung (keine Aktendokumente).

### 34.13 Zusammenfassung (Stand 14.10.2026, morgens)

**Aufgebaut:**
- Quellenregister (BLOCKIERT, siehe unten).
- Korpus `legal_e2e_v1`: 7 synthetische Akten (4 Dev, 3 Test), 35 Dokumente, darunter 6 Scans, ein Vertrag mit
  rund 50.000 Zeichen und eine Word-Tabelle.
- Validator mit Negativtests.
- E2E-Runner über die echten Pfade: Import/OCR, Verarbeitung, Suche, Chat, Schriftsatz, Bearbeitung, Export,
  Aktenzuordnung.
- Proben: Quellenintegrität, fremder Anhang, schlechter Scan, Erhalt, Fehlalarm-Kandidaten, Laufzeit.
- Regressionstests in der Suite.
- Agent I (`agents/qa/AGENT.md`) übernimmt die Verantwortung; es gibt keinen neuen Agenten.

**Gefunden und behoben (je mit Tests, Reviews und, wo App-Code betroffen war, installiertem Build):**

| # | Befund | Art |
|---|---|---|
| 1–3 | Geburtsdatum ohne Bezeichnung bzw. zerteilt im Klartext (Fristausschnitt; Auszug langer Dokumente; Schreibweisen) | Leck |
| 7 | Geburtsdaten aus einer Tabellenspalte im Klartext (Normalisierung des Auszugs, §34.14) | Leck |
| 4 | Firmenname ohne Rechtsform teilweise im Klartext; vorbestehend: jedes „GmbH“ wurde zur Partei | Leck / Bedeutungsfehler |
| 5 | Nachricht von Mandant A automatisch in die Akte von Mandant B (Absender ↔ Aktenzeichen im Konflikt), Karte ohne Warnung | Mandantentrennung |
| 6 | OCR liest „§“ als „$“, Normzitate in Scans verloren | Quellenintegrität |

**Gemessen, nicht behoben (Owner-Entscheidung):**
- Fehlalarme der Maskierung zerstören Kernfakten; im Testfall LE-T003 führt das zur Fehlblockade aller Anfragen
  (F1–F4, §34.6/§34.7).
- Erfundene Fundstellen in Modellantworten werden nicht markiert, 0/10 (Q1–Q3, §34.8).
- „Ihr Zeichen“ wird nicht erkannt; eine Mandanten-E-Mail allein liegt unter der Prüfschwelle (§34.10).
- Fristvorschläge sind überwiegend Ereignisdaten.
- Klassifikation „Unbekannt“ bei vielen Dokumenttypen.

**BLOCKIERT:** Nutzungsrechte und URLs der 7 Registerquellen sind ungeprüft. Ein externer Abruf ist nicht erlaubt, und
die Einstufung trifft eine verantwortliche Person. Keine öffentliche Quelle wird verwendet.

**NICHT VERIFIZIERT:**
- Installierter Build für Leck 7 (Tabellen, Fall 31: Versand in der QA-Oberfläche fehlgeschlagen, im Prozess bestanden).
  → Geklärt und im installierten Build bestanden, siehe §38 (P1.1).
- Antwortqualität und Kosten eines echten Modells (kein Live-Lauf ohne Freigabe).
- Widerspruchserkennung durch „Entwurf prüfen“ (cloudbasiert).
- Handschrift, Tabellen, mehrspaltige Scans.
- Laufzeit mit Modell-Latenz.
- Vollständige Leckfreiheit und rechtliche Konformität: ausdrücklich keine solche Aussage.

**Nächste Schritte (Vorschlag):**
1. Entscheidungen F (Fehlalarme) und Q (Fundstellenprüfung) treffen. Danach Umsetzung nur auf Dev-Fällen und mit neuen
   Testfällen messen.
2. Rechteprüfung der Registerquellen durch eine verantwortliche Person.
3. Freigegebener Live-Lauf, nur mit synthetischen Daten und gesetztem Anbieter-Ausgabenlimit.
4. Mehr Testfälle: Tabellen, Handschrift, E-Mail-Verläufe.
5. Benchmark als festen Release-Schritt aufnehmen (P6).
- QA Etappe 12: Datenschutz-Datums-Tests 74 grün; volle Suite 4473 bestanden, nur die 4 bekannten Logo-Fehler.
- **Installierter Build `f1c2000`, Fall 31 (Erbentabelle als Chat-Anhang): NICHT VERIFIZIERT.** → Ursache geklärt
  (Fehler im Prüfskript, kein Produktfehler), im installierten Build bestanden; siehe §38 (P1.1).
  - In der QA-Oberfläche meldete der Versand zweimal „Senden fehlgeschlagen“. Es gab keinen Cloud-Aufruf und damit
    auch kein Leck.
  - Der Kontrollfall 27 (gleicher UI-Ablauf, ohne Tabelle) bestand im selben Lauf.
  - Dasselbe Tabellendokument lief im Prozess über den echten Chat-Pfad fehlerfrei: Payload vorhanden, kein
    Geburtsdatum, „1/3“ und Verkehrswert erhalten.
  - Ursache offen. Möglich ist ein Fehler im Upload/Versand bei Word-Tabellen im installierten Build oder ein
    Zeitproblem des Prüfskripts.
  - **Offener Punkt:** im installierten Build reproduzieren, mit Netzwerkantwort des Versands.
  - **Nachtrag:** Die echte Web-Upload-Route `/dashboard/chat/send` nimmt dasselbe Tabellen-DOCX im TestClient an:
    HTTP 303, ein Payload, kein Geburtsdatum, „1/3“ erhalten (neuer Test in `test_web_chat.py`). Die serverseitige
    Korrektur ist damit über die Web-Route belegt. Der UI-Fehler liegt wahrscheinlich clientseitig oder im
    Prüfskript; das ist nicht geklärt.
  - Normale Instanz wiederhergestellt.

## 35. P0-Arbeitspaket F: Fehlmaskierungen juristischer Begriffe (14.10.2026, Ausgang `d0b1c74`)

Nur synthetische Daten, kein externer Modellaufruf. Bezug: Befund und Optionen F1–F4 in §34.6/§34.7.

### 35.1 Ursachenanalyse (Diagnose je Fehlalarm, welcher Detektor ihn erzeugt)

| Klasse | Stufe | Beispiele (Legal-E2E-Benchmark) | Ursache |
|---|---|---|---|
| A | „sicher“, automatisch maskiert | Reparaturdauer, Tagebuchnummer, Unfallmitteilung, Schlafzimmer, Schichtleiters, Alleinerbin, Hausgrundstücks, Messbeamter, „Mein Sohn“, „[Unbekannt]“ | NER (`de_core_news_lg`) und Zweitsignale (`detect_unlabelled_proper_nouns`, `detect_probed_names`, `detect_article_names`) auf Komposita ohne Ableitungsfuge, Possessiv + Verwandtschaftswort, Systemlabel |
| B | „unsicher“ (Rückfragestufe, `review.py`) | „bei Grün“, Schimmel, Testament, Andernfalls | im Chat (`ask`) wird der Anwalt gefragt, im Entwurf (`mask`) vorsichtshalber maskiert |
| C | Fehlblockade LE-T003 | „Messbeamter“ | Klasse-A-Wort, das erst der Restrisiko-Scan im pseudonymisierten Kontext meldet (fail-closed) |

### 35.2 Bewertete Optionen und Entscheidung

| Option | Datenschutz | Inhaltsqualität | Entscheidung |
|---|---|---|---|
| F1 nichts tun | unverändert | Kernfakten gehen verloren | verworfen |
| F2 Präposition-/Artikelregel | mittleres Leckrisiko („laut Schneider“, Artikel-Benchmark: 21/96 Namen nach Artikel lecken bereits) | hoch | verworfen |
| F3 freie Wortliste | „Grün“, „Schimmel“ sind echte Nachnamen; pauschal | mittel | verworfen |
| F4 Rückfrage | keins | – | besteht bereits für Klasse B im Chat; keine Änderung, Lockerung von `mask` wäre pauschal |
| **Gewählt: strukturelle Ursachenbehebung für Klasse A** | siehe Messung | gezielt | umgesetzt, in zwei Red-Team-Runden verschärft |

**Umsetzung (`app/privacy/presidio_ner.py`, `detectors.py`, `security_check.py`, `review.py`):**
- **Komposita (`is_common_compound`):** Erweitert den geprüften Präzedenzfall `is_derivational_compound` auf Komposita
  ohne Ableitungsfuge. Bedingungen:
  - Kopfnomen aus einer **geschlossenen** Klasse;
  - bekannter Vorderteil mit mindestens 4 Buchstaben (oder aus zwei bekannten Wörtern);
  - Modell-Namensprobe = 0 (das Modell erkennt das Wort in keinem neutralen Namensrahmen als Person);
  - namensnahe Kopfnomen (-leiter, -zimmer, -dauer, -kosten, -frist, …) nur, wenn das ganze Wort im Modellvokabular steht;
  - erbrechtliche Rollen mit geschlossenem Präfix (Vor-/Mit-/Allein- + -erbin/-erbe).
- **Geltungsbereich:** Basis-NER, Zweitsignale und Rückfrage-Kandidaten. **Nicht** nach Anrede oder Rollenwort („Herr
  Waldleiter“ bleibt geschützt). Für Orte nur mit sicheren Kopfnomen.
- **Possessiv + Verwandtschaftswort (`POSSESSIVE_KINSHIP`):** Der Treffer muss genau „Mein Sohn“ o. ä. sein. Dieselbe
  Definition gilt in der Regelheuristik des Gateways; dort wird genau dieses Wortpaar übersprungen, das Folgepaar mit
  einem Namen bleibt geprüft.
- **Feldbezeichnung vor ersetzter Person (`is_role_label_before_person`):** Gilt nur, wenn der Feldwert bis zum
  Feldende ausschließlich aus Personenplatzhaltern oder Titeln besteht. Wirkt also nur im Restrisiko-Scan; eine
  Sprecherzeile mit Fließtext bleibt geprüft.
- **`unbekannt`:** Systemlabel „[Unbekannt]“ der Dokumenttypen, als Ganzwort-Ausnahme.

**Red-Team-Reviews (unabhängig):**
- **Runde 1:** Die erste Fassung ließ blosse, kompositaartige Nachnamen ohne Anrede durch („Hochleiter“,
  „Rosenzimmer“, „Steinkosten“: 11 von 13 Sätzen im Klartext, Baseline 0). Daraufhin wurden die Kopfnomen geteilt
  (namensnahe nur mit Vokabular).
- **Runde 2:** Auch „sichere“ Kopfnomen ließen erfundene Namen durch („Waldnotiz“, „Kirchbericht“, „Steinerbin“:
  11 von 14). Daraufhin wurde die Namensprobe = 0 für alle Klassen verlangt. Die gemessenen Probewerte überlappen sonst
  (Fachwörter 0–5, Namensformen 1–5); nur 0 trennt.
- Alle Red-Team-Sätze sind dauerhafte Negativtests in `tests/test_privacy_compound_terms.py`. Kein Satz geht im Klartext
  hinaus.

### 35.3 Messung vorher/nachher (identische Benchmarks, Ausgang `d0b1c74` vs. finale Fassung)

| Benchmark | vorher | nachher |
|---|---|---|
| `bench_name_detection` (1100 Namensfälle): Lecks / Blocks | 0 / 0 | 0 / 0 |
| `bench_article_noun_persons`: Namenslecks / maskierte Fachwörter | 21/96 / 8/240 | 21/96 / 8/240 |
| `bench_name_followups`: Folgeerwähnung / Artikel-Lecks / Fehlalarme | 0 / 32/138 / 3/22 | unverändert |
| `pseudonymization_v1`: Lecks (bekannte Grenzen) / Fehlalarme | 8 / 7 | 8 / 7 |
| Legal-E2E: kritische Befunde | keine | keine |
| Legal-E2E Test: Fehlalarm-Kandidaten | 22 | 17 (Reparaturdauer, Alleinerbin, „Mein“, „Mein Sohn“, „Unbekannt“ entfallen) |
| Legal-E2E Dev: Fehlalarm-Kandidaten | 37 | 37 |
| LE-T003 blockiert | 3/3 | 3/3 vor, **0/3 nach** dem Nachtrag 35.6 |

Keine neuen Lecks in irgendeinem Benchmark. Die Wirkung der finalen Fassung ist **bewusst klein**; die erste Fassung
hätte mehr Fehlalarme beseitigt, aber Namen freigegeben.

### 35.4 Restrisiken und offene Punkte

- **LE-T003 – inzwischen behoben, siehe 35.6.** Stand vor dem Nachtrag: fail-closed blockiert (kein Leck).
  - Ursache 1: Im echten Dokument verbindet die PDF-Umbruchlogik (`_reflow_soft_wraps`) die Zeile „Messbeamter:
    Polizeiobermeister …“ mit der Folgezeile; das Feldende geht verloren.
  - Ursache 2: Das Modell hält „Messbeamter“ für einen Namen (Probe 4), in derselben Form wie die erfundenen Namen
    der Red-Team-Runde 2.
  - Eine Freigabe würde entweder Rollen-Komposita mit hoher Namensprobe freigeben (nachweislich leckgefährlich) oder
    den Restrisiko-Scan lockern. Beides ist ausgeschlossen. Der Klartext ist im sauberen Feldfall belegt behoben (Test).
  - **Offen:** Feldgrenzen bei der PDF-Aufbereitung erhalten; das ist eine eigene Änderung der Dokumentaufbereitung.
- **Klasse B unverändert:** „bei Grün“, „Testament“, „Schimmel“ werden im Entwurf (`mask`) weiter maskiert und im Chat
  erfragt. Auflösung nur über die Rückfrage-Entscheidung des Anwalts (F4).
- **Konservative Grenzen:** „Schlafzimmer“ (Probe 2), „Tagebuchnummer“ (4), „Hausgrundstücks“ (5),
  „Wiederherstellungsdauer“ (nicht im Vokabular) bleiben maskiert. Inhaltsverlust in der sicheren Richtung.
- **Restrisiko der Freigabe:** Ein realer, seltener Nachname, der wie ein Fachkompositum mit Namensprobe 0 aufgebaut
  ist und ohne Anrede steht, würde nicht mehr maskiert. In beiden Red-Team-Runden und allen Namensbenchmarks wurde kein
  solcher Fall gefunden; vollständige Freiheit ist NICHT bewiesen.

### 35.5 QA

| Prüfung | Status |
|---|---|
| Neue Tests `test_privacy_compound_terms.py` (54): Fachwörter lesbar, Komposita-Nachnamen mit Titel, blosse Namensformen beider Red-Team-Runden, Possessiv, Feldbezeichnung | PASS |
| Privacy-, Security- und Benchmark-Tests (1651) | PASS |
| Volle Suite: 4528 bestanden, nur die 4 bekannten Logo-Fehler | PASS |
| Benchmarks vorher/nachher (35.3), ohne neue Lecks | PASS |
| Zwei unabhängige Red-Team-Reviews; alle bestätigten Lecks vor dem Commit behoben | PASS |
| LE-T003 entblockt | PASS (Nachtrag 35.6) |
| Installierter Build | NICHT VERIFIZIERT |

## 36. P0-Arbeitspaket Q: erfundene oder ungeprüfte Fundstellen (14.10.2026)

Nur synthetische Daten, kein externer Modellaufruf. Bezug: Befund und Optionen Q1–Q3 in §34.8.

### 36.1 Ursache und Korrektur der Messmethode

- **Ursache:** Lexono prüft Fundstellen einer Modellantwort lokal nicht. Der Schutz bestand nur aus der Anweisung an
  das Modell und der Recherchebewertung vor dem Aufruf. Die optionale Cloud-Prüfung „Entwurf prüfen“ ist
  modellbasiert und muss bewusst gestartet werden.
- **Messlücke im eigenen Benchmark:** Die Probe aus §34.8 las nur `draft.content`. Das enthält bewusst nur das
  Schreiben (`review_notes.py`); die „Offenen Prüfpunkte“ stehen in `draft_text` (Chat-Verlauf). Die Probe hätte eine
  Markierung also nie gefunden.
- **Korrektur:** Die Probe liest jetzt `draft_text`. Mit korrigierter Probe und ohne neue Prüfung: 0/8 (Dev) und
  0/4 (Test). Der Befund bleibt also bestätigt.

### 36.2 Optionen und Entscheidung

| Option | Bewertung | Entscheidung |
|---|---|---|
| Q1 Normen gegen die Gesetzesbibliothek | deterministisch, lokal; nur aussagekräftig, wo das Gesetz amtlich vollständig importiert ist | umgesetzt, mit Unterscheidung amtlich/kuratiert/nicht installiert |
| Q2 Rechtsprechung gegen Akte und freigegebene Quellen | lokal und deterministisch; überschneidet sich mit „Entwurf prüfen“, ohne Cloud-Aufruf | umgesetzt, als Sammelhinweis |
| Q3 Blockieren | unpraktikabel; nicht verifizierbar heißt nicht falsch | verworfen |

**Umsetzung (`app/drafting/citation_check.py`, Haken in `DraftingService` nach der Rückführung, Streaming und Nicht-Streaming):**
- **Erkennung:**
  - Normen: §/§§/Art. mit Abs./Satz/Nr./lit./römischen Absätzen/ff., Aufzählungen.
  - Aktenzeichen: auch vierstellige Jahre und EuGH „C-…“, nur nach einer Gerichtsbezeichnung; „AG“/„KG“ nur mit Ort.
  - Zeitschriftenfundstellen („NJW 2020, 123“).
- **Existenz/Beleg, je Fundstelle genau eine Stufe:**
  - `norm_amtlich_vorhanden` (amtlicher Import, `doknr`);
  - `norm_kuratiert_vorhanden`;
  - `norm_amtlich_nicht_gefunden`: nur, wenn das Gesetz amtlich importiert ist; in einer kuratierten Auswahl beweist
    ein Fehlen nichts;
  - `norm_nicht_pruefbar`;
  - `freigegebene_quelle` (Source „freigegeben“ oder Kanzleiwissen „approved“);
  - `aktenbelegt`: steht wörtlich in einem Aktendokument; wird als „nur in Aktendokumenten gefunden, keine
    unabhängige Bestätigung“ ausgewiesen, weil Akteninhalt ungeprüfter Input ist;
  - `nicht_verifiziert`.
- **Stützung der Aussage:** Wird nie automatisch geprüft und ist in jedem Hinweis ausdrücklich als „nicht geprüft“
  gekennzeichnet. Ebenso: „nicht erkannte Zitierformen wurden nicht geprüft“.
- **Keine Selbstbestätigung:** kein Modellaufruf, nur lokale Daten.
- **Ausgabe:** nur im Block „Offene Prüfpunkte“. `draft.content` (Editor, Export) bleibt das reine Schreiben; der
  Chat-Verlauf, der an die Cloud geht, entfernt die Hinweise (`split_review_notes`).

**Unabhängiges Review – bestätigte Befunde, alle vor dem Commit behoben und als Tests gesichert:**
- Aktenbeleg zu locker: „§ 4 … Satzung … KSchG“ zählte als Beleg.
- Aktenzeichen als Teilstring: „I ZR 1/21“ in „VIII ZR 1/21“.
- Wortwahl „belegt“.
- Fehlende Zitierformen.
- „Muster AG, … 2 K 33/24“ als Gericht erkannt.
- § und Art. nicht unterschieden.
- Groß-/Kleinschreibung der Gesetzeskürzel; „SGB 5“ nicht erkannt.

### 36.3 Messung

| Messung | vorher | nachher |
|---|---|---|
| Legal-E2E, erfundene Norm + Entscheidung in simulierter Modellantwort, markiert (korrigierte Probe) | Dev 0/8, Test 0/4 | Dev 8/8, Test 4/4; Stützung jeweils als „nicht geprüft“ markiert |
| Unit-Tests `test_citation_check.py` | – | 13 bestanden (Stufen, Grenzen, Formen, Wortwahl) |
| Streaming-Tests | – | angepasst: Schreiben weiterhin exakt gleich dem Stream, Hinweis im Prüfpunkte-Block |

Im Benchmark erscheint „§ 9999 BGB“ korrekt als „Existenz lokal nicht prüfbar“: In der Benchmark-Datenbank ist kein
Gesetz installiert. Mit amtlich importiertem BGB lautet die Stufe „im amtlichen Gesetzestext NICHT gefunden“ (Unit-Test).

### 36.4 Restrisiken

- **Hinweisdichte:** Jede Antwort mit erkannter Fundstelle erhält mindestens die Sammelzeile, auch Chat-Antworten mit
  korrekten Normen (bewusste Entscheidung für Q1/Q2; Lärm möglich).
- **Grenzen der Erkennung:** Nicht erkannte Zitierformen bleiben ungeprüft; der Hinweis sagt das ausdrücklich.
  Aufgehobene Paragraphen ohne Text können als „nicht gefunden“ erscheinen.
- **Existenz ist nicht Richtigkeit:** Eine existierende Norm kann falsch angewendet sein. Die Stützung bleibt
  NICHT VERIFIZIERT, bis eine freigegebene externe Quelle oder die Prüfung durch die Kanzlei sie bestätigt.
- **Installierter Build:** NICHT VERIFIZIERT.
- QA: `test_citation_check.py` 13, Streaming-/Benchmark-Tests grün; volle Suite 4541 bestanden, nur die 4 bekannten
  Logo-Fehler. Unabhängiges Review mit 7 bestätigten Befunden, alle behoben.

### 35.6 Nachtrag: LE-T003 entblockt – Feldgrenze bei der Dokumentaufbereitung (Ursachenbehebung)

**Ursache der verbliebenen Blockade (§35.4):** Die PDF-Umbruchlogik `_reflow_soft_wraps` verband die Feldzeile
„Messbeamter: Polizeiobermeister Jannik Brettschneider“ (mindestens 50 Zeichen, ohne Satzzeichen am Ende) mit der
Folgezeile „Eichschein des Messgeräts …“. Das Feldende ging verloren. Der Restrisiko-Scan hielt „Messbeamter“ deshalb
für einen weiteren Namen und blockierte jede Anfrage der Akte.

**Korrektur (`app/ai_providers/local_ai_provider.py`):**
- Eine **namensartige** Feldzeile wird nicht mehr mit einer groß beginnenden Folgezeile verbunden. Namensartig heißt:
  Bezeichnung aus 1–3 Wörtern, „:“, dann ausschließlich 1–5 Titel- oder Namenswörter bis zum Zeilenende.
- Sätze mit Doppelpunkt und umbrochene Namen werden weiter normal verbunden.
- Damit greift die enge Regel `is_role_label_before_person` (nur Kategorie Person).
- **Kein Privacy-Gate, kein Fail-Closed und keine Pseudonymisierungsregel wurde gelockert.**

**Unabhängiges Review in zwei Runden:**
- **Runde 1:** Die erste Fassung erfasste jede Zeile mit Doppelpunkt und zerteilte Sätze sowie umbrochene Namen
  (Qualität, kein Leck). Daraufhin wurde die Regel auf namensartige Feldwerte verschärft.
- **Runde 1, zweiter Befund:** Die NER etikettiert die Bezeichnung kontextabhängig als Ort. Die zunächst ergänzte
  Ausnahme für „ort“ gab im Restrisiko-Scan echte Ortsnamen vor einem Personenplatzhalter frei. Sie war für LE-T003
  **nicht nötig** und wurde wieder entfernt.
- **Runde 2:** keine neuen Lecks; Satz- und Namensumbrüche korrekt.

**Ergebnis (identische Benchmarks gegen die Ausgangsbasis `d0b1c74`):**

| Kennzahl | vorher | nachher |
|---|---|---|
| LE-T003: Anfragen ohne Cloud-Anfrage (blockiert) | 3/3 | **0/3** |
| Test-Split: Schriftsatz und Export | 2/3 | 3/3 |
| Test-Split: benötigte Werte im Payload | 14/14 | 20/20 |
| Test-Split: `keep` | 13/13 | 19/19 |
| Klartext im Payload / kritische Befunde | 0 / keine | 0 / keine |
| Namens-, Artikel-, Folgeerwähnungs- und Pseudonymisierungs-Benchmarks | siehe 35.3 | unverändert, keine neuen Lecks |

Die Fehlalarm-Kandidaten im Test-Split steigen von 22 auf 26, weil LE-T003 jetzt überhaupt Payloads erzeugt; seine
Maskierungen waren vorher hinter der Blockade unsichtbar. Fünf Kandidaten in T001/T002 entfallen.

**Tests:**
- `test_field_lines_are_not_joined_with_the_next_field_or_sentence`
- `test_sentence_with_colon_and_wrapped_names_are_still_joined`
- `test_protocol_field_line_through_document_preparation_does_not_block` (mit und ohne Nachbarsatz, über die echte
  Dokumentaufbereitung und das Gateway)

**Restrisiko:** Bleibt eine Feldbezeichnung in anderer Aufbereitung (z. B. OCR mit anderer Zeilenstruktur) unbezeichnet,
kann der Restrisiko-Scan weiter fail-closed blockieren. Das ist die sichere Richtung.
- QA Nachtrag 35.6: volle Suite 4545 bestanden, nur die 4 bekannten Logo-Fehler; Benchmarks gegen die Ausgangsbasis
  ohne neue Lecks.

## 37. Verifikation F und Q im installierten Windows-Build (14.10.2026)

Nur synthetische Daten, isolierte QA-Datenbank, Fang-Server (`ANTHROPIC_BASE_URL` Loopback): kein Aufruf eines echten
Modells, keine Schlüssel ausgegeben.

### 37.1 Build-Nachweis

- **Vorher:** Der installierte `Lexono.exe` (06:24) enthielt F/Q **nicht** (Archivprüfung `bundlecheck_fq.py`):
  `citation_check` fehlte, `is_common_compound`, `_FIELD_LINE_RE` und `POSSESSIVE_KINSHIP` waren nicht vorhanden.
  Der Abschlussbericht §35/§36 („installierter Build NICHT VERIFIZIERT“) war also zutreffend.
- **Neu gebaut** aus dem sauberen Arbeitsbaum auf `0245788` (abgesehen von fremden Asset-Änderungen) und installiert
  (10:54).
- Die Archivprüfung findet alle 9 Merkmale aus F und Q im Bundle.

### 37.2 Testaufbau

- **QA-Akte:** „QA-SYNTH Zabelhuber Mietsache“.
- **Testdokumente:** LE-T003-Messprotokoll (PDF), Fachbegriffe (DOCX), Namen (DOCX), Aktenunterlage mit Fundstellen
  (DOCX).
- **Fang-Server:** ergänzt die Echo-Antwort für Q um eine simulierte Modellantwort mit je einer Fundstelle pro
  Evidenzstufe.
- **Gesetzesdaten (nur QA-DB, danach wieder entfernt):**
  - kuratierte Auswahl BGB/StGB über die Produktfunktion `import_all_fixtures`;
  - ein klar synthetisches Testgesetz „QASynthG“ mit `doknr` „QA-SYNTH-…“, das einen amtlichen Import simuliert.
    Das echte Gesetz herunterzuladen wäre ein externer Abruf gewesen.

### 37.3 F – Ergebnisse (installierter Build)

| Fall | Ergebnis |
|---|---|
| **LE-T003** (Messprotokoll als PDF, Feldzeile „Messbeamter: Polizeiobermeister …“) | **PASS**: nicht blockiert, Payload vorhanden; PDF-Feldgrenze erhalten („Messbeamter: [PERSON_03] [PERSON_04]“, danach Doppel-Leerzeichen); kein Name im Klartext (Brettschneider, Jannik, Quast, Tönnies) |
| „Reparaturdauer“, „Alleinerbin“, „Mein Sohn“, „[Unbekannt]“ | **PASS**: im Payload lesbar |
| „Messbeamter“ (eigene Zeile im DOCX) | **FAIL gegenüber der Erwartung „nicht maskiert“**: maskiert als Person. Das entspricht dem dokumentierten Design (Namensprobe 4, §35.4: gilt in der sicheren Richtung, gelöst ist nur die Blockade). Im LE-T003-PDF ist „Messbeamter“ dagegen lesbar. |
| Namen: blosse Red-Team-Formen (Hochleiter, Lindenvertrag, Steinerbin, Rotherbin), mit Titel (Herr Bergmann, Zeugin Hausmann, Herr Waldleiter), nach Artikel („Die Pfannenstiel“, „an den Sommerkorn“) | **PASS**: alle maskiert, kein Klartext. „Sommerkorn“ wird als Ort statt als Person kategorisiert (geschützt, Kategorie falsch). |
| Bekannte Fehlalarme bleiben | „Lasermessung“, „Talweg“, „Eichschein“, „Polizeiobermeister“ maskiert (dokumentierte Grenzen) |

**Restrisiko (unverändert dokumentiert):** Im Artikel-Benchmark lecken 21 von 96 Namen nach Artikel; das ist
vorbestehend und war vor F gleich. Im installierten Build waren die geprüften Artikel-Fälle geschützt; die
vorbestehende Lücke ist damit nicht widerlegt.

### 37.4 Q – Ergebnisse (installierter Build)

| Fall | Ergebnis |
|---|---|
| Chat: Evidenzstufen | **PASS**: § 99 QASynthG „im amtlichen Gesetzestext NICHT gefunden“; I ZR 999/19 „nicht verifiziert“; § 9999 BGB und § 12 StVG „Existenz lokal nicht prüfbar“; § 4 KSchG und 2 AZR 111/19 „nur in Aktendokumenten gefunden (keine unabhängige Bestätigung)“; § 823 BGB (kuratiert) und § 2 QASynthG (amtlich) als „gefunden“ gezählt (2 von 8) |
| Nicht erkannte Zitierform („§ 535 Rn. 12“) | **PASS**: nicht einzeln geprüft; der Sammelhinweis nennt „nicht erkannte Zitierformen wurden nicht geprüft“ |
| Hinweis „Stützung nicht geprüft“ | **PASS**: im Chat sichtbar; keine Fundstelle erscheint als inhaltlich verifiziert |
| Payload | **PASS**: Folgefrage ohne den Hinweisblock (nur der Antworttext steht im Verlauf) |
| **Entwurf (Schriftsatz-Werkzeug → Editor)** | **FAIL (Stand Build 0245788)**: Der Entwurf enthält die erfundenen Fundstellen, der Fundstellenhinweis erscheint aber **weder im Editor noch in der Entwurfsansicht**. → behoben, siehe §37.6 |
| Export DOCX/PDF | **PASS**: enthält nur das Schreiben, keinen Hinweisblock |
| Export als Ganzes | **FAIL (Stand Build 0245788)**: Wegen der Entwurfs-Lücke kann eine ungeprüfte Fundstelle unmarkiert in den Export gelangen. → behoben, siehe §37.6 |

**Ursache der Entwurfs-Lücke:**
- Die Route `/dashboard/tools/schriftsatz/generate` leitet nach `create_draft` direkt in den Editor. Der Editor zeigt
  nur `draft.content`, also das Schreiben.
- `DraftingResult.draft_text` mit den „Offenen Prüfpunkten“ wird verworfen.
- Das Modell `Draft` hat kein Feld für Prüfhinweise.
- Das betrifft alle Hinweise dieses Blocks, nicht nur Q.
- **Folge für die Messung:** Der Benchmark-Wert „erfundene Fundstellen markiert 8/8 bzw. 6/6“ misst `draft_text` und
  **überschätzt** damit für den Entwurfspfad, was der Anwalt sieht.

**Optionen (Stand 95c12fc, inzwischen entschieden – siehe §37.6):**
- Option 1: Fundstellenprüfung bei jeder Editor-Darstellung lokal aus `draft.content` neu berechnen und im Editor
  anzeigen (kein Schema-Eingriff; berücksichtigt auch Bearbeitungen). **Umgesetzt.**
- Option 2: Prüfhinweise am Entwurf speichern. Nicht umgesetzt (Schema-Eingriff, veraltet nach Bearbeitung).

### 37.5 Regression

| Prüfung | Ergebnis |
|---|---|
| Relevante Testgruppen (F/Q-Tests, Privacy, Gateway, Pseudonymisierung, Drafting-Streaming, Chat, Posteingang, Matching, Benchmarks) | 611 bestanden |
| Volle Suite | Lauf vom eigenen Speicherwächter wegen paralleler normaler Instanz abgebrochen (Rest-RAM < 1,5 GB). Kein Code geändert seit dem letzten vollen Lauf auf diesem Stand (4545 bestanden, nur die 4 bekannten Logo-Fehler, §35.6). |
| Legal-E2E auf `0245788` | keine kritischen Befunde, 0 Klartext, 0 fremde Akte, 0 blockierte Aufgaben, benötigte Werte 29/29 und 20/20, erfundene Fundstellen markiert 8/8 und 6/6 (siehe Einschränkung oben), keine Fremdzuordnung, fremder Anhang 7/7 blockiert |
| Neue Fehler neben den 4 bekannten Logo-Tests | keine |

**QA-Umgebung wiederhergestellt:**
- Testgesetze aus der QA-DB entfernt (vorher waren keine installiert).
- Fang-Server beendet.
- Normale Instanz läuft (Health 200, kein Debug-Port).
- Der Fang-Server (QA-Werkzeug außerhalb des Repos) hat eine optionale Zusatzdatei-Antwort
  (`LEXONO_CAPTURE_APPEND_FILE`).

### 37.6 Behebung der Entwurfs-Lücke und zweier Selbstbestätigungswege (14.10.2026)

Kein neues Feature, sondern die Behebung des FAIL aus §37.4. Gewählt wurde Option 1 (ohne Schema-Eingriff).

**Editor-Anzeige (`app/web/draft_editor_router.py`, `draft_editor.html`):**
- Bei jeder Darstellung des Editors wird die Fundstellenprüfung lokal aus dem aktuellen Schreiben
  (`draft_plain_text(draft)`) neu berechnet.
- Sie erscheint als Block „Offene Prüfpunkte – Fundstellen (nicht Bestandteil des Schreibens)“ mit dem Hinweis
  „Stand beim Öffnen des Editors; nach Änderungen Seite neu laden“.
- Der Block ist nie Teil von `draft.content`. Export und Versand enthalten ihn deshalb nicht.
- Ausgabe über Jinja-Autoescape.
- **Fehlerverhalten:** Ein Fehler der Prüfung führt nicht zu einem 500-Fehler. Stattdessen erscheint der ausdrückliche
  Hinweis „Fundstellenprüfung konnte nicht ausgeführt werden – alle Fundstellen dieses Schreibens sind ungeprüft“.
  Es wird also keine Prüfung vorgetäuscht.

**Selbstbestätigung 1 – exportierte Entwürfe (`citation_check._matter_texts`):**
- **Fund im installierten Build:** Jeder Export legt das Schreiben als Aktendokument ab (`generated_from_draft_id`).
  Danach galten die erfundenen Fundstellen der früheren Modellantwort als „aktenbelegt“.
- Beobachtet in der QA-Akte mit 14 Exporten: „I ZR 999/19“ und „§ 9999 BGB“ wurden als Aktenbeleg angezeigt.
- **Behebung:** Von Lexono erzeugte Dokumente zählen nie als Beleg.

**Selbstbestätigung 2 – Kanzleiwissen aus Entwürfen (`citation_check._approved_source_texts`):**
- **Fund aus dem Review:** Ein über das Entwurfs-Feedback übernommener Eintrag enthält die frühere Modellantwort selbst
  (Herkunft „Übernommen aus Entwurfs-Feedback …“, `app/feedback/service.py`). Nach seiner Freigabe hätte er deren
  Rechtsprechungszitate als „freigegebene Quelle“ bestätigt.
- **Behebung:** Solche Einträge zählen nicht als Beleg. Manuell gepflegtes, freigegebenes Kanzleiwissen zählt
  unverändert.

**Tests:**
- `test_exported_draft_documents_never_count_as_file_evidence` war vor der Behebung rot und ist danach grün.
- `test_knowledge_promoted_from_a_draft_is_not_evidence`
- `test_editor_shows_local_citation_check_for_the_current_letter`
- `test_editor_without_citations_shows_no_citation_block`
- `test_editor_citation_block_escapes_content`
- `test_editor_stays_usable_when_citation_check_fails`

**Installierter Build:**
- Neu gebaut und installiert (12:29). `bundlecheck_fq.py`: alle 17 Merkmale vorhanden, einschließlich der beiden
  Selbstbestätigungs-Korrekturen, des Fehlerverhaltens, des Templates und des CSS.
- Gleiche QA-Umgebung wie in §37.2: QA-DB, Fang-Server per Loopback, keine echten Modellaufrufe.

| Fall (installierter Build) | Ergebnis |
|---|---|
| Editor bestehender QA-Entwürfe (14 Exporte in der Akte) | **PASS**: I ZR 999/19 „nicht verifiziert (weder in der Akte noch in freigegebenen Quellen)“; § 9999 BGB, § 12 StVG, § 823 BGB, § 2 und § 99 QASynthG „Existenz lokal nicht prüfbar“ (ohne Gesetzesdaten); § 4 KSchG „nur in Aktendokumenten“ (zu Recht: hochgeladene Aktenunterlage) |
| Editor mit QA-Gesetzesdaten (danach wieder entfernt) | **PASS**: § 99 QASynthG „im amtlichen Gesetzestext NICHT gefunden“; § 9999 BGB „nicht prüfbar“; § 4 KSchG „nur Akte“; § 823 BGB (kuratiert) und § 2 QASynthG (amtlich) gefunden (2 von 5); Hinweis „Stützung nicht geprüft“ stets vorhanden |
| Kanzleiwissen „Übernommen aus Entwurfs-Feedback …“, freigegeben, mit I ZR 999/19 | **PASS**: bleibt „nicht verifiziert“. Positivkontrolle: derselbe Text als manuell gepflegter, freigegebener Eintrag zählt (1 von 3 gefunden). Beide Einträge danach gelöscht. |
| Frisch erzeugte Entwürfe im Schriftsatz-Pfad dieses Builds | **PASS**: Editor zeigt die Hinweise (§ 823/§ 9999 BGB nicht prüfbar; I ZR 999/19 nicht verifiziert; § 12 StVG nicht prüfbar; „§ 535 Rn. 12“ nicht einzeln geprüft, im Sammelhinweis genannt) |
| Export DOCX/PDF (5 Entwürfe) | **PASS**: nur das Schreiben, kein Hinweisblock |
| Payload am Fang-Server (6 Anfragen) | **PASS**: kein Originalwert der synthetischen Personen im Klartext, kein Prüfhinweis, kein Schlüssel |

**Beobachtung (vorbestehend, kein F-/Q-Fehler) – Fail-Closed-Block bei „Klagefrist“:**
- Eine synthetische Antwort mit dem Satz „Die Klagefrist nach § 4 KSchG ist zu beachten.“ wird im Schriftsatz-Pfad
  blockiert („nicht ausreichend anonymisierter Wert“). Ohne diesen Satz entsteht der Entwurf.
- **Ursache:** Im Gesamt-Sachverhalt der QA-Akte maskiert die NER „Klagefrist“ (Satzanfang nach „Die“) als Person
  (`[PERSON_03]`). Die Leck-Prüfung der Antwort erkennt den Originalwert dann zu Recht.
- „Mietsache“ wird über den Aktentitel „QA-SYNTH Zabelhuber Mietsache“ ebenfalls als Person maskiert.
- **Herkunft:** Im Prozess auf einer Kopie der QA-DB reproduziert, ohne Netz, Abbruch vor dem Versand. Identisch auf
  `d0b1c74` (vor F) und auf HEAD, also weder durch F noch durch Q verursacht.
- **Isoliert geprüft:** Der Satz allein, mit Review-Kandidaten oder im Gesamtkontext aller QA-Dokumente wird nicht
  maskiert. Die Fehlmaskierung entsteht erst im vollständigen Sachverhalt.
- **Bewertung:** sichere Richtung (Übermaskierung, Fail-Closed), keine Lockerung. Gleiche Klasse wie die dokumentierten
  Fehlalarme in §37.3.
- **Restrisiko Nutzbarkeit:** Eine echte Modellantwort, die einen solchen fälschlich maskierten Fachbegriff wörtlich
  wiederholt, wird blockiert. Offener Punkt für eine künftige F-Fortsetzung, mit konkretem Reproduktionsfall.

**Regression (Stand nach §37.6):**

| Prüfung | Ergebnis |
|---|---|
| Relevante Testgruppen: Fundstellenprüfung, Editor, Drafting, Entwurf-Export/Versionierung, Feedback/Kanzleiwissen, Legal-Benchmark-Tests, Web-Entwürfe/Schriftsatz, Exporte, Normzitate-Privacy | 485 bestanden, 0 fehlgeschlagen |
| Volle Suite (erster Lauf vor dem Commit) | Abgebrochen: Claude Code hat den Lauf wegen knappen Systemspeichers gestoppt. Bis dahin (ca. 11 %, 519 Tests) 0 Fehler. Ersetzt durch den Gesamt-QA-Lauf auf cc71f99 (unten). |
| Legal-E2E (Arbeitsbaum 95c12fc + §37.6) | keine kritischen Befunde, 0 Klartext, 0 fremde Akte, 0 blockierte Aufgaben, benötigte Werte 29/29 und 20/20, Schriftsatz 4/4 und 3/3, erfundene Fundstellen markiert 8/8 und 6/6, keine Fremdzuordnung, fremder Anhang 7/7 blockiert |
| Neue Fehler neben den 4 bekannten Logo-Tests | keine (Gesamt-QA-Lauf auf cc71f99, unten) |

**QA-Umgebung:**
- Testgesetze und Testeinträge im Kanzleiwissen entfernt.
- Fang-Server und QA-Instanz beendet.

**Gesamt-QA-Abschluss auf `cc71f99` (14.10.2026):**

Sauberer Arbeitsbaum, abgesehen von den fremden Logo- und Asset-Änderungen, die nicht verändert oder wiederhergestellt
wurden. HEAD entspricht `origin/main`.

| Prüfung | Status | Ergebnis |
|---|---|---|
| Volle Suite (`memwatch.ps1`, `tests -q`, 9:15 min) | **PASS bis auf die bekannten Logo-Fehler** | 4551 bestanden, **4 fehlgeschlagen**, 1 übersprungen, 6 xfailed, 1 xpassed. Die Suite ist **nicht** fehlerfrei. |
| Die 4 Fehler | bekannte Logo-Tests, keine neuen Fehler | `test_design_refresh.py`: `test_full_logo_lockup_assets_have_a_real_alpha_channel`, `test_dark_logo_icon_is_byte_identical_to_the_light_logo_icon`, `test_dark_logo_has_identical_overall_geometry_to_light_logo`, `test_dark_logo_wordmark_is_recolored_to_white`. Ursache: Die Logo-Dateien unter `assets/branding/` sind durch fremde Änderungen gelöscht. |
| xpassed | kein neuer Befund | `test_known_limit_name_that_is_also_a_common_noun[Richter]` (nicht-striktes xfail, dokumentierte Grenze); identisch in allen früheren vollen Läufen |
| F/Q-Tests: Komposita und Feldzeilen, Namen mit zweitem Signal, Pseudonymisierungs-Benchmark, Fundstellenprüfung, Editor, Drafting-Streaming, Legal-Benchmark-Tests, Normzitate | **PASS** | 305 bestanden, 0 fehlgeschlagen (2 xfailed und 1 xpassed sind dokumentierte Grenzen) |
| Legal-E2E auf `cc71f99` ohne lokale Änderungen | **PASS** | keine kritischen Befunde, 0 Klartext-Lecks, 0 fremde Akte im Payload, keine blockierten Aufgaben, benötigte Werte 29/29 und 20/20, Schriftsätze 4/4 und 3/3, erfundene Fundstellen markiert 8/8 und 6/6, keine falsche Aktenzuordnung, fremder Anhang 7/7 blockiert |
| Normale Instanz danach | **PASS** | Health 200, nur Port 8000 offen; Debug-Port 9333 geschlossen; kein Fang-Server (18080) |

**Ressourcen:**
- Vor dem Lauf wurde die normale Instanz regulär über das Fenster beendet. **Ein erzwungenes Beenden war nicht nötig.**
- Freier Arbeitsspeicher: 7,9 GB vor dem Beenden der Instanz, 8,5 GB beim Start der Suite, mindestens 5,0 GB
  während der Suite (Abbruchschwelle von `memwatch.ps1`: 1,5 GB, nicht erreicht), 4,8 GB nach dem Neustart der
  Instanz.
- Kein Abbruch, keine Wiederholung.

**Offene Risiken (unverändert):**
- „Klagefrist“ wird im Gesamt-Sachverhalt als Person fehlmaskiert. Eine Antwort mit diesem Wort wird korrekt
  fail-closed blockiert (Beobachtung oben).
- Im Artikel-Benchmark lecken 21 von 96 Namen nach Artikeln (§37.3).
- „Messbeamter“ wird in einer eigenen DOCX-Zeile maskiert (§37.3).
- Die 4 Logo-Tests bleiben rot, bis über die fremden Logo-Änderungen entschieden ist.

## 38. P1.1: Senden von Tabellendaten im installierten Windows-Build (14.10.2026, Ausgang `85ccef3`)

Nur synthetische Daten, isolierte QA-Datenbank, Fang-Server per Loopback: kein Aufruf eines echten Modells, keine
Schlüssel ausgegeben.

**Ausgangsbefund (§34.13/§34.14, Fall 31):**
- Eine Word-Erbentabelle als Chat-Anhang meldete in der installierten Oberfläche zweimal „Senden fehlgeschlagen“.
- Im Prozess und über `/dashboard/chat/send` (TestClient) lief derselbe Fall fehlerfrei.
- Ursache war offen.

### 38.1 Ursache: Fehler im Prüfskript, kein Produktfehler

- **Im Prüfskript:** `pii_qa31.py`, ein QA-Werkzeug außerhalb des Repos, wurde von `make_table_qa31.py` aus einer
  Vorlage erzeugt. Der Blockersatz schnitt dabei `document.save(path)` mit ab: Das Tabellendokument wurde erzeugt,
  aber nie gespeichert.
- **Im Browser:** CDP `DOM.setFileInputFiles` setzte den nicht existierenden Pfad. Der Browser zeigte eine Datei mit
  0 Byte. Der `fetch` auf `/dashboard/chat/send-stream` scheiterte mit „Failed to fetch“, bevor der Server erreicht
  wurde.
- **In der Oberfläche:** `chat.html` meldet genau diesen Fall (`fetch` abgelehnt vor dem ersten Stream-Ereignis) als
  „Senden fehlgeschlagen“. Es gab keinen Payload und keinen Cloud-Aufruf.
- **Belegt durch Reproduktion im installierten Build** (Fetch-Protokoll im Seitenkontext):

  | Variante | Beobachtung |
  |---|---|
  | Datei nicht gespeichert (wie Originallauf) | Anhang im Eingabefeld 0 Byte; `POST /dashboard/chat/send-stream` → „Failed to fetch“; kein Payload |
  | Datei gespeichert | Anhang 36.913 Byte; Versand erreicht den Server, Nachricht und Anhang („Verarbeitet“) werden gespeichert |

- **Web- vs. Desktop-Pfad:** Die Desktop-Oberfläche sendet über `send-stream`. Der bisherige Regressionstest deckte
  nur `/send` ab. Serverseitig verhalten sich beide gleich (siehe Tests unten).
- **Tabellenextraktion, Dokumentaufbereitung, Payload und Serialisierung:** ohne Befund. Zellen werden als
  `Spalte | Spalte | Spalte` übergeben, in der Reihenfolge der Zeilen.

**Stand `0e53ede`: kein Produktcode geändert.** Korrigiert wurden nur die beiden QA-Skripte außerhalb des Repos
(`document.save(path)` ergänzt). Die anschließende Produktkorrektur der Fehlermeldung (Owner-Entscheidung) steht in
§38.6.

### 38.2 Nebenbefund in der alten QA-Akte (kein Tabellenfehler, sichere Richtung)

- Mit gespeicherter Datei wurde der Versand in der QA-Akte „QA-SYNTH Zabelhuber Mietsache“ fail-closed blockiert
  („weiterhin erkennbare Muster“, Kategorie `aktenzeichen`).
- **Ursache, im Prozess auf einer Kopie der QA-DB reproduziert:** Alle Rest-Treffer sind „I ZR 999/19“. Das Zeichen
  stammt aus den §37-Testdaten derselben Akte: exportierte Entwürfe mit der erfundenen Fundstelle und daraus abgeleitete
  Fristausschnitte.
- **Kontrolle:** Dieselbe Konversation ohne Tabellendokument wird identisch blockiert, die Tabelle ist also nicht
  ursächlich.
- **Isoliert:** Derselbe Satz wird weder maskiert noch gemeldet. Der Effekt entsteht nur im Kontext dieser QA-Akte.
- **Bewertung:** Blockade in sicherer Richtung, ausgelöst durch QA-Testdaten. Nicht gelockert, nicht weiter verfolgt
  (außerhalb von P1.1). **Offener Punkt:** In einer Akte mit erfundenen Rechtsprechungszitaten aus exportierten
  Entwürfen kann der Chat fail-closed blockieren. Ob das außerhalb der QA-Akte auftritt, ist NICHT VERIFIZIERT.
- Die QA-Rückfrage betraf dabei wieder „Klagefrist“ (§37.6); das Prüfskript beantwortet Rückfragen automatisch mit
  „personenbezogen“.

### 38.3 Ergebnis im installierten Build (neue, saubere synthetische Akte)

- **Build:** installierter Build vom 14.10.2026, 12:29 (Stand cc71f99, gleicher Code wie `85ccef3`; §37.6:
  `bundlecheck_fq.py`). Neu gebaut wurde nicht, da kein Produktcode geändert wurde.
- **Akte:** neu angelegt, „QA-SYNTH P11 Erbsache Tabelle“, Mandant „Quirin Zabelhuber“ (synthetisch).

| Prüfung | Ergebnis |
|---|---|
| Versand Erbentabelle (DOCX, 3 Zeilen + Kopfzeile, Verkehrswert) über die Oberfläche | **PASS**: genau ein Cloud-Aufruf (Fang-Server), keine Rückfrage, Status `success` |
| Tabellenstruktur und Reihenfolge im Payload | **PASS**: „Erbe \| Geburtsdatum \| Erbquote“, danach `[MANDANT_01]`, `[PERSON_01]`, `[PERSON_02]` je mit „(Datum entfernt) \| 1/3“ in Originalreihenfolge |
| Juristisch relevante Werte | **PASS**: „1/3“ dreimal, „312.000,00 EUR“ erhalten |
| Payload-Prüfung vor der Cloud-Grenze | **PASS**: kein Name, kein Geburtsdatum oder -jahr, kein Schlüssel |

**Nebenbeobachtung, sichere Richtung:** Das Wort „Erbsache“ im Aktentitel wird als `[ORGANISATION_01]` maskiert. Das
ist eine Übermaskierung im Titel, kein Leck. Nicht behoben.

### 38.4 Regressionstests (`tests/test_web_chat.py`, Desktop-Pfad `send-stream`)

- `test_table_docx_via_send_stream_keeps_structure_and_order_without_birthdates`:
  - Versand gelingt;
  - Kopfzeile und drei Zeilen stehen in Reihenfolge;
  - Quoten und Wert sind erhalten;
  - kein Geburtsdatum und kein Name.
- `test_empty_docx_attachment_via_send_stream_is_never_sent_to_the_cloud` (Gegenfall 0-Byte-Datei): sichtbarer Hinweis
  „nicht an die Cloud-KI gesendet“, kein Cloud-Aufruf, kein Serverfehler.
- **Vorher/Nachher:** Beide Tests bestehen schon auf dem unveränderten Produktcode. Sie halten das nachgewiesene
  Verhalten fest; es gab keinen roten Zustand, weil kein Produktfehler vorlag. Der bestehende `/send`-Test bleibt
  grün.

### 38.5 Regression und offene Punkte

| Prüfung | Ergebnis |
|---|---|
| Relevante Testgruppen: Web-Chat, Datenschutz-Rückfrage, Dokumentbereitschaft, Extraktion, Datums-Chronologie, Fristen, Gateway, Leck-Prüfung, Chat-Service, Legal-Benchmark-Tests, Pseudonymisierungs-Benchmark, Komposita, Fundstellenprüfung | **PASS**: 600 bestanden, 0 fehlgeschlagen |
| Legal-E2E auf Stand `0e53ede` | **NICHT VERIFIZIERT**: Der Lauf wurde von Claude Code wegen knappen Systemspeichers gestoppt. Der Produktcode war identisch mit `cc71f99`, wo der Benchmark am selben Tag bestand (§37.6). Neuer Lauf nach der Produktkorrektur: §38.6. |
| Volle Suite | auf diesem Stand nicht erneut ausgeführt; letzter voller Lauf auf `cc71f99`: 4551 bestanden, 4 fehlgeschlagen (nur die bekannten Logo-Tests), §37.6 |
| Installierter Build (Stand `0e53ede`) | **PASS**, siehe §38.3 (kein Neubau nötig, kein Produktcode geändert) |
| Normale Instanz danach | Health 200, nur Port 8000, Debug-Port 9333 geschlossen, kein Fang-Server |

**Offene Punkte:**
- Mögliche Chat-Blockade durch erfundene Rechtsprechungszitate aus exportierten Entwürfen im Aktenkontext (§38.2):
  sichere Richtung, außerhalb der QA-Akte NICHT VERIFIZIERT.
- „Erbsache“ wird im Aktentitel als Organisation maskiert (Übermaskierung, sichere Richtung).
- Die Fehlermeldung „Senden fehlgeschlagen“ unterscheidet nicht, ob ein Anhang unlesbar ist oder das Netz fehlt.
  Echte Nutzer wählen Dateien über den Dateidialog; ein danach verschobener oder gelöschter Anhang würde dieselbe
  Meldung zeigen. → Behoben in §38.6.
- Unverändert aus §37.6: „Klagefrist“-Fehlmaskierung (korrekte Fail-Closed-Blockade), 21/96 Namen nach Artikeln im
  Benchmark, „Messbeamter“ in einer eigenen DOCX-Zeile, die 4 Logo-Tests.

### 38.6 Produktkorrektur: klare Meldung bei unlesbarem Anhang (Owner-Entscheidung, 14.10.2026)

**Anlass:**
- Das in §38.1 reproduzierte Symptom kann auch echten Nutzern begegnen: Ein Anhang wird nach der Auswahl verschoben
  oder gelöscht, etwa eine temporäre Outlook-Datei.
- Bisher meldete der Chat dann „Senden fehlgeschlagen. Bitte erneut versuchen.“ und gab den Anhang unverändert zurück.
  Ein erneuter Versuch scheiterte deshalb immer gleich.
- Die Owner-Entscheidung lautete, die Meldung zu präzisieren.

**Änderung (nur `app/web/templates/chat.html`, nur im Fehlerpfad):**
- Der Fehlerpfad ist der `.catch`-Zweig von `fetch("/dashboard/chat/send-stream")`, und zwar nur dann, wenn noch kein
  „start“-Ereignis kam.
- In diesem Fall liest `findUnreadableFiles` jeden Anhang lokal an. Unlesbare Anhänge werden in der Meldung benannt
  („Der Anhang „…“ kann nicht mehr gelesen werden (verschoben oder gelöscht?). Bitte die Datei neu auswählen.“) und
  **nicht** zurückgegeben.
- Lesbare Anhänge und der Text bleiben im Composer. Ohne unlesbaren Anhang bleibt die bisherige Meldung.
- Ein erfolgreicher Versand und der Fall einer bereits gespeicherten Nachricht (Fall B, `finish()`) sind unverändert.
- Kein Einfluss auf Server, Pseudonymisierung, Privacy Gates, Fail-Closed oder Mandantentrennung.

**Erster Versuch im installierten Build durchgefallen (Ursache belegt, korrigiert):**
- Zunächst wurde nur ein 1-Byte-Ausschnitt gelesen (`file.slice(0, 1)`).
- Im WebView2 meldet ein nicht mehr vorhandener Anhang aber Größe 0. Der Ausschnitt eines 0-Byte-Blobs wird nie von der
  Platte gelesen und „gelingt“. Die Messung im installierten Build ergab: `slice1` = 0 ohne Fehler, `arrayBuffer()` des
  ganzen Anhangs → `NotFoundError`.
- **Korrektur:** Anhänge mit Größe 0 werden vollständig gelesen (das kostet nichts), sonst ein Byte. Ein großer Anhang
  wird also nie ganz geladen.
- Vor dem Neubau im laufenden WebView2 geprüft: Der nie gespeicherte und der nach der Auswahl gelöschte Anhang gelten als
  unlesbar. Der lesbare Anhang und eine tatsächlich vorhandene leere Datei gelten als lesbar; die leere Datei behandelt
  der Server wie bisher (Test in §38.4).

**Ergebnis im installierten Build (Neubau 14.10.2026, 19:05; Template enthält die Korrektur; saubere synthetische
Akte; Fang-Server):**

| Fall | Ergebnis |
|---|---|
| A: Anhang existierte nie (wie im Originallauf) | **PASS**: Meldung nennt die Datei; Composer ohne die Datei, Text erhalten; kein Payload |
| B: Anhang nach der Auswahl gelöscht | **PASS**: dieselbe Meldung; Datei nicht zurückgegeben, Text erhalten; kein Payload |
| C: ein lesbarer und ein gelöschter Anhang | **PASS**: nur der gelöschte wird benannt; der lesbare bleibt im Composer |
| C: erneuter Versand ohne weitere Eingabe | **PASS**: genau ein Payload; Tabellenstruktur und Reihenfolge erhalten, „1/3“ dreimal, „312.000,00 EUR“; kein Name, kein Geburtsdatum; nur ein Anhang im Payload |
| Payload-Prüfung (alle Läufe) | **PASS**: ein Payload, kein Klartext, kein Schlüssel |

**Tests:**
- Neu: `test_network_failure_names_unreadable_attachments_and_does_not_restore_them` (Struktur des Fehlerpfads,
  wie die übrigen Template-Tests). Das Verhalten selbst ist im installierten Build geprüft (oben).
- **Vorher:** Den neuen Test gab es nicht; das Verhalten wurde im installierten Build vor der Korrektur gemessen (Fälle
  A–C alle mit der alten Meldung und zurückgegebenem Anhang).
- **Nachher:**
  - Web-Chat: 166 von 166 bestanden.
  - Chat-Gruppen inkl. Design-Tests: 370 bestanden, 4 fehlgeschlagen. Die 4 sind nur die bekannten Logo-Tests
    (`test_design_refresh.py`, gelöschte Logo-Dateien).

**NICHT VERIFIZIERT:**
- **Legal-E2E auf diesem Stand:** Der Lauf wurde erneut von Claude Code wegen knappen Systemspeichers gestoppt und nicht
  wiederholt. Das lokale Ollama-Modell des Nutzers belegt rund 2,8 GB. Der Benchmark treibt die Serverpfade über den
  TestClient und führt das geänderte Browser-JavaScript nicht aus; der serverseitige Produktcode ist unverändert
  gegenüber `cc71f99` (bestanden, §37.6).
- **Volle Suite:** auf diesem Stand nicht erneut ausgeführt.

**Normale Instanz danach:** Health 200, nur Port 8000, Debug-Port 9333 geschlossen, kein Fang-Server.

## 39. P1.2: Aktenzuordnung – „Ihr Zeichen“, bekannte Absender, Mehrdeutigkeit (14.10.2026, Ausgang `ff879b8`)

Nur synthetische Daten. Kein Modellaufruf: Die Zuordnung ist rein lokal (`app/matching`).

### 39.1 Befund (reproduziert mit dem echten Matcher und der echten Vorschlagskarte, synthetische In-Memory-DB)

| Fall | vorher |
|---|---|
| „Ihr Zeichen: \<eigenes Az.\>“ (unbekannter Absender) | kein Signal, kein Vorschlag (§34.10: 0/4) |
| Gerichts-Az. vor „Ihr Zeichen“ | nur das **erste** Aktenzeichen wurde gelesen; das eigene war verdeckt |
| **zwei Aktenzeichen verschiedener Akten** in einer Nachricht | **automatisch der zuerst genannten Akte zugeordnet, samt Anhängen**; das zweite wurde ignoriert |
| bekannte Mandantenadresse allein | 0,3 < Prüfschwelle 0,4 → Entscheidung `no_match`; die Karte zeigte trotzdem einen Kandidaten |
| bekannte Adresse, Mandantin mit **zwei** Akten | Karte zeigte eine Akte (30 %) mit Ein-Klick-„Übernehmen“; die gleich gut passende zweite Akte war unsichtbar |
| Adresse von A + „Ihr Zeichen“ von B | Karte schlug die Akte von A vor; der Widerspruch wurde nicht erkannt |
| **gelöschte Akte** (Soft-Delete) | vom Matcher weiter bewertet: Aktenzeichen und bekannter Absender → **automatisch in die gelöschte Akte**. Die manuelle Auswahl bot gelöschte Akten an, „Übernehmen“ nahm sie an. |

**Ursachen im Code:**
- `_MATTER_REFERENCE_PATTERN` kannte nur „Az.“ und „Aktenzeichen“.
- Gelesen wurde nur der erste Treffer (`search`).
- `email_match` allein lag unter der Prüfschwelle.
- Die Karte zeigte nur `candidates[0]`.
- `_score_all_matters`, `_load_assignable_matters` und `accept_matter_suggestion` ignorierten `deleted_at`; überall
  sonst (API, Suche, lokale KI) wird danach gefiltert.

### 39.2 Owner-Entscheidung

Die Frage war, ob ein einzelnes „Az.: \<eigenes Az.\>“ von einem unbekannten Absender künftig nur noch ein
Vorschlag mit Bestätigung sein soll (vorher automatisch, §34.10 4/4). Entscheidung: **ja.**

Automatisch zugeordnet wird nur noch bei **zwei unabhängigen Signalen derselben Akte**: genanntes Aktenzeichen
**und** bekannter Absender. Hinzu kommen wie bisher: keine Mehrdeutigkeit, kein Absender-Konflikt, ausreichende
Klassifikation.

### 39.3 Änderungen

- **`app/matching/matcher.py`**
  - **Aktenzeichen-Erkennung:** Erkannt werden „Ihr Zeichen“/„Ihre Zeichen“ („Ihr Az.“ und „Ihr Aktenzeichen“ waren
    schon abgedeckt). „Unser Zeichen“ bleibt bewusst außen vor, weil es das Zeichen des Absenders ist.
  - **Alle Aktenzeichen statt nur des ersten:** Treffen zwei Akten, sind beide gleich bewertet. Das ergibt die
    bestehende Ambiguität und damit eine Prüfung.
  - **Automatik nur bei Bestätigung durch zwei Signale** (`corroborated`); die Begründung nennt „Nur ein
    Zuordnungssignal …“.
  - **Bekannte Adresse allein:** immer `needs_review` (Vorschlag), nie `no_match` und nie eine Automatik.
  - **Gelöschte Akten:** werden nicht bewertet.
- **`app/web/router.py`**
  - Die Vorschlagskarte nennt gleich gut passende weitere Akten (`alternative_titles`, gleiche Marge wie die
    Ambiguität).
  - Die manuelle Auswahl blendet gelöschte Akten aus.
  - „Übernehmen“ beantwortet eine gelöschte Akte mit 404, wie die API.
- **`app/web/templates/partials/message_detail.html`:** Hinweis „Mehrdeutig: Ebenso gut passt …“.
- **`scripts/legal_bench/e2e.py`:** „korrekt“ heißt jetzt: richtige Akte zugeordnet **oder** zur Prüfung
  vorgeschlagen. Neu gezählt werden `aktenzeichen_allein_automatisch` und `ihr_zeichen_allein_automatisch`.
  Fremdzuordnung bleibt kritisch.

**Unverändert und nicht gelockert:**
- Prüfschwelle und Auto-Schwelle, Gewichte und Ambiguitätsmarge;
- Konfliktregel (§34.10) und Klassifikationssperre;
- die Vorschlagskarte wendet nichts an. Nur „Übernehmen“ oder die manuelle Auswahl ordnen zu.

### 39.4 Tests

**Neu:**
- `test_matching_matcher.py`:
  - „Ihr Zeichen“ in sechs Schreibweisen;
  - mit bekanntem Absender automatisch;
  - „Unser Zeichen“ nie;
  - Gerichts-Az. davor;
  - zwei Aktenzeichen → mehrdeutig;
  - bekannte Adresse mit zwei Akten → mehrdeutig;
  - Konflikt;
  - unbekanntes Zeichen → `no_match`;
  - zweite eigene Akte → genau diese;
  - gelöschte Akte nie Kandidat.
- `test_matching_service.py`:
  - Aktenzeichen allein verschiebt keinen Anhang;
  - zwei Aktenzeichen verschiedener Mandanten verschieben keinen Anhang.
- `test_web_inbox.py`:
  - Karte mit Mehrdeutigkeitshinweis bzw. ohne;
  - gelöschte Akte weder in der Auswahl noch per „Übernehmen“.
- `test_main_mail_ingestion.py`: Im echten Abrufpfad bleibt eine Nachricht mit Aktenzeichen von unbekanntem Absender
  unzugeordnet (`matter_match_needs_review`).

**Angepasst (sie hielten das alte Verhalten fest):**
- Aktenzeichen allein → automatisch;
- bekannte Adresse allein → `no_match`;
- Service-, Audit- und Abruf-Tests mit Aktenzeichen allein: jetzt mit bekanntem Absender, ihr Zweck (Anhänge
  verschieben, Audit) bleibt erhalten;
- Benchmark-Test: zusätzlich „Ihr Zeichen“ 1/1 erkannt, Aktenzeichen allein 0/1 automatisch.

**Vorher/Nachher:**
- **Vorher:** Die neuen Tests liefen auf dem unveränderten Code (`ff879b8`, temporärer Worktree): 18 fehlgeschlagen.
  Der Test zur gelöschten Akte, später ergänzt, schlug vor der Korrektur mit `auto_assigned` statt `no_match` fehl.
- **Nachher:** alle grün.

| Prüfung | Ergebnis |
|---|---|
| Relevante Testgruppen (API, Audit, End-to-End, Mail-Abruf, Matching, Posteingang, Akten, Mandanten, Benchmark-Tests, Suche, Klassifikation), Endstand | **PASS**: 384 bestanden, 0 fehlgeschlagen |
| Volle Suite (`memwatch.ps1`, einmal; Start vor der Korrektur der gelöschten Akten) | 4574 bestanden, **4 fehlgeschlagen = nur die bekannten Logo-Tests** (`test_design_refresh.py`), 1 übersprungen, 6 xfailed, 1 xpassed; mindestens 4,9 GB frei, kein Abbruch |

### 39.5 Installierter Build (Neubau 14.10.2026, 20:34)

- **Archivprüfung `bundlecheck_p12.py`:** alle 9 Merkmale vorhanden (Matcher, Router, Template).
- **Testdaten:** synthetisch in der QA-Datenbank; nach der Prüfung vollständig entfernt (5 Nachrichten, 4 Akten,
  2 Mandanten).
- **Fang-Server:** 0 Anfragen. Die Zuordnung ist lokal.

| Fall | Ergebnis (echte Vorschlagskarte im Posteingang) |
|---|---|
| „Ihr Zeichen“, unbekannter Absender | **PASS**: richtige Akte vorgeschlagen, keine Warnung |
| zwei Aktenzeichen | **PASS**: Hinweis „Mehrdeutig: Ebenso gut passt …“ mit der zweiten Akte |
| bekannte Adresse, Mandantin mit zwei Akten | **PASS**: Hinweis „Mehrdeutig“ mit der zweiten Akte |
| Konflikt: Adresse A, „Ihr Zeichen“ B | **PASS**: B vorgeschlagen, Warnung nennt die Akte des Absenders |
| Aktenzeichen einer gelöschten Akte, bekannter Absender | **PASS**: gelöschte Akte weder vorgeschlagen noch in der Auswahl; nur die aktive Akte des Absenders als Vorschlag (34 %) |
| Ansehen der Karten | **PASS**: keine Nachricht zugeordnet |

Nicht im installierten Build geprüft, durch Tests abgedeckt:
- der echte Postfach-Abruf, mangels Testpostfach; abgedeckt durch `test_main_mail_ingestion.py` über die echte
  Abrufschleife;
- „Übernehmen“ einer gelöschten Akte per manipuliertem Formularwert; abgedeckt durch `test_web_inbox.py`.

### 39.6 Restrisiken

- **Mehr manuelle Bestätigungen:** Jede Nachricht mit Aktenzeichen von einem unbekannten Absender (Gericht,
  Gegenseite, Versicherer) braucht jetzt eine Bestätigung im Posteingang. Das ist die gewollte Folge der
  Owner-Entscheidung.
- **Absender in mehreren Akten:** Eine bekannte Adresse, die in vielen Akten steht (z. B. ein Gegenanwalt als
  Beteiligter), erzeugt Vorschläge mit Mehrdeutigkeitshinweis. Kein Risiko für Fehlzuordnung, aber mehr Hinweise.
- **Abweichende Schreibweisen:** Aktenzeichen mit Leerzeichen oder anderer Schreibweise als `reference_number` werden
  weiterhin nicht erkannt (exakter Abgleich, unverändert).
- **Themen-Ähnlichkeit:** Sie erzeugt kleine Rauschkandidaten (Score ≈ 0,04). Sie bleiben unter der Prüfschwelle
  und ohne Wirkung, wie bisher.

### 39.7 Legal-E2E auf diesem Stand (Arbeitsbaum `ff879b8` + §39, einmal ausgeführt)

Der Lauf startete erst, nachdem das lokale Ollama-Modell des Nutzers von selbst entladen war (8,7 GB frei). Zwei
frühere Läufe (§38) waren mit geladenem Modell wegen Systemspeichers gestoppt worden.

| Kennzahl | Ergebnis |
|---|---|
| Kritische Befunde, Klartext-Lecks, fremde Akte im Payload, blockierte Aufgaben | keine, 0, 0, keine |
| Benötigte Werte, Schriftsätze, erfundene Fundstellen markiert | 29/29 und 20/20; 4/4 und 3/3; 8/8 und 6/6 |
| Fremder Anhang | 7/7 blockiert |
| Fremdzuordnung (kritisch) | **0** |
| „Az.“ → richtige Akte zugeordnet oder vorgeschlagen | 5/5 |
| „Ihr Zeichen“ → richtige Akte zugeordnet oder vorgeschlagen | **5/5** (vorher 0/4) |
| Aktenzeichen allein automatisch zugeordnet („Az.“ / „Ihr Zeichen“) | **0/5 / 0/5** (Owner-Entscheidung) |
| Konflikt (Adresse A, Aktenzeichen B) | 7/7 `needs_review` |
| Korpus-E-Mails bekannter Mandantenadressen | 6/6 `needs_review`, also Vorschlag statt wie bisher `no_match`; keine automatische Zuordnung |

**NICHT VERIFIZIERT im Benchmark:** welche Akte bei den Korpus-E-Mails vorgeschlagen wird. Der Runner protokolliert
den Vorschlag nur für die Aktenzeichen-Proben. Dass ein bekannter Absender seine eigene Akte vorgeschlagen bekommt,
ist durch die Tests in §39.4 und durch den installierten Build (§39.5) belegt.

**Normale Instanz danach:** Health 200, nur Port 8000, Debug-Port 9333 geschlossen, kein Fang-Server.

## 40. P2.1: Fristenerkennung und Dokumentklassifizierung (14.10.2026, Ausgang `4d7981c`)

Nur synthetische Daten, rein lokal (regelbasiert, kein Modellaufruf, keine API-Kosten). Pseudonymisierung, Privacy
Gates, Leck-Prüfungen, Fail-Closed und Mandantentrennung sind unverändert. Der Konfidenzdeckel der Klassifikation (0,4)
liegt weiter unter der Sperrschwelle der Aktenzuordnung (0,6).

### 40.1 Messverfahren

- **Skript `scripts/legal_bench/p21_measure.py`:** Es nutzt den echten Verarbeitungspfad (`IntakeService` → Text/OCR →
  `ClassificationService` → `DeadlineAnalysisService`) über den Korpus `legal_e2e_v1` (33 Dokumente, davon 6 Scans).
  Dev und Test werden getrennt ausgewiesen.
- **Entwickelt wurde nur an Dev und an neuen synthetischen Unit-Fällen.** Die Korpusdateien blieben unverändert.
- **Klassifikation**, zweistufig:
  - Kerntypen mit eindeutiger Entsprechung: vertrag, rechnung, klageschrift, bescheid, gutachten, protokoll.
    Präzision und Recall je Lexono-Typ.
  - Rollentypen (schreiben, email, notiz, anlage) haben keinen einzig richtigen Typ. Ein Kerntyp darauf zählt als
    Fehler, alles andere wird zur Sichtprüfung ausgegeben.
- **Fristen**, je Fall, verglichen mit Referenzfristen, Chronologie und Dokumentdaten:
  - richtig, übersehen, Quellenbezug (richtiges Quelldokument);
  - Ereignisdatum als Frist;
  - ungeklärt;
  - persönliches Datum als Frist (kritisch).
- **Baseline:** auf `4d7981c` mit OCR. Ein Kontrolllauf in einem Git-Worktree hatte kein OCR (Tesseract liegt
  unversioniert unter `windows/vendor`) und ist deshalb nicht vergleichbar. Er wurde verworfen.

### 40.2 Ursachen (Code)

**Fristen (`app/deadlines/extractor.py`):**
- **Jedes Datum wurde ein Fristvorschlag.** Ohne Schlüsselwort bekam es nur weniger Konfidenz (0,15), wurde aber
  trotzdem gespeichert: Briefdatum, „Datum:“-Zeile, vergangene Ereignisse.
- **„termin“ war ein Teilwort-Treffer im 60-Zeichen-Fenster.** „(Arzttermin). Die Abmahnung vom 12.02.2026“ wurde so
  zur Frist mit 0,5.
- **Echte Fristen ohne „bis zum“ erhielten nur 0,15:** „ordentlich zum 30.06.2026“; „Klagefrist … endet … am
  23.03.2026, also am 13.04.2026“ (Schlüsselwort mehr als 60 Zeichen entfernt).
- **Keine Unterscheidung** zwischen Frist, Termin und Ereignis; Fristbeginn und Dauer wurden nicht festgehalten.

**Klassifikation (`app/classification/classifier.py`):**
- **Teilwort-Treffer im gesamten Text, der erste Typ der Tabelle gewinnt:**
  - „Abmahnung“ → Mahnung, dadurch wurde die Klageschrift zur Mahnung;
  - „Schlussrechnung“ im Fliesstext → Rechnung;
  - „Namens und in Vollmacht“ → Vollmacht.
- **Kein Typ** für Gutachten, Protokoll, nicht-steuerlichen Bescheid, Urteil/Beschluss oder Anlage → „Unbekannt“.

### 40.3 Änderungen

**Fristen:**
- **Rolle jedes Datums im selben Satz:**
  - *Frist (ausdrücklich):* Fristwort unmittelbar davor („bis (zum)“, „spätestens“, „fällig“, „zahlbar“, „also am“,
    „…frist:“; „zum“ nur mit Beendigungs- oder Ablaufbezug) oder Fristwort danach („läuft die Frist ab“).
  - *Termin:* Terminwort unmittelbar davor oder danach.
  - *Fristbeginn:* auslösendes Ereignis am Datum (Kenntnis, Zugang, Zustellung, Bekanntgabe). Das ergibt einen
    Vorschlag **ohne** Fälligkeitsdatum mit dem Vermerk „Fristende nicht berechnet“; ein solches Datum wird nie
    stillschweigend verworfen.
  - *Ereignis/blosses Datum:* **kein** Vorschlag.
  - *Zeitraumende* („vom … bis …“): keine Frist.
- **Die Begründung nennt Art, Grundlage und Beleg.** Bei „N Wochen nach Zugang … am X, also am Y“ werden Dauer und
  Ereignis/Fristbeginn genannt. Das Fristende wird **nicht nachgerechnet**, weil es keine geprüfte
  Fristberechnungslogik gibt (Zustellfiktion, Wochenende, Feiertag).
- **Relative Fristen** („binnen …“, „innerhalb von …“, „N Wochen nach <Ereignis>“) werden als „Fristdauer – vermutete
  Frist, Voraussetzungen unvollständig“ ausgewiesen, ohne Datum und mit „Fristbeginn fehlt“ bzw. dem genannten
  Ereignis.
- **Matter-Kontext (`local_ai_provider`):** „Möglicher Termin“, „Möglicher Fristbeginn (Fristende offen)“ bzw.
  „Mögliche Fristdauer (Fristende offen)“ statt pauschal „Mögliche Frist“. Altvorschläge ohne Art-Angabe bleiben
  „Mögliche Frist“.
- **Bestehende Fristen bleiben unverändert.** Die Analyse ist idempotent: bereits analysierte Dokumente werden nicht
  neu bewertet, gespeicherte Vorschläge werden weder gelöscht noch umgeschrieben.

**Klassifikation:**
- **Kopf des Dokuments zuerst** (600 Zeichen). Im Fliesstext zählen nur selbstbeschreibende Wendungen: Steuerbescheid,
  Einspruch, Prüfungsanordnung, Steuererklärung, Kündigung.
- **Ausschlüsse:** „Abmahnung“ ≠ Mahnung, „in Vollmacht“ ≠ Vollmacht, ein Verweis auf einen Bescheid („gegen den …“,
  „Betreff: …“) ≠ Bescheid.
- **Neue Typen:**
  - „Urteil/Beschluss“: nur bei Überschriftzeile oder „Im Namen des Volkes“/„für Recht erkannt“, nicht bei
    „BGH, Urteil vom …“;
  - „Bescheid“: nur ausdrückliche Bescheidarten, vor dem Steuerbescheid geprüft;
  - „Gutachten“, „Protokoll“;
  - „Anlage“: nur mit Kennzeichnung „Anlage K1“ oder Dateiname; ein erkannter Inhaltstyp hat Vorrang.
- **OCR:** unscharfer Abgleich (≥ 0,85) nur für Überschriftwörter am Zeilenanfang im Kopf; die Begründung nennt die
  mögliche OCR-Abweichung.
- **„Unbekannt“ bleibt der Normalfall** ohne belastbare Merkmale.

**Datenschutz-Gegenprobe:**
- Alle neuen Typ- und Vorschlagsbezeichnungen liefen durch den echten Gateway: nichts maskiert, keine Rückfrage, keine
  Blockade.
- Persönliche Daten (Geburt, Tod) werden weiterhin ausgeschlossen. Die Bereinigung gespeicherter Altvorschläge
  (`trim_partial_personal_dates`) ist unverändert.

### 40.4 Ergebnis (echter Verarbeitungspfad, OCR aktiv)

**Klassifikation:**

| Kennzahl | Dev vorher → nachher | Test vorher → nachher |
|---|---|---|
| Kerntypen richtig | 4/7 → **7/7** | 0/5 → **3/5** |
| Vertrag (P / R) | 3/3, 3/3 → 3/3, 3/3 | – |
| Rechnung (P / R) | 1/2, 1/1 → 1/2, 1/1 | – |
| Klage/Schriftsatz (P / R) | 0/0, 0/1 → **1/1, 1/1** | – |
| Gutachten (P / R) | 0/0, 0/2 → **2/2, 2/2** | 0/0, 0/1 → **1/1, 1/1** |
| Bescheid (P / R) | – | 0/0, 0/2 → **1/1, 1/2** |
| Protokoll (P / R) | – | 0/0, 0/2 → **1/1, 1/2** |
| „Unbekannt“ insgesamt | 9/20 → 9/20 | 11/13 → 9/13 |
| Fehler (einzeln) | 4 → 1 | 5 → 2 (beide „Unbekannt“, kein falscher Kerntyp) |

- **Je Dokument gegenüber der Baseline:** 6 besser, **0 schlechter**.
- **Dev:** Gleich viele „Unbekannt“, weil zwei vorher falsch als „Mahnung“ eingestufte Rollendokumente (Abmahnung,
  Gesprächsnotiz) jetzt begründet „Unbekannt“ sind.
- **Regression der Erstfassung, im Testsplit gefunden und behoben:** Ein Einspruch gegen einen Bußgeldbescheid
  (vorher richtig „Einspruch“) und eine E-Mail mit Betreff „Bußgeldbescheid“ (vorher „Unbekannt“) wurden zum
  „Bescheid“.
  - Die Behebung ist eine allgemeine Regel (Verweis auf einen Bescheid zählt nicht) mit eigenen synthetischen Tests.
  - Für diese Fehlerklasse ist der Testsplit damit **nicht mehr vollständig unabhängig**.
  - Die Testwerte vor dieser Korrektur: Kerntypen 3/5, Bescheid P 1/3.

**Fristen:**

| Kennzahl | Dev vorher → nachher | Test vorher → nachher |
|---|---|---|
| Referenzfristen gefunden | 9/9 → 9/9 | 3/3 → 3/3 |
| davon im richtigen Quelldokument | 9/9 → 9/9 | 3/3 → 3/3 |
| Vorschläge insgesamt | 41 → **10** (9 Frist, 1 Termin) | 25 → **7** (4 Frist, 2 Fristbeginn ohne Datum, 1 Fristdauer ohne Datum) |
| **Ereignisdatum als Frist** | **27 → 0** | **17 → 0** |
| ungeklärt | 1 → 0 | 1 → 1 |
| persönliches Datum als Frist (kritisch) | 0 → 0 | 0 → 0 |
| übersehene Referenzfristen (kritisch) | 0 → 0 | 0 → 0 |

- **Laufzeit:** Verarbeitung inkl. OCR dev 22,1 s → 22,2 s, test 14,9 s → 14,9 s. Keine messbare Änderung.
- **Kritische Fehler einzeln:** keine falsche Frist aus einem persönlichen Datum, keine übersehene Referenzfrist, kein
  falscher Kerntyp im Testsplit.

### 40.5 Tests

**Neu, synthetische Entwicklungsfälle getrennt vom Korpus:**
- `tests/test_deadlines_kinds.py`: blosses Datum, Ereignis und Zeitraum (kein Vorschlag); ausdrückliche Frist; Fristende
  mit Ereignis und Dauer; Termin (vor oder nach dem Datum); Fristbeginn ohne Datum; Fristdauer ohne bzw. mit Ereignis;
  Geburtsdatum neben einer Frist; Textstelle erhalten.
- `tests/test_classification_types.py`: Urteil/Beschluss, Schriftsatz mit Urteilszitat, Klage mit Abmahnung,
  Bußgeld- und Widerspruchsbescheid, Gutachten, Protokoll, Vertrag, Rechnung, Mahnung; Gegenfälle (Erwähnung im Text,
  „in Vollmacht“, Abmahnung, Urteilszitat); Anlage nur mit Kennzeichnung; OCR-Fehler in der Überschrift; Verweis auf
  einen Bescheid; Unbekannt.
- `tests/test_ai_providers_local.py`: Bezeichnung von Termin, Fristbeginn und Fristdauer im Matter-Kontext.

**Vorher (erste Testfassung, auf dem unveränderten Code):** 21 von 22 Fristtests und 21 von 27 Klassifikationstests
schlugen fehl. Die übrigen sechs Klassifikationsfälle sind Gegenfälle, die schon vorher richtig waren. Die später
ergänzten Tests (Termin nach dem Datum, Fristbeginn, Verweis auf einen Bescheid) liefen nicht auf dem alten Code.

**Angepasst, weil sie das alte Verhalten festhielten:**
- `test_bare_date_without_keyword_gets_low_confidence` → kein Vorschlag;
- drei Ausschnitt-Tests: Eingabe mit „bis zum“, die Prüfung der Fenster- und Absatzgrenze ist unverändert;
- zwei Datenschutz-Tests: Ereignisdaten sind keine Vorschläge mehr. Die Prüfung, dass Geburts- und Todesdaten
  ausgeschlossen bleiben und gespeicherte Altvorschläge bereinigt werden, ist unverändert.

| Prüfung | Ergebnis |
|---|---|
| Betroffene Testgruppen (Fristen, Klassifikation, Dokumente, Chat, Drafting, Export, Suche, Aufgaben, Posteingang, Akten, Datenschutz, Benchmark-Tests) | **1334 bestanden**, 0 fehlgeschlagen, 1 xfailed (bekannte Grenze) |
| Nach der Bezeichnungs-Korrektur (Matter-Kontext, Datenschutz-Datums-Tests) | 114 bestanden |
| Volle Suite (`memwatch.ps1`, einmal, vor der kleinen Bezeichnungs-Korrektur) | 4633 bestanden, **4 fehlgeschlagen = nur die bekannten Logo-Tests** (`test_design_refresh.py`), 1 übersprungen, 6 xfailed, 1 xpassed; mindestens 4,6 GB frei, kein Abbruch |

### 40.6 Installierter Build (Neubau 14.10.2026, 23:35)

- **Archivprüfung `bundlecheck_p21.py`:** alle Merkmale vorhanden (Extraktor, Klassifikator, Schema, Matter-Kontext).
- **Echter Upload-Pfad** (Chat-Anhang in der sauberen synthetischen QA-Akte, Fang-Server, kein echtes Modell):

| Dokument | Ergebnis |
|---|---|
| Bußgeldbescheid (DOCX) | **PASS**: Typ „Bescheid“. Vorschläge: Fristbeginn „zugestellt am 06.08.2026“ (ohne Fälligkeitsdatum), Fristdauer „innerhalb von zwei Wochen nach Zustellung“ (ohne Datum), Termin 25.08.2026 (Anhörung), Frist 31.08.2026 („zahlbar bis zum“). Briefdatum 03.08.2026 und Tattag 14.06.2026: kein Vorschlag. |
| Malerrechnung (DOCX) | **PASS**: Typ „Rechnung“, nur die Frist 04.04.2026 („fällig am“). Rechnungsdatum und Leistungszeitraum: kein Vorschlag. |
| Payload vor der Cloud-Grenze | **PASS**: „Möglicher Fristbeginn (Fristende offen)“, „Möglicher Termin“, „Mögliche Fristdauer (Fristende offen)“, „Mögliche Frist“ jeweils korrekt; kein Schlüssel |

### 40.7 Restrisiken und Grenzen

**Datenlage:**
- Der Korpus ist klein: 33 Dokumente und 12 Referenzfristen.
- Urteil/Beschluss und eine gekennzeichnete Anlage kommen im Korpus nicht vor. Für diese Typen gibt es nur
  Unit-Belege und **keine belastbare Präzisionsaussage**.
- Die Testwerte für „Bescheid“ sind wegen der dort gefundenen Regression nicht mehr vollständig unabhängig (§40.4).

**Bekannte Fehlklassifikationen** (keine Regression, vorher ebenfalls falsch):
- LE-D003/D4: Ein Schreiben mit „Schlussrechnung“ im einzigen Satz wird als „Rechnung“ eingestuft.
- LE-D001/D5: Ein Anwaltsschreiben, das eine fristlose Kündigung nur androht, wird als „Kündigungsschreiben“ eingestuft
  (vorher „Vollmacht“).
- **Grund für beide:** Kurze Dokumente bestehen nur aus dem Kopf, und Erwähnungen sind dort nicht sicher von Titeln zu
  trennen.

**Verbleibende „Unbekannt“** (begründete Unsicherheit, kein falscher Typ): Testsplit LE-T001/D1 (Protokoll) und
LE-T002/D2 (Bescheid).

**Fristen:**
- **Bekannter Fehlalarm, vorbestehend:** „Eichschein … gültig bis 31.12.2025“ (LE-T003) wird als Frist vorgeschlagen.
  Es ist ein Gültigkeitsdatum, keine Frist.
- **Mögliche Lücke:** Fristen in nicht erfassten Formulierungen (ohne „bis“, „spätestens“, „fällig“, „Frist“, „zum“
  mit Beendigungsbezug) werden nicht mehr als blosses Datum vorgeschlagen. Das ist bewusst so (keine Frist aus einem
  blossen Datum), kann aber eine echte Frist übersehen. Im Korpus wurde keine Referenzfrist übersehen (12/12).
- **Termine:** Ob ein Termin vergangen ist oder bevorsteht, wird nicht geprüft.
- **Keine Fristberechnung:** Fristenden werden nie aus Dauer und Ereignis berechnet. Steht das Ende nicht im Text,
  bleibt es offen und verlangt eine anwaltliche Prüfung.
- **Altvorschläge:** Vorschläge aus der Zeit vor dieser Änderung bleiben unverändert gespeichert, auch blosse
  Ereignisdaten. Sie werden weder gelöscht noch umgedeutet und sind über die bestehende Prüfung (bestätigen oder
  ablehnen) zu bereinigen.

### 40.8 Legal-E2E-Benchmark (Arbeitsbaum `4d7981c` + §40, einmal ausgeführt, Ollama-Modell vorher entladen)

| Kennzahl | zuletzt committetes Ergebnis (`e13bffe`) | jetzt |
|---|---|---|
| Fristen: Referenzdaten gefunden | dev 9/9, test 3/3 | dev 9/9, test 3/3 |
| Vorschläge außerhalb der Referenz (alle mit Datum, Termine eingeschlossen) | dev 28 (davon 27 bekannte Falldaten), test 18 (17) | **dev 1** (Ortstermin, jetzt als Termin ausgewiesen), **test 1** (Eichschein „gültig bis“, bekannter Fehlalarm) |
| Kritische Befunde, Klartext-Lecks, fremde Akte im Payload, blockierte Aufgaben | keine, 0, 0, keine | keine, 0, 0, keine |
| Benötigte Werte, Schriftsätze, erfundene Fundstellen markiert | 29/29 und 20/20; 4/4 und 3/3; 8/8 und 6/6 | unverändert |
| Aktenzuordnung | keine Fremdzuordnung | keine Fremdzuordnung; Aktenzeichen bzw. „Ihr Zeichen“ 5/5 |
| Fremder Anhang | 7/7 blockiert | 7/7 blockiert |

**Normale Instanz danach:** Health 200, nur Port 8000, Debug-Port 9333 geschlossen, kein Fang-Server.

## 41. P2.2: Testkorpus Klassifikation/Fristen und Modellvergleich Claude ↔ OpenAI (15.10.2026, Ausgang `e2b3176`)

Nur synthetische Daten. Lexono-Messungen rein lokal. Im Modellvergleich gingen nur pseudonymisierte synthetische Texte
an die Cloud. Die Schlüssel stammen aus der lokalen `.env` (`ANTHROPIC_API_KEY`, `OpenAI_API_KEY`); sie wurden nie
ausgegeben oder protokolliert, die `.env` blieb unverändert und uncommittet. Es gibt keinen automatischen Modellwechsel,
keine neue Fristberechnung und keine neue automatische Zuordnung.

### 41.1 Korpus

`benchmarks/classification_deadlines_v1` (Details und Labels: README dort):
- **Dev, 34 Dokumente:** von der Entwicklungssitzung erstellt. Enthält die Reproduktionen bekannter Fehler (Eichschein
  „gültig bis“, „Schlussrechnung“ im Satz, Kündigungsandrohung, Unfallmitteilung, Urkunde) sowie Urteile und Beschlüsse,
  gekennzeichnete Anlagen, OCR-Scans, lange Dokumente, Tabellen, Widersprüche und ungewöhnliche Fristformulierungen.
- **Test, 33 Dokumente:** **blind** von einem eigenen Agenten verfasst. Vor jeder Codeänderung eingefroren
  (SHA-256 `a2cb8015…54fe9f2`, per Test geprüft).
- **Ablauf:** Messung zuerst auf dem unveränderten Code, Entwicklung nur an Dev, danach eine einzige Schlussmessung.
- **Bewertung:** gemeinsam für alle Quellen (`cd_measure.score`).
- **Baselines gesichert:** Legal-E2E auf `e2b3176`; Dev und Test auf `e2b3176`; historische §40-Messungen kopiert.

### 41.2 Ursachen (Dev, belegt mit `tests/test_p22_dev_regressions.py`, vorher 21 rot)

**Fristen:**
- **Regression aus P2.1:** „binnen **eines** Monats nach Zustellung“ wurde nicht mehr als Dauer erkannt, weil „eines“
  als Zahlwort fehlte. „innerhalb eines Monats“ (ohne „von“) und „Frist von zwei Wochen ab Zugang“ wurden nie erkannt.
- **Gültigkeitsdatum als Frist:** „Eichschein gültig bis …“ wurde zur Frist.
- **Nicht erkannte Fristformulierungen:** „bis Freitag, 05.06.2026“; „Einspruchsfrist läuft am … ab“; eine
  Fristentabelle mit der Spalte „Frist“ (die Zeilen selbst tragen kein Fristwort).
- **Termine:** „Verhandlung/Sitzung **vom** …“ (Verweis auf eine vergangene Sitzung) wurde zum Termin; lange
  Terminformeln („wird anberaumt auf den …“) wurden nicht erkannt.

**Klassifikation:**
- Urteil wurde zum Steuerbescheid, weil die „Rechtsbehelfsbelehrung“ als Steuermerkmal vorrangig war.
- Klage, Mahnung und Briefe wurden zur Rechnung, weil „Schlussrechnung vom …“ bzw. „zu unserer Rechnung vom …“ im Satz
  zählte.
- Kündigungsandrohung wurde zum Kündigungsschreiben. Zusätzlich trennten Abkürzungspunkte („§ 543 Abs. 2 … Nr. 3“) den
  Satz.
- „Stellungnahme Gutachten“ in einer Tabellenzelle wurde zum Gutachten.
- Bei „Einspruch“ über einen PDF-Zeilenumbruch („Einspruch\nein.“) wurde der Einspruch nicht erkannt.
- „Unfallmitteilung“ blieb ohne Typ.

### 41.3 Änderungen (eng begrenzt, Produktivcode)

**`app/deadlines/extractor.py`:**
- Dauer-Muster erweitert („innerhalb (von)“, „Frist von“, „ab/seit“ + Ereignis, „eines“ als Zahlwort). Ein Ende wird
  weiterhin nie berechnet.
- „gültig bis“ ist ein Datum, keine Frist.
- Wochentag zwischen Hinweiswort und Datum wird übersprungen.
- Fristwort früher im Satzteil + „am/bis/zum“ ergibt eine Frist, außer der Satz nennt einen Beginn („begann“).
- Tabellen: Der Spaltenkopf „Frist/Fällig…/Termin“ entscheidet.
- „vom“ ergibt keinen Termin; Terminformeln bis 70 Zeichen.
- **Zurückgenommen:** „Werktage“ als Einheit. Die Legal-E2E-Regressionsprüfung zeigte 119 Dauer-Vorschläge aus den
  Vertragsklauseln eines 50.000-Zeichen-Bauvertrags.

**`app/classification/classifier.py`:**
- Urteil/Beschluss wird zuerst geprüft.
- Rechnung nur mit Rechnungsmerkmal („Rechnung Nr./-nummer/-datum“) oder „…rechnung“ als Überschrift.
- Gutachten/Protokoll nur in Titelstellung (erste zwei Wörter einer Zeile, keine Tabellenzeilen).
- Androhungssätze zählen nicht für „Kündigungsschreiben“. Sie werden am Originaltext erkannt; eine Satzgrenze ist nur
  Punkt + Großbuchstabe, Gegentest: eine echte Kündigung neben einem Vorbehaltssatz bleibt eine Kündigung.
- Leerraum wird normalisiert (PDF-Umbruch).
- „Unfallmitteilung/-anzeige/-aufnahme“ gilt als Protokoll.
- **Rechtsbehelfsbelehrung ist kein Einspruch:** „kann … Einspruch eingelegt werden“, „Einspruch einlegen/einzulegen“
  zählen nicht. Gefunden beim Vorbereiten der Prüfung im installierten Build, eigener Dev-Test; aktive Formen bleiben.
  Die Schlussmessung lief erneut auf diesem Endstand: Test unverändert, Dev nur CD-D026 „Einspruch“ → „Unbekannt“
  (beides zulässig).

**Unverändert:** Konfidenzdeckel 0,4, Pseudonymisierung, Gates, Fail-Closed, idempotente Fristenanalyse (bestehende
Fristen werden nie geändert).

### 41.4 Ergebnis Lexono (echter Verarbeitungspfad, OCR aktiv)

| Kennzahl | Dev (n=34) vorher → nachher | **Test (n=33, unabhängig)** vorher → nachher |
|---|---|---|
| Typ richtig | 25 → 34 | **23 → 24** |
| Kerntypen richtig | 19/24 → 24/24 | **19/26 → 20/26** |
| „Unbekannt“ | 8/34 → 10/34 | 4/33 → 4/33 |
| Fristen gefunden | 13/17 → 17/17 | **10/18 → 12/18** |
| Nicht-Frist als Frist (kritisch) | 1 → 0 | **4 → 0** |
| Termine (falsch) | 3/4 (2) → 4/4 (0) | 3/6 (2) → 3/6 (1) |
| Fristbeginn | 1/1 → 1/1 | 3/3 → 3/3 |
| Fristdauern | 1/6 → 6/6 | **5/10 → 8/10** |
| kritische Fehler | 5 → 0 | **12 → 6** |
| Laufzeit gesamt | 39 s → 39 s | 41 s → 40 s |

**Präzision/Recall je Klasse, Test nachher** (Fallzahlen 1–6 je Klasse):

| Klasse | Präzision | Recall |
|---|---|---|
| Bescheid, Gutachten, Urteil/Beschluss, Anlage, Vollmacht, Kündigungsschreiben | voll (je 1–3 Fälle) | voll (je 1–3 Fälle) |
| Rechnung | 3/4 | 3/3 |
| Vertrag | 3/4 | 3/3 |
| Protokoll | 2/2 | 2/3 |
| Steuerbescheid | 1/2 | 1/1 |
| Klage/Schriftsatz | **2/5** | 2/3 |
| Gerichtliches Schreiben | **0/2** | 0/1 |
| Mahnung | – | **0/2** |
| Einspruch | – | 0/1 |

**Verwechslungen (Test):**
- Briefe und E-Mails → Klage/Schriftsatz (2);
- Brief → Gerichtliches Schreiben;
- Mahnung → Vertrag und Rechnung;
- Einspruch → Steuerbescheid;
- Ladung → Klage/Schriftsatz.

**Bewertung:**
- Dev ist nach der Entwicklung fehlerfrei; der unabhängige Test verbessert sich deutlich weniger. Das ist ein klares
  Zeichen für **Überanpassung**: Die Regeln verallgemeinern nur teilweise.
- Belastbar verbessert (Test): **keine Nicht-Frist mehr als Frist (4 → 0)**, Dauern (5 → 8), Fristen (10 → 12),
  kritische Fehler (12 → 6).
- Die Klassifikation bleibt im Test nahezu unverändert.

**Legal-E2E-Korpus (`p21_measure`):**
- Keine Verschlechterung je Dokument.
- LE-D003/D4 („Schlussrechnung“-Brief) jetzt „Unbekannt“; LE-T001/D1 (Unfallmitteilung) jetzt „Protokoll“; LE-D001/D5
  (Androhung) jetzt „Unbekannt“.
- Der Eichschein-Fehlalarm ist weg. Die Vorschläge bleiben: dev 10, test 6.

### 41.5 Modellvergleich Claude ↔ OpenAI (Phase 2)

**Methode:**
- **Fälle:** dieselben, Dev und Test.
- **Eingabe:** dieselbe, nämlich der von Lexono extrahierte Text inkl. OCR-Fehlern, vor dem Versand mit Lexonos
  Pseudonymisierung (Presidio/NER) bearbeitet.
- **Fail-closed:** Danach lief der Restrisiko-Scan (`detect_all`). Bei einem Rest-Treffer wurde **nicht** gesendet:
  Dev 1/34, Test 5/33.
- **Aufgabe und Bewertung:** dieselbe Aufgabe (ein JSON-Prompt mit Typliste und Fristarten), dieselbe Bewertung.
- **Harness:** `scripts/legal_bench/cloud_compare.py`; Zugangsprüfung ohne Kosten über `cloud_access_check.py`.
- **Modelle und Parameter:**
  - `claude-sonnet-5`, das Lexono-Produktivmodell: Thinking aus, `max_tokens` 1200, kein `temperature`, wie im
    Produktiv-Provider.
  - `gpt-5.5-2026-04-23`, fester Snapshot: `reasoning_effort` none, `max_completion_tokens` 1200, JSON-Antwortformat;
    `temperature` wird von gpt-5.x nicht unterstützt.
- **Kostengrenze, vorab festgelegt:** ein Aufruf je Dokument und Modell, höchstens 80 Aufrufe und 5 USD je Modell,
  10 USD gesamt.

**Ergebnis, identische Teilmenge der gesendeten Dokumente:**

| Kennzahl | Lexono P2.2 (Regeln) | Claude Sonnet 5 | OpenAI GPT-5.5 |
|---|---|---|---|
| **Test (n=28)**: Typ richtig | 20 | **28** | **27** |
| Test: Fristen gefunden | 11/17 | **17/17** | **17/17** |
| Test: Nicht-Frist als Frist | 0 | 0 | 0 |
| Test: Termine (falsch) | 2/5 (1) | 5/5 (**2**) | 5/5 (0) |
| Test: Fristbeginn (falsch) | 3/3 (0) | 3/3 (1) | 3/3 (0) |
| Test: Dauern | 7/9 | 9/9 | 9/9 (+1) |
| Test: kritische Fehler | 6 | 0 | 0 |
| **Dev (n=33)**: Typ richtig | 33 (auf Dev entwickelt) | 29 | 30 |
| Dev: Fristen gefunden | 17/17 | 16/17 | 16/17 |

**Kosten und Laufzeit:**

| | Claude | OpenAI |
|---|---|---|
| Test (28 Aufrufe) | 41.016 Ein- / 1.640 Aus-Token, ≈ 0,098 USD, 35 s | 21.114 / 1.062, ≈ 0,137 USD, 57 s |
| Dev (33 Aufrufe) | 40.024 / 1.818, ≈ 0,098 USD, 52 s | 20.209 / 1.155, ≈ 0,136 USD, 54 s |

- Gesamt einschließlich Probeläufe ≈ 0,51 USD.
- Die Kosten sind aus den gemeldeten Token und öffentlichen Preisangaben geschätzt (Sonnet 5: 2/10 USD, GPT-5.5:
  5/30 USD je 1 Mio. Token, Stand 10/2026) und **nicht** mit dem Anbieter-Dashboard abgeglichen.
- Die Tokenizer unterscheiden sich: Derselbe Text ergibt bei Claude rund doppelt so viele Eingabe-Token.

**Einzelfehler der Modelle:**
- **Claude, Test:** 2× ein vergangenes Datum als Termin, 1× ein Datum als Fristbeginn, das keiner ist.
- **OpenAI, Test:** eine nur über den **Dateinamen** gekennzeichnete Anlage wurde zu „Unbekannt“. Der Harness gibt den
  Modellen nur den Text, nicht den Dateinamen; das benachteiligt beide Modelle bei 1 Test- und 1 Dev-Dokument.
- **Dev, beide:** Abweichungen bei strittigen Rollen-Labels (Zahlungsaufforderung mit Kündigungsandrohung →
  „Mahnung“, Unfallmitteilung → „Unbekannt“, Wiedervorlage nicht als Frist). Die Dev-Labels wurden **nicht**
  nachträglich an die Modelle angepasst.

**Bewertung (Phase 3):**
- Auf dem unabhängigen Test sind **beide Modelle deutlich besser als die Regeln**: Typ 27–28 von 28 gegenüber 20,
  Fristen 17/17 gegenüber 11/17, keine kritischen Fehler.
- **Zwischen Claude und OpenAI gibt es bei n=28 keinen belastbaren Unterschied:** ein Typfehler gegenüber drei
  Datumsart-Fehlern bei gleichen Fristtreffern. Kein Sieger.
- Claude war günstiger (≈ 0,0035 gegenüber ≈ 0,005 USD je Dokument) und im Test schneller. Auch das liegt bei diesen
  Fallzahlen im Bereich einer Einzelmessung.

### 41.6 Datenschutz

- Alle Texte waren synthetisch und wurden vor dem Versand lokal pseudonymisiert. Danach lief der Restrisiko-Scan mit
  fail-closed: 6 von 67 Dokumenten wurden **nicht** gesendet (Rest-Treffer „person“/„ort“).
- Der Modellvergleich nutzt die Gateway-**Bausteine**, nicht den Produktiv-Gateway. Dessen Allowlist kennt keinen Zweck
  „Klassifikation“, und ein neuer Zweck wäre eine Architekturentscheidung.
- Das Siegel und die Antwort-Leck-Prüfung des Produktivpfads sind im Harness nicht nachgebildet. Für synthetische Daten
  ist das vertretbar; **für echte Daten ist dieser Harness nicht zugelassen**.
- **Folge für ein Produkt:** Rund 15 % der Testdokumente (5/33) würden von einer cloudbasierten Klassifikation
  fail-closed ausgeschlossen und müssten lokal bewertet werden.
- Fristdaten gingen durch die Pseudonymisierung nicht verloren (lokal geprüft). Geburtsdaten werden maskiert.

### 41.7 Tests und QA

| Prüfung | Ergebnis |
|---|---|
| Neue Tests (`test_p22_dev_regressions.py`, `test_classification_deadlines_corpus.py` inkl. Hash-Prüfung) | PASS; vorher 21 Dev-Regressionsfälle rot |
| Betroffene Testgruppen (Fristen, Klassifikation, Dokumente, Chat, Drafting, Export, Suche, Aufgaben, Posteingang, Matching, Datenschutz, Benchmarks, Mail-Abruf), Endstand | **1381 bestanden**, 0 fehlgeschlagen, 1 xfailed |
| Volle Suite | **NICHT VERIFIZIERT**: Der Lauf wurde von Claude Code wegen knappen Systemspeichers bei etwa 15 % gestoppt und nicht wiederholt. Bis dahin 707 bestanden, fehlgeschlagen nur die 4 bekannten Logo-Tests (`test_design_refresh.py`). |
| Legal-E2E-Korpus (`p21_measure`) | keine Verschlechterung je Dokument (siehe 41.4) |

### 41.8 Offene Risiken

- **Überanpassung der Regeln:** Dev 34/34 gegenüber Test 24/33. Die Restfehler im Test sind Klassen, die erst neu als
  Dev-Fälle nachzubauen sind (Teilwort „klage“/„gericht“ in Briefen, Mahnung, Einspruch gegen Steuerbescheid, Ladung)
  und dann gegen einen **neuen** blinden Testsplit (v2) zu messen.
- **Kleine Fallzahlen** (Klassen mit 1–6 Fällen). Synthetische Texte derselben Modellfamilie.
- **Dauer-Klauseln in Verträgen** („innerhalb von N Tagen nach …“) erzeugen weiterhin Dauer-Vorschläge. Bewusst
  unverändert; Werktage sind zurückgenommen.
- **Modellvergleich:**
  - nur ein Lauf je Modell, keine Wiederholungsmessung (Streuung unbekannt);
  - Dateiname nicht übergeben;
  - Preise nicht vom Anbieter bestätigt.

### 41.9 Installierter Build (Neubau 15.10.2026, 01:31)

- **Archivprüfung `bundlecheck_p22.py`:** alle P2.2-Merkmale vorhanden (Extraktor, Klassifikator, Matter-Kontext).
- **Echter Upload-Pfad:** Chat-Anhang in der sauberen synthetischen QA-Akte, Fang-Server, kein echtes Modell.

| Dokument | Ergebnis |
|---|---|
| Schreiben mit „Schlussrechnung“-Erwähnung und „bis Freitag, 05.06.2026“ | **PASS**: Typ „Unbekannt“ (vorher „Rechnung“), Frist 05.06.2026 |
| Messprotokoll mit „Eichschein gültig bis 31.12.2026“ und „binnen eines Monats nach Zustellung … Einspruch eingelegt werden“ | **PASS**: Typ „Protokoll“; keine Frist aus dem Gültigkeitsdatum; eine Fristdauer ohne Datum |
| Endurteil mit Rechtsbehelfsbelehrung und „Verhandlung vom 03.03.2026“ | **PASS**: Typ „Urteil/Beschluss“; kein Termin aus dem Verweis |
| Payload vor der Cloud-Grenze | **PASS**: Bezeichnungen „Mögliche Frist/Termin/Fristbeginn“ korrekt, kein Schlüssel |

### 41.10 Legal-E2E-Benchmark (Arbeitsbaum `e2b3176` + §41, einmal, Ollama-Modell vorher entladen)

| Kennzahl | Ergebnis |
|---|---|
| Kritische Befunde, Klartext-Lecks, fremde Akte im Payload, blockierte Aufgaben | keine, 0, 0, keine |
| Benötigte Werte, Schriftsätze, erfundene Fundstellen markiert | 29/29 und 20/20; 4/4 und 3/3; 8/8 und 6/6 |
| Fristen: Referenzdaten / außerhalb der Referenz | dev 9/9 / 1 (Ortstermin als Termin); test 3/3 / **0** (vorher 1: Eichschein) |
| Aktenzuordnung | keine Fremdzuordnung; „Az.“ bzw. „Ihr Zeichen“ 5/5; Aktenzeichen allein automatisch 0/5 |
| Fremder Anhang | 7/7 blockiert |

**Normale Instanz danach:** Health 200, nur Port 8000, Debug-Port 9333 geschlossen, kein Fang-Server.

## §42 P2.3/P2.4: Blinder Testkorpus v2 (104 Dokumente) und Modellvergleich mit Wiederholungsläufen (15.10.2026)

**Ziel:**
- Die Verallgemeinerung der Regeln aus §40/§41 auf einem **neuen, größeren, blinden** Testsplit messen.
- Die in §41.8 benannten Restfehlerklassen nur über **neue Dev-Fälle** angehen.
- Claude und OpenAI auf identischen Fällen mit Dateiname und mehreren Läufen vergleichen.

**Ausgangsstand:** `07c919d` (= origin/main). Der P2.2-Split `test.json` (`a2cb8015…`) blieb unverändert.
Nur synthetische Daten.

### 42.1 Korpus v2

| Split | Datei | Dokumente | SHA-256 |
|---|---|---|---|
| Entwicklung v2 | `cases/dev_v2.json` | 8 | `e75d8bc00c33ea88cf366450d4220366bdad327959d53d0efa4e0f5c10ad294a` |
| Blinder Test v2 | `cases/test_v2.json` | 104 | `eb84225cc2b8bcb18de59a62f78658c8c43061298e5f160b0e44d2eca4933981` |

- **Erzeugung `test_v2`:** vier getrennte Agenten (A Gericht, B Korrespondenz, C Behörden/Steuer/Finanzen,
  D Verträge/Gutachten/Protokolle/Grenzfälle), je 26 Dokumente.
- **Blindheit:** Die Agenten hatten keinen Zugriff auf `app/`, `tests/`, `scripts/`, den Korpusordner, diese Datei,
  `.agentic/` und `FUTURE_ROADMAP.md`. Sie sahen auch nicht die Teildateien der anderen Agenten.
- **Labels:** Sie wurden **vor** jeder Messung fachlich vergeben („erfahrene Kanzleikraft“) und nicht aus Vorhersagen
  abgeleitet.
- **Zusammenführung:** Die Teile wurden mechanisch zusammengeführt (Skript ohne Inhaltsausgabe), validiert, mit
  LF-Zeilenenden geschrieben und **vor** der ersten Messung eingefroren.
- **Hash-Prüfung:** `tests/test_classification_deadlines_corpus.py` prüft den Hash und die Disjunktheit zu allen
  anderen Splits.
- **Einschränkung der Blindheit:** Die Fallgruppen hat die Entwicklungssitzung im Auftrag vorgegeben, z. B. „gültig
  bis“, Erwähnungen, KW-Fristen, generische Dateinamen. Text und Labels verfassten die Agenten allein.
- **Zusammensetzung `test_v2`:**
  - 75 Kerntyp- und 29 Rollendokumente; 18 Typen von Anlage bis Vollmacht, 24 × „Unbekannt“.
  - Formate: 42 PDF, 24 DOCX, 17 TXT, 12 Scan, 9 schlechter Scan.
  - Daten: 76 Fristen, 21 Termine, 10 Fristbeginne, 36 Dauern ohne Enddatum, 261 Nicht-Fristen, 25 nicht bewertet.
  - Fallgruppen: 17 Tabellen; Geburtsdaten; vergangene Verhandlungen; „gültig bis“; Abmahnung ≠ Mahnung;
    Kündigungsandrohung; widersprüchliche Daten/Beträge; irreführende oder generische Dateinamen; E-Mail-Ketten;
    lange Verträge mit abstrakten Fristen.
- **`dev_v2`:** acht neue Texte. Sie bilden die Fehlerklassen des **P2.2-Testsplits** nach: Teilwort „klage“/„gericht“
  in Briefen, Ladung, 2. Mahnung, Einspruch gegen Steuerbescheid, Zeilenumbruch im PDF, „bis einschließlich“ mit
  OCR-Fehler. Sie wurden erstellt, ohne `test_v2` einzusehen.
- **Folge:** Der P2.2-Testsplit `test.json` ist **nicht mehr unabhängig**, weil seine Fehlerklassen in die Entwicklung
  eingeflossen sind. Er bleibt als Regressionsmessung erhalten.

### 42.2 Messreihenfolge (Lexono, echter Pfad inkl. OCR)

1. **Baseline:** `test_v2` auf dem unveränderten Stand `07c919d` gemessen
   (`results_lexono_test_v2_07c919d.json`). Vom Ergebnis wurden nur Gesamtzahlen gelesen, keine Einzeldokumente.
2. **Entwicklung** ausschließlich gegen `dev`, `dev_v2` und Unit-Tests (`tests/test_p23_dev_regressions.py`: vorher
   10 rot, danach grün).
3. **Eine Schlussmessung** von `test_v2` (`results_lexono_test_v2_p23.json`). Danach gab es **keine** weiteren
   Regeländerungen. Die dort neu sichtbaren Fehler sind unten nur dokumentiert.

### 42.3 Änderungen (Produktivcode, minimal)

**`app/deadlines/extractor.py`:**
- **Weicher Zeilenumbruch:** Ein Zeilenumbruch beendet einen Satz nur noch, wenn er „hart“ ist: nach `.!?;`, an
  einer Leerzeile oder in Tabellenzeilen (`|`). Vorher trennte jeder Umbruch eines PDF-Fließtexts das Hinweiswort
  („bis zum“, „bestimmt auf“) vom Datum in der Folgezeile.
- **OCR-Variante:** Die Formel „bis einschließlich“ erkennt auch OCR-Varianten („einschlieBlich“, „einschliesslich“).

**`app/classification/classifier.py`:**
- „Mahnung“ wird vor „Rechnung“ geprüft („2. Mahnung … unsere Rechnung Nr. …“).
- **Gericht und Klage/Schriftsatz** zählen nur noch, wenn das Schlüsselwort im **ersten Wort einer Zeile** steht
  (Briefkopf/Überschrift). Für Klage/Schriftsatz gelten zusätzlich Antragsformeln („beantragen wir“, „an das …gericht“).
  Bloße Erwähnungen in Briefen und E-Mails ergeben den Typ nicht mehr.
- **Einspruch gegen Steuerbescheid:** Die Bescheid-Referenz zählt im Steuerbescheid-Bereich nicht als
  Steuerbescheid. Rechtsbehelfsformeln wie „Einspruch einlegen“ sind kein Einspruch.

**Unverändert:**
- Schnittstellen, Schwellen (Klassifikation höchstens 0,4, Sperre 0,6) und Fristberechnung. Fristen werden nur
  vorgeschlagen und nie automatisch übernommen.
- Aktenzuordnung, Privacy-Gateway, Pseudonymisierung, Leak-Detection.

### 42.4 Ergebnis Lexono auf `test_v2` (n=104, echter Pfad inkl. OCR, rein lokal)

| Kennzahl | vorher `07c919d` | nach P2.3 |
|---|---|---|
| Typ richtig | 64/104 | **84/104** |
| Kerntypen richtig | 52/75 | 63/75 |
| „Unbekannt“ vorhergesagt (Gold „Unbekannt“: 24) | 10 | 19 (davon 17 richtig) |
| Fristen gefunden / übersehen | 63/76 / 13 | 66/76 / 10 |
| Nicht-Frist als Frist | 5 | 6 |
| Termine richtig (falsch) | 9/21 (2) | 12/21 (3) |
| Fristbeginn richtig (falsch) | 9/10 (2) | 9/10 (2) |
| Dauern erkannt (zusätzlich) | 27/36 (+15) | 27/36 (+15) |
| **Kritische Fehler** (übersehene Frist + Nicht-Frist als Frist) | 18 | **16** |
| Laufzeit (104 Dokumente inkl. Rendern, OCR, Import) | 130 s | 130 s (≈ 1,25 s/Dokument) |

**Je Dokument:**
- Klassifikation: 20 besser, **0 schlechter**.
- Fristen: 7 Dokumente besser, **3 neue Fehler**.

**Neue Fristfehler durch den weichen Zeilenumbruch, nicht behoben:**
- Eine vergangene Kündigung („kündigte das ↵ Mietverhältnis … zum …“) wird als Frist erkannt.
- „Datum: … ↵ Anhörung im Bußgeldverfahren“ wird als Termin erkannt.
- „innerhalb eines Monats nach ↵ Bekanntgabe, spätestens am …“: Die Frist wird übersehen, weil die Fristbeginn-Regel
  über das Komma greift.

Diese Fehler sind auf `test_v2` sichtbar geworden. Eine Korrektur jetzt wäre Optimierung am Testsplit. Sie werden als
Fehlerklasse für den nächsten Zyklus mit neuen Dev-Fällen und einem frischen blinden Split (v3) geführt.

**Je Klasse, Präzision/Recall nach P2.3 (kleine Klassen, Werte nur als Hinweis):**
- **Schwach:**
  - Kündigungsschreiben P 3/7: 3 Briefe mit Androhung → Kündigung, dazu je 1 Gericht und 1 Mahnung.
  - Rechnung P 6/9.
  - Steuerbescheid P 5/9: Bescheid, Urteil und Briefe → Steuerbescheid.
  - Urteil/Beschluss R 5/7.
  - Vertrag R 7/9.
- **Gut:**
  - Klage/Schriftsatz 6/6 (vorher P 4/11).
  - Gerichtliches Schreiben P 6/8 (vorher 2/11).
  - Mahnung R 4/5 (vorher 1/5).
- Formate: PDF 34/42, DOCX 19/24, TXT 12/17, Scan 11/12, schlechter Scan 8/9. Die OCR ist **nicht** der Engpass.
- **Kleine Untergruppen:** Prüfungsanordnung (1), Sonstiges (1), Steuererklärung (2), Einspruch/Vollmacht (je 3).
  Hier sind keine Aussagen möglich.

**Regression auf den älteren Splits:**
- `dev` 34/34 mit allen Fristen; `dev_v2` 8/8.
- P2.2-`test` (nicht mehr unabhängig): Typ 24 → 30/33, Fristen 12 → 14/18, kritisch 6 → 4.
- Legal-E2E-Korpus (`p21_measure`): unverändert bis auf ein Dokument, das von „Gericht“ zu „Unbekannt“ wechselt
  (neutral).

### 42.5 Modellvergleich (P2.4)

**Gleich wie in §41.5:**
- Harness `cloud_compare.py`.
- Lokale Pseudonymisierung und fail-closed-Restscan.
- Dieselbe Aufgabe und dieselbe Bewertung (`cd_measure.score`).
- Eingabe ist der von Lexono extrahierte Text inkl. OCR-Fehlern aus der Schlussmessung.

**Abweichungen von P2.2 (bewusst):**
- **Dateiname:** Er wird als erste Zeile mitgegeben („Dateiname: …“, wird mit pseudonymisiert). Der Prompt erwähnt
  diese Zeile. Lexono sieht den Dateinamen ebenfalls.
- **Wiederholungen:** drei Läufe je Modell auf allen gesendeten Dokumenten.
- **Kostengrenze vorab:** je Modell höchstens 330 Aufrufe und 3 USD über alle Läufe.

**Modelle und Parameter (unverändert):**
- `claude-sonnet-5`: Thinking aus, `max_tokens` 1200, kein `temperature`.
- `gpt-5.5-2026-04-23`: `reasoning_effort` none, `max_completion_tokens` 1200, JSON-Antwortformat.

**Versand:**
- 99/104 gesendet; **5 fail-closed** (Rest-Treffer person/organisation), in allen Läufen dieselben.
- Fehlende Vorhersagen zählen wie in §41 als „Unbekannt“ ohne Daten.
- **0 API-Fehler.**

**Ergebnis auf allen 104 Dokumenten** (Cloud: Mittelwert [Min–Max] über 3 Läufe):

| Kennzahl | Lexono P2.3 | Claude Sonnet 5 | OpenAI GPT-5.5 |
|---|---|---|---|
| Typ richtig | 84 | **95 [95–95]** | 92,3 [92–93] |
| Kerntypen richtig (75) | 63 | 69 | 69 |
| Fristen gefunden (76) / übersehen | 66 / 10 | 75 / 1 | **76 / 0** |
| Nicht-Frist als Frist | 6 | 1,3 [1–2] | **0,3 [0–1]** |
| Termine richtig (21) / falsch | 12 / 3 | 20 / 5,3 [5–6] | 20 / **1,7 [0–3]** |
| Fristbeginn richtig (10) / falsch | 9 / 2 | 10 / 1 | 10 / **0** |
| Dauern erkannt (36) / zusätzlich | 27 / 15 | 30 / 30 [29–31] | 30 / 34,7 [34–36] |
| **Kritische Fehler** | 16 | 2,3 [2–3] | **0,3 [0–1]** |

- Auf der gesendeten Teilmenge (n=99; erwartet 76 Fristen, 20 Termine, 10 Fristbeginne, 31 Dauern) sind die
  Cloud-Werte identisch.
- Lexono auf derselben Teilmenge: Typ 81, Kerntypen 60, Fristen 66, kritisch 16.
- Die fünf nicht gesendeten Dokumente enthalten keine Frist, aber 1 Termin und 5 Dauern. Diese fehlen den
  Cloud-Modellen zwangsläufig.

**Kosten und Laufzeit je Lauf** (99 Aufrufe; Schätzung aus gemeldeten Token und öffentlichen Preisen, **nicht** mit
dem Anbieter-Dashboard abgeglichen):

| | Claude | OpenAI |
|---|---|---|
| Token je Lauf (Ein / Aus) | 156.832 / ≈ 5.890 | 81.400 / ≈ 3.870 |
| Kosten je Lauf | ≈ 0,373 USD (≈ 0,0038 USD/Dokument) | ≈ 0,523 USD (≈ 0,0053 USD/Dokument) |
| Laufzeit je Lauf (sequenziell) | 159 / 147 / 142 s | 183 / 157 / 173 s |
| Summe 3 Läufe | 297 Aufrufe, ≈ 1,12 USD | 297 Aufrufe, ≈ 1,57 USD |

- Dazu kamen Probeläufe ≈ 0,02 USD, insgesamt also ≈ 2,71 USD (P2.2: ≈ 0,51 USD).
- Die Eingabe-Token sind höher als in P2.2, weil die Dokumente länger sind und der Dateiname mitgegeben wird.

**Streuung:**
- Die drei Läufe sind sehr stabil.
- Claude hat in allen drei Läufen 95 Typtreffer. Die Läufe unterscheiden sich nur um je ±1 bei falschen Terminen,
  Nicht-Fristen und zusätzlichen Dauern.
- OpenAI streut um ±1 Typ und bei den Terminen (0–3 falsch).
- Wiederholungsläufe zeigen also kaum Zufallsrauschen. Das bedeutet **nicht**, dass das Ergebnis auf andere Texte
  übertragbar ist.

**Fehlerbilder:**
- **Claude:** häufiger vergangene oder bloß erwähnte Daten als Termin (≈ 5 je Lauf); 1–2 Nicht-Fristen als Frist.
- **OpenAI:** etwas mehr Typfehler, u. a. Anlage-Überschätzung (P 9/11) und Rechnung; mehr zusätzliche Dauern.
- **Beide:** Präzision der zusätzlichen Dauern schlecht (+30 bzw. +35). Die Label-Konvention „abstrakte
  Vertragsklauseln zählen nicht“ wird von beiden nicht übernommen. Das ist teils eine Definitionsfrage und **kein**
  kritischer Fehler.

### 42.6 Bewertung (Phase 3)

**Generalisierung der Regeln:**
- Auf dem neuen blinden Split verbessert P2.3 die Klassifikation deutlich: 64 → 84, 20 Dokumente besser, keines
  schlechter.
- Die Fristen verbessern sich kaum: kritische Fehler 18 → 16, drei neue Fehler aus derselben Änderung.
- Der Abstand zu Dev (34/34, 8/8) bleibt groß. Die Regeln verallgemeinern also **teilweise**.
- Die verbleibenden 16 kritischen Fristfehler sind das eigentliche Produktrisiko des lokalen Pfads. Fristen bleiben
  Vorschläge mit menschlicher Prüfung.

**Cloud gegenüber Regeln:**
- Beide Modelle sind auf identischen Fällen klar besser.
- Fristen: 75–76/76 gegenüber 66/76.
- Kritische Fehler: ≈ 0–3 gegenüber 16.
- Typ: 92–95 gegenüber 84.

**Claude gegenüber OpenAI:**
- **Kein belastbarer Sieger.**
- Claude hat 2–3 Typtreffer mehr und ist ≈ 30 % günstiger.
- OpenAI hat weniger Datumsart-Fehler (Termin falsch 1,7 gegenüber 5,3) und weniger kritische Fehler (0,3 gegenüber
  2,3).
- Die Unterschiede betreffen wenige Dokumente einer einzigen synthetischen Quelle. Über die Wiederholungen sind sie
  stabil, aber nicht auf echte Kanzleitexte übertragbar.
- Für Fristen, das sicherheitsrelevantere Kriterium, liegt OpenAI auf diesem Korpus leicht vorn; für die
  Klassifikation Claude.

**Kein Modellwechsel umgesetzt.**
- Die produktive Klassifikation und Fristerkennung bleibt lokal.
- Eine cloudgestützte Variante wäre ein neuer Gateway-Zweck und damit eine Architektur- bzw. Owner-Entscheidung.

### 42.7 Datenschutz

- Nur synthetische Texte.
- Vor dem Versand lokal pseudonymisiert und mit Restscan geprüft, fail-closed: 5/104 (≈ 5 %) nicht gesendet; in
  P2.2 waren es 5/33.
- Schlüssel kamen aus der lokalen `.env`. Sie wurden weder ausgegeben noch protokolliert noch in Ergebnisdateien
  geschrieben; `.env` wurde nicht geändert.
- Die Ergebnisdateien enthalten nur pseudonymisierte Vorhersagen und Token-Zählungen.
- **Unverändert:** Privacy-Gateway, Allowlist, Pseudonymisierung, Leak-Detection.
- Der Harness bleibt für echte Daten **nicht zugelassen** (siehe §41.6).

### 42.8 Offene Risiken und nächster Schritt

**Offene Risiken:**
- **Restfehler der Regeln (blind v2):**
  - Kündigungsandrohung → Kündigungsschreiben (3);
  - Bescheid/Urteil/Brief → Steuerbescheid (4);
  - Mahnung → Vollmacht/Kündigung;
  - Beschlüsse → Gericht/Steuerbescheid;
  - Termine 12/21;
  - Dauern-Präzision (+15);
  - die drei Zeilenumbruch-Fehler aus 42.4.
- **Gleiche Urheberschaft:** Alle Texte stammen von Sprachmodellen; echte Kanzleidokumente sind vielfältiger.
  Kleine Klassen mit 1–3 Fällen.
- **Preise** nicht vom Anbieter bestätigt.

**Empfohlener nächster Schritt:**
1. Die Restfehlerklassen aus `test_v2` als **neue** Dev-Fälle nachbauen. Schwerpunkt Fristen: Zeilenumbruch mit
   vergangenem Ereignis, „Datum:“-Kopfzeile, Fristbeginn über Komma, Termine. Danach einen frischen blinden Split
   **v3** einmal messen.
2. **Owner-Entscheidung**, ob eine cloudgestützte Fristen-/Typ-**Zweitmeinung** nur als Vorschlag über einen neuen
   Gateway-Zweck geprüft werden soll. Der Abstand bei den kritischen Fristfehlern (16 gegenüber 0–3) ist der
   stärkste Befund dieser Messung. Fail-closed-Dokumente (≈ 5 %) blieben lokal.

**Reproduzierbar:**
- `python -m scripts.legal_bench.cd_measure --split test_v2 --out … --texts-out …`
- `python -m scripts.legal_bench.cloud_compare --split test_v2 --texts … --out … --runs 3 --max-calls 330 --budget-usd 3`
- `python -m scripts.legal_bench.cd_compare_v2 --out …`

### 42.9 Tests und QA

| Prüfung | Ergebnis |
|---|---|
| Neue/angepasste Tests (`test_p23_dev_regressions.py`; `test_classification_deadlines_corpus.py` mit Hash- und Disjunktheitsprüfung für `test_v2`) | PASS; vorher 10 Dev-Regressionsfälle rot |
| Betroffene Testgruppen (116 Dateien: Fristen, Klassifikation, Dokumente, Chat, Drafting, Export, Suche, Aufgaben, Posteingang, Matching, Datenschutz/Pseudonymisierung/Leak, Benchmarks, Mail, OCR) | **2984 bestanden**, 0 fehlgeschlagen, 6 xfailed, 1 xpassed |
| Volle Suite, in sechs Teilen im Vordergrund nacheinander (nach Entladen des Ollama-Modells) | **4690 bestanden**, fehlgeschlagen nur die 4 bekannten Logo-Tests (`test_design_refresh.py`, fremde Asset-Änderungen); 1 übersprungen, 6 xfailed, 1 xpassed |
| Neubau und Installation (`windows\build.ps1`, stille Installation) | PASS |
| Archivprüfung `bundlecheck_p23.py` | PASS: alle P2.3-Merkmale in Extraktor und Klassifikator vorhanden |
| Legal-E2E-Benchmark (`scripts.legal_bench.e2e`) | Erster Lauf: von Claude Code wegen knappen Systemspeichers gestoppt (Ollama-Modell durch Instanzstart vorgeladen). **Wiederholung: PASS**, siehe 42.10. |

**Installierter Build, echter Upload-Pfad:** Chat-Anhang in der sauberen synthetischen QA-Akte, QA-Datenbank,
Fang-Server, kein echtes Modell.

| Dokument | Ergebnis |
|---|---|
| PDF „2. Mahnung … Rechnung Nr. …“, „letztmalig bis zum ↵ 31.07.2026“ (Umbruch im extrahierten Text nachgewiesen) | **PASS**: Typ „Mahnung“, Frist 31.07.2026 |
| DOCX „Amtsgericht … Ladung … Termin … bestimmt auf den 22.10.2026“ | **PASS**: Typ „Gerichtliches Schreiben“, Termin 22.10.2026, Zustellung als Fristbeginn ohne Fristende |
| DOCX Mandantenbrief, der Gericht und Klage nur erwähnt; „bis einschließlich 15.09.2026“ | **PASS**: Typ „Unbekannt“ (nicht Gericht/Klage), Frist 15.09.2026 |
| Payload vor der Cloud-Grenze | **PASS**: Bezeichnungen „Mögliche Frist“/„Möglicher Termin“, kein Schlüssel |

**Normale Instanz danach:** Health 200, nur Port 8000, Debug-Port 9333 geschlossen, kein Fang-Server.

### 42.10 Legal-E2E-Wiederholung (15.10.2026, 09:48–09:50, Stand `295f591`, sauberer Arbeitsbaum)

**Ursache des Abbruchs um ≈ 08:55:**
- Jeder Start der Lexono-Instanz lädt das lokale Ollama-Modell vor (`app/main.py:139-144` → `OllamaProvider.warm_up`,
  `keep_alive` „30m“, `ollama_provider.py:160,424`).
- Für die QA-Prüfung des installierten Builds wurde die Instanz zweimal kurz hintereinander gestartet (QA-Instanz,
  danach die normale Instanz). Dadurch blieb `llama-server` mit 3,4–3,8 GB im Speicher.
- Freier Speicher lag dabei bei 3,95 von 15,7 GB; dazu kamen die Claude-Code-Prozesse (≈ 1,5 GB).
- Der Benchmark selbst nutzt **kein** Ollama (`RuleBasedLocalAIProvider`, `e2e.py:158`). Er braucht aber für
  Einbettungsmodell, OCR und Verarbeitung bis zu ≈ 3,0 GB (gemessen, siehe unten).
- Claude Code beendete den im Hintergrund laufenden Befehl wegen knappen Systemspeichers. Ein Fehler des Benchmarks
  lag nicht vor.

**Wiederholung, ressourcenschonend:**
- Gewartet, bis `llama-server` entladen war; die Instanz während des Laufs **nicht** neu gestartet.
- Lauf im **Vordergrund**, mit Speicherprotokoll alle 5 s.
- Zuerst die Splits einzeln (dev 93 s, test 37 s), dann der kanonische Gesamtlauf (122 s).
- Messwerte: mindestens 5,1 GB frei; größter Python-Prozess 2,96 GB; `llama-server` während des Laufs nicht geladen.
- Datenschutz-Gates unverändert.

| Kennzahl | dev (4 Fälle) | test (3 Fälle) |
|---|---|---|
| Kritische Befunde / Aufgabenfehler / Dokumentfehler | keine / keine / 0 | keine / keine / 0 |
| Klartext-Lecks, fremde Akte im Payload | 0, 0 | 0, 0 |
| Benötigte Werte im Payload | 29/29 | 20/20 |
| Extraktion / OCR-Werte (schlechter Scan) | 121/121 / 12/12 (12/12) | 82/82 / 27/27 (27/27) |
| Fristen: Referenzdaten / außerhalb der Referenz | 9/9 / 1 (bekanntes Falldatum) | 3/3 / 0 |
| Schriftsätze, Export mit Bearbeitung, erfundene Fundstellen markiert | 4/4, 4/4, 8/8 | 3/3, 3/3, 6/6 |
| Erhalt Keep / Chronologie / Normen | 25/25 / 23/23 / 11/11 | 19/19 / 16/16 / 7/7 |
| Suche Top-1 / Top-5 | 3/4 / 4/4 | 3/3 / 3/3 |

- **Aktenzuordnung:** keine Fremdzuordnung. „Az.“ und „Ihr Zeichen“ werden je 5/5 richtig zugeordnet; allein
  automatisch 0/5.
- **Fremder Anhang:** 7/7 blockiert.
- **Ergebnis: PASS.** Die Werte sind identisch mit §41.10, also keine Regression durch P2.3. Damit ist der in 42.9
  als NICHT VERIFIZIERT geführte Punkt erledigt.

**Betriebshinweis, nicht geändert:** Die Vorwärmung ist ein bewusstes Produktverhalten (§68 Performance). Für
speicherintensive QA auf dieser 16-GB-Maschine gilt: erst laufen lassen, wenn das Modell entladen ist, oder die
Instanz erst nach dem Lauf starten.

## §43 Entwurf: optionale Cloud-Zweitmeinung für Dokumenttyp und Fristen (15.10.2026, NICHT umgesetzt)

**Status:**
- Reine Machbarkeitsanalyse auf Basis von §42 (Commit `295f591`). Es wurde **kein** Produktivcode, kein
  Gateway-Zweck und keine Schnittstelle geändert.
- Eine Umsetzung setzt eine ausdrückliche Änderung des Grundprinzips durch den Owner voraus (43.1).
- „Belegt“ heißt: im Code oder in Messungen nachgewiesen (mit Fundstelle). „Annahme“ heißt: nicht gemessen.

### 43.1 Ausgangslage (belegt)

**Architekturgrenze:**
- `ARCHITECTURE.md`, „Grundprinzip“ (verbindliche Anwaltsvorgabe): Claude ausschließlich für sprachliche
  Textproduktion, „niemals für Aktenanalyse, Aktenzuordnung, Rechtsrecherche, **Fristenbestimmung**“.
- Gleichlautend der Kommentar zu `ALLOWED_PURPOSES` (`app/privacy/security_check.py:38-40`).
- Eine Zweitmeinung zu Fristen und Typ ist damit **heute unzulässig** und keine bloße technische Erweiterung.

**Gateway:** `ClaudePrivacyGateway.prepare_request` (`app/privacy/gateway.py:177`) durchläuft in dieser Reihenfolge:
- Unsichtbare Zeichen entfernen.
- Pseudonymisierung (Presidio/NER).
- Kandidatenprüfung (`review_mode` ask/mask).
- Restrisiko-Scan (`SecurityCheckService.check` → `detect_all`).
- Prüfung der Platzhalter-Integrität.
- HMAC-Siegel (`seal_payload`, geprüft von `require_gateway_seal` in jedem Provider).

Nach der Antwort folgen die Leck-Prüfung (`check_response_placeholder_integrity`) und die Rückübersetzung.

**Payload und Zugang:**
- Payload `ClaudeRequestPayload`: genau acht Felder (Owner-Grenze, `gateway_schema.py`).
- Zulässige Zwecke: nur Textproduktion plus `review_draft` und `chat_response`.
- Kein globaler Cloud-Schalter, keine Cloud-Einstellung je Akte.
- Kostenbremse `CostControlService.check_before_call`; Berechtigung `PERM_CLAUDE_CALL`.
- Protokoll `ApiCallLog`: Zweck, Token, Kosten, Prompt-Hash, Status – **ohne Inhalt**.

**Lokaler Pfad heute:**
- Klassifikation läuft synchron in `DocumentProcessingService.process_document` (`documents/service.py:230`),
  Konfidenz höchstens 0,4. Unter 0,6 gibt es keine automatische Aktenzuordnung (`matching/service.py:67-80`).
- Fristen sind `Deadline` mit `review_status` „unreviewed“/„confirmed“/„rejected“ (`models/deadline.py:51`).
- Eine Bestätigung erfolgt nur manuell (`POST /dashboard/tasks/{id}/review`). Erkannte Fristen werden nie
  automatisch bestätigt.

**Provider:**
- `AnthropicClaudeWritingProvider`: Thinking aus, **kein eigenes Timeout**. SDK-Standard sind 600 s Lesezeit und
  2 Wiederholungen (installiertes SDK geprüft).
- Kein OpenAI-Provider in `app/`; OpenAI existiert nur im Benchmark-Harness.

**Nutzen auf `test_v2`** (§42; synthetisch, 104 Dokumente, 99 gesendet). Gemessen als **Ergänzung** der lokalen
Vorschläge, nicht als Ersatz, je Lauf:

| Kennzahl | Claude (3 Läufe) | OpenAI (3 Läufe) |
|---|---|---|
| Lokal übersehene Fristen, von der Cloud gefunden | **10/10** (alle Läufe) | **10/10** |
| Zusätzliche Cloud-Fristhinweise / davon gelabelte Nicht-Frist | 11–13 / 0–1 | 11–12 / 0 |
| Lokale Nicht-Frist-als-Frist, von der Cloud nicht bestätigt | 5/6 | 5–6/6 |
| Richtige lokale Fristen, von der Cloud nicht bestätigt | **1** | 0 |
| Typ abweichend / dabei Cloud richtig / lokal richtig | 21 / 19 / 5 | 23–24 / 20 / 8–9 |

**Latenz und Kosten (gemessen):**
- Lokale Pseudonymisierung und Restscan auf denselben 104 Texten mit Dateiname: Median 0,22 s, p90 0,45 s,
  max 1,64 s. Blockiert: 5, reproduzierbar.
- API-Zeit je Aufruf: Claude Median 1,31 s, p90 1,61 s, max 8,85 s; OpenAI 1,69 / 2,00 / 3,48 s.
- Lokale Verarbeitung je Dokument: 1,0–2,4 s.
- Eingabe-Token Claude: Median 1.360, max 4.550; ≈ 0,0038 USD je Dokument (Preisannahme 2/10 USD je 1 Mio. Token,
  nicht vom Anbieter bestätigt).

**Folgerungen:**
- Als Ergänzung liefert die Zweitmeinung genau die fehlenden Fristen, mit wenigen falschen Zusatzhinweisen.
- Als Ersatz oder Filter wäre sie **gefährlich**: Eine richtige lokale Frist wäre unterdrückt worden.
- Daraus folgt die Kernregel des Entwurfs: **Die Cloud darf nur hinzufügen und markieren, nie entfernen,
  überschreiben oder bestätigen.**

### 43.2 Vorgeschlagene Architektur

**Datenfluss:**

```
Upload/Mail → [lokal, unverändert] Text/OCR → Klassifikation → Fristvorschläge (unreviewed)
                     │
                     └─ nur wenn: Owner-Schalter an + Akte nicht ausgeschlossen + Berechtigung + Budget
                        + Nutzer klickt „Zweitmeinung anfordern“ (Stufe 1, kein Automatismus)
                              ▼
        SecondOpinionService (neu, außerhalb des Upload-Pfads, eigener Thread wie bestehende Lifespan-Tasks)
                              ▼
        ClaudePrivacyGateway.prepare_request(purpose="second_opinion_document", review_mode="ask")
           · anonymisierter_sachverhalt = extrahierter Text (gekürzt, z. B. ≤ 12.000 Zeichen)
           · schreibauftrag = fester Auftragstext; Dateiname als Teil des Textes → mit pseudonymisiert
           · review_required ODER Rest-Treffer ODER Fehler → BLOCKIERT (kein Versand)
                              ▼ (versiegelte Payload)
        SecondOpinionProvider (neu): require_gateway_seal, Timeout ≤ 20 s, max_retries 0–1,
           Thinking aus, max_tokens ≈ 600, kein Web-Search, JSON-only
                              ▼
        Antwortprüfung: JSON-Schema; typ ∈ ALLOWED_DOCUMENT_TYPES; Daten TT.MM.JJJJ und wörtlich im
           gesendeten Text enthalten; Art ∈ {frist, termin, fristbeginn}; kein Freitext;
           check_response_placeholder_integrity → sonst UNGÜLTIG (verworfen)
                              ▼
        Speicherung getrennt: document_second_opinions (neu) – nie documents.classified_type, nie deadlines
                              ▼
        Vergleich lokal ↔ Cloud → Anzeige als Hinweis; Übernahme nur per Klick als NEUER Fristvorschlag (unreviewed)
```

**Komponenten** (alles neu, nichts am bestehenden Pfad ersetzt):

1. **Zweck `second_opinion_document`** in `ALLOWED_PURPOSES`.
   - Voraussetzung ist die Owner-Änderung des Grundprinzips (43.1) und eine Ergänzung in `ARCHITECTURE.md`.
   - Die bestehende 8-Felder-Payload reicht. Ein neues Feld ist **nicht** nötig: Der Text geht in
     `anonymisierter_sachverhalt`, der Auftrag in `schreibauftrag`.
2. **`SecondOpinionProvider`**, getrennt vom Schreib-Provider:
   - eigener, kurzer Systemprompt (wie Harness §41/§42);
   - explizites Timeout und keine Retry-Kaskade;
   - prüft das Siegel;
   - Modell zunächst nur Claude (`claude-sonnet-5`), weil Anthropic der einzige angebundene Anbieter ist. Ein
     zweiter Anbieter wäre ein neuer Auftragsverarbeiter und braucht eine eigene Entscheidung.
3. **`SecondOpinionService`**:
   - prüft Schalter, Berechtigung (`PERM_CLAUDE_CALL`) und Budget (`check_before_call`);
   - ruft Gateway und Provider auf, validiert, speichert und protokolliert (`ApiCallLog`, `AuditEvent`
     `second_opinion_requested/completed/blocked/invalid`, `cloud_hint_adopted` mit Akteur).
4. **Tabelle `document_second_opinions`** (Migration nötig):
   - Felder: `document_id`, `matter_id`, `model`, `status` (ok/blocked/review_required/timeout/error/invalid/
     budget), `typ`, `daten` (JSON mit Art), Prompt-Hash, Zeitstempel.
   - **Kein** Dokumenttext, keine Pseudonymzuordnung.
5. **Vergleichslogik**, rein lokal und deterministisch. Je Datum: „übereinstimmend“, „nur lokal“, „nur Cloud“,
   „andere Art“. Beim Typ: „übereinstimmend“ oder „abweichend“.
6. **UI** in Dokument- und Fristansicht:
   - Kennzeichnung „Cloud-Zweitmeinung – ungeprüft, kann irren“.
   - „Nur Cloud“: Hinweis mit Fundstelle und Button „als Fristvorschlag übernehmen“. Der Button legt eine **neue**
     `Deadline` mit `review_status="unreviewed"` an, die wie jede andere bestätigt werden muss.
   - „Nur lokal“: Markierung „Cloud hat diese Frist nicht gefunden – bitte prüfen“. Der lokale Vorschlag bleibt
     **unverändert sichtbar**.
   - Typ abweichend: Hinweis ohne Änderung von `classified_type`.

**Ausdrücklich ausgeschlossen:**
- automatische Übernahme, Bestätigung, Änderung von `due_date`;
- Fristberechnung;
- Änderung von `classified_type` oder Aktenzuordnung;
- Versand an andere Stellen als das Gateway;
- Speicherung des Cloud-Rohtexts.

### 43.3 Fail-Closed-Matrix

In allen Fällen bleiben die lokalen Vorschläge unverändert, und es gibt keinen automatischen erneuten Versand.

| Situation | Verhalten |
|---|---|
| Schalter aus, Akte ausgeschlossen, keine Berechtigung, Budget erschöpft, Endpoint nicht erlaubt | kein Aufruf; Status mit Grund |
| Rest-Treffer, Review-Kandidaten (`review_mode="ask"` ohne Dokument-Review-UI), Prüfung wirft Fehler | **blockiert, nicht gesendet**; Hinweis „Zweitmeinung aus Datenschutzgründen nicht möglich“ |
| Dateiname enthält personenbezogene Rest-Treffer | ganze Anfrage blockiert; kein Versand ohne Dateinamen, damit es keinen stillen Umweg gibt |
| Timeout, HTTP- oder SDK-Fehler | Status timeout/error; keine Wiederholung ohne Nutzeraktion |
| Antwort kein gültiges JSON, unbekannter Typ, Datum nicht im gesendeten Text, Platzhalter-Auffälligkeit | Status ungültig; Antwort verworfen, nichts angezeigt |
| Text länger als die Grenze | Status zu_lang oder nur der Kopfteil; Annahme, Entscheidung offen |

### 43.4 Datenschutz- und Fehlerrisiken

- **Prinzipbruch:** Fristenbestimmung in der Cloud widerspricht der verbindlichen Vorgabe. Ohne ausdrückliche,
  dokumentierte Owner-Entscheidung keine Umsetzung.
- **Mehr Daten in der Cloud:** ganze Dokumenttexte statt kurzer Schreibaufträge. Die Pseudonymisierung ist auf
  synthetischen Daten geprüft; vollständige Leckfreiheit ist damit **nicht** bewiesen. Eine
  Datenschutz-Folgenabschätzung und Mandanteninformation (Auftragsverarbeitung) sind nötig.
- **Keine Dokument-Review-UI:** Mit `review_mode="mask"` würden unsichere Begriffe ohne Rückfrage maskiert.
  Der Entwurf wählt deshalb „ask“ und behandelt jede Rückfrage als blockiert. Der Preis ist eine höhere
  Blockadequote; Annahme: über 5 % bei echten Texten.
- **Automatisierungsneigung:** Nutzer könnten Cloud-Hinweise als geprüft wahrnehmen. Gegenmaßnahmen sind
  Kennzeichnung, Pflichtklick und unreviewed-Status sowie Audit der Übernahme.
- **Falsche Beruhigung:** Fehlt eine Frist auch in der Cloud, entsteht keine Warnung. Die menschliche Prüfung
  bleibt Pflicht.
- **Falsche Zweifel:** Gemessen wurde 1 richtige lokale Frist von Claude nicht bestätigt. Deshalb nie ausblenden.
- **Modellabhängigkeit:** Snapshot-Wechsel ändern das Verhalten. Modell-ID protokollieren und Regressionsmessung
  auf `test_v2` vor jedem Wechsel.
- **Synthetische Evidenz:** Alle Nutzenzahlen stammen von modellgenerierten Texten; echte Kanzleitexte sind
  länger und vielfältiger.

### 43.5 Kosten- und Latenzannahmen

Gemessen auf synthetischen Texten mit median 917 und max 7.221 Zeichen:
- ≈ 0,22–0,45 s lokal und ≈ 1,3–1,6 s API;
- zusammen ≈ 2 s je Dokument zusätzlich, asynchron und daher ohne Wirkung auf den Upload.

**Annahmen (nicht gemessen):**
- Echte Schriftsätze und Verträge sind 10- bis 50-mal länger: bei 12.000 Zeichen etwa 4.000–5.000 Eingabe-Token,
  rund 0,01 USD je Dokument.
- Bei 50 angeforderten Zweitmeinungen pro Tag ergäbe das etwa 0,5 USD pro Tag.
- Pseudonymisierungszeit und Blockadequote steigen mit der Textlänge.
- Preise sind nicht vom Anbieter bestätigt.

### 43.6 Offene Fragen (Owner)

1. Wird das Grundprinzip (keine Fristenbestimmung in der Cloud) für eine **nur hinweisende** Zweitmeinung
   geändert? Falls nein, endet der Entwurf hier.
2. Auslösung: nur manuell je Dokument (empfohlen für Stufe 1) oder automatisch für bestimmte Akten oder
   Dokumentarten?
3. Mitgabe des Dateinamens (Nutzen in §42: nur bei Anlagen, die allein über den Dateinamen erkennbar sind).
4. Textgrenze und Umgang mit langen Dokumenten (Kopfteil, Abschnitte, gar nicht).
5. Akten- oder mandantenbezogene Ausschlüsse und Einwilligung bzw. Information der Mandanten.
6. Nur Anthropic oder später zusätzlich ein zweiter Anbieter (eigene Auftragsverarbeitung).

### 43.7 Stufenweiser Testplan

Jede Stufe nur synthetisch; eine Stufe beginnt erst, wenn die vorige PASS ist.

1. **Offline-Vertrag:**
   - Unit-Tests für Antwortvalidierung (Schema, Typliste, Datum muss im Text stehen, Platzhalter) und für die
     Vergleichslogik.
   - Kein Netz, kein Gateway-Zweck.
   - Darf vor der Owner-Entscheidung gebaut werden, wenn der Owner das ausdrücklich freigibt.
2. **Gateway-Integration mit Fang-Server** (`LEXONO_QA_LOOPBACK_CLOUD_ENDPOINT`):
   - neuer Zweck nur in einem Feature-Branch;
   - Siegel-Pflicht, Blockade bei Rest-Treffer und Review, Leck-Prüfung;
   - Payload-Inspektion auf `legal_e2e_v1` und `test_v2`.
3. **Fail-Closed-Tests:** Timeout, 5xx, ungültiges JSON, erfundenes Datum, Budget, Endpoint, Berechtigung. Jeweils
   lokale Vorschläge unverändert und kein Versand.
4. **Benchmark über den echten Gateway-Pfad** (statt Harness): Wiederholung von §42 mit Zielen „übersehene
   Fristen ergänzt“, „falsche Zusatzhinweise“, „Blockadequote“, „Kosten“.
5. **Installierter Build mit QA-Instanz:**
   - UI-Kennzeichnung, Übernahme als unreviewed, Audit-Einträge;
   - keine Änderung bestehender Fristen;
   - Legal-E2E ohne Regression.
6. **Erst danach** wird über einen Pilotbetrieb mit echten Daten entschieden. Voraussetzungen: DSFA und eine
   eigene Owner-Freigabe.

### 43.8 Owner-Entscheidung und Stufe 1 umgesetzt (15.10.2026)

**Entscheidung:**
- Die Cloud-Zweitmeinung wird als **ergänzende, manuell je Dokument angeforderte** Funktion weiter evaluiert,
  zunächst nur mit Anthropic/Claude.
- Lokale Ergebnisse werden nie entfernt, überschrieben oder bestätigt; die lokale Verarbeitung bleibt unverändert.
- In dieser Phase gibt es keine automatische Ausführung und keine produktive Gateway-Integration.
- Das Grundprinzip in `ARCHITECTURE.md` und `ALLOWED_PURPOSES` bleiben deshalb **unverändert**.

**Umgesetzt** wurde nur Stufe 1 aus 43.7: die isolierte, netzfreie Grundlage in `app/second_opinion/`.

`validation.py`:
- `validate_response(raw, sent_text)` nimmt nur das kleine JSON-Schema an:
  - Pflichtfelder `typ`/`frist`/`termin`/`fristbeginn`, optional `fristdauern`;
  - kein Freitext, keine unbekannten Felder, `typ` aus `ALLOWED_DOCUMENT_TYPES`;
  - Daten streng als gültiges `TT.MM.JJJJ`.
- **Jedes Datum muss im tatsächlich gesendeten Text vorkommen.** Erkannt werden auch die Schreibweisen
  1.7.2026, 01.07.26 und 1. Juli 2026.
- Abgelehnt werden außerdem: Platzhalter-Tokens, dasselbe Datum in zwei Arten (widersprüchlich) und Überschreitung
  der Größengrenzen.
- Bei jedem Verstoß wird die **ganze** Antwort verworfen; es gibt keine Teilübernahme.
- `check_length(text, max_chars)`: Ein zu langer Text wird **nicht gekürzt**, sondern nicht gesendet (`zu_lang`).
  Das Modul enthält bewusst keine Kürzungsfunktion.

`comparison.py`:
- Lokale Ergebnisse gehen nur als unveränderliche Schnappschüsse ein (`LocalSnapshot`, `snapshot_from` liest nur).
- `compare` liefert eigene `CloudHint`-Objekte: Quelle „cloud“, Status „ungeprüft“, Kategorien übereinstimmend /
  nur lokal / nur Cloud / andere Art. Ein „nur lokal“-Datum bleibt sichtbar und wird nur markiert.
- Diese Status ergeben **keine** Hinweise: deaktiviert, Datenschutzblock, Rückfrage, zu lang, Budget,
  Zeitüberschreitung, Fehler, ungültig.
- `adopt_hint` nur bei Status `ok`, nur für „nur Cloud“-Daten, nur mit `user_confirmed=True` und benanntem
  Akteur. Ergebnis ist ein **neuer** `DeadlineProposal` mit `review_status="unreviewed"`; der Hinweis selbst bleibt
  ungeprüft.

**Isolation, per Test erzwungen:**
- keine Netz-, Provider-, Gateway-, Datenbank- oder Web-Importe im Paket;
- kein Zweck mit „second“, „zweit“ oder „frist“ in `ALLOWED_PURPOSES`;
- kein anderes Modul unter `app/` bindet das Paket ein;
- keine Migration, keine UI.

**Tests und QA:**

| Prüfung | Ergebnis |
|---|---|
| `tests/test_second_opinion_foundation.py` (71 Fälle) | PASS |
| Mutationsprobe: 8 Mutationen an den Schutzregeln (Datumsabgleich, Widerspruch, Platzhalter, Längengrenze, unreviewed, nur Cloud, Statusprüfung, nur-lokal-Markierung) | 8/8 gefangen |
| Volle Suite, in sechs Teilen im Vordergrund (Ollama-Modell entladen) | **4761 bestanden**; fehlgeschlagen nur die 4 bekannten Logo-Tests (`test_design_refresh.py`, fremde Asset-Änderungen); 1 übersprungen, 6 xfailed, 1 xpassed |
| Lint | **NICHT VERIFIZIERT**: Im Repo ist kein Linter konfiguriert. `py_compile` PASS; Zeilenlänge an die Repo-Konvention angepasst. |
| Installierter Build, UI, echte Cloud-Aufrufe | nicht betroffen bzw. bewusst nicht ausgeführt (Stufe 1 ist nirgends eingebunden) |

**Grenzen und Risiken von Stufe 1:**
- **Kein Leckschutz:** Die Validierung ersetzt **nicht** die Leck-Prüfung des Gateways
  (`check_response_placeholder_integrity` mit den Pseudonym-Zuordnungen). Sie kommt in Stufe 2 zusätzlich davor.
- **Grenzen des Datumsabgleichs:** Er erkennt nur Ziffern- und Monatsnamen-Daten. Ein Datum, das der Text nur
  relativ nennt („in zwei Wochen“), wird abgelehnt. Das ist gewollt: Es gibt keine Fristberechnung.
- **Zweistellige Jahre** werden als 20JJ gelesen.
- **Zeichengrenze offen:** `max_chars` ist noch nicht festgelegt (Owner-Frage 43.6/4).

**Nächster sicherer Schritt (Stufe 2):** Integration mit dem Fang-Server in einem Feature-Branch.
- neuer Zweck nur dort;
- Siegel-Pflicht, Blockade bei Rest-Treffer und Rückfrage, Leck-Prüfung;
- Payload-Inspektion auf synthetischen Korpora.

Vorher braucht es die ausdrückliche Owner-Freigabe für die Änderung von `ALLOWED_PURPOSES` in diesem Branch.

## §44 Produktiver Schreibpfad über das Lexono-Relay: Token-Grenze, Denkmodus, Abschneiden, Timeout (15.10.2026)

**Branch:** `fix/relay-schreibpfad`, von `origin/main` (`757e638`). Er ist unabhängig vom gesperrten
Zweitmeinungs-Branch, damit die Korrektur gesondert geprüft und übernommen werden kann. `main` ist unverändert.

**Reproduziert** (synthetische Daten, echter Pfad: Factory → `GatewayRelayWritingProvider` → `call_gateway_messages` →
Relay-Endpunkt `/v1/relay/messages`; nur das Anthropic-SDK gefälscht):
- Standard-Settings ohne `CLAUDE_MAX_TOKENS` in der `.env`: Der Client sendet `max_tokens=4096`.
- Das Relay (`max_tokens_ceiling = 4000`) antwortet mit **400**, Anthropic wird nicht aufgerufen.
- Der Client wirft `GatewayRelayError("… Status 400")`. Der Nutzer sieht „Interner Fehler bei der Textproduktion“; das
  Protokoll zeigte keinen HTTP-Status, weil der Fehler keinen `status_code` trug.
- **Betroffen** ist jede Relay-Bereitstellung (Produktivpfad §70) ohne eigenen Wert. Die lokale Installation dieser
  Maschine nutzt den direkten Pfad (`LEXONO_GATEWAY_URL` nicht gesetzt).
- `.env.example` setzte 2000. Genau dieser Wert schnitt laut dokumentiertem Fund vom 05.10. längere Antworten ab.

**Ursache:** Zwei unabhängig gesetzte Standardwerte:
- Client 4096: am 05.10. von 2000 angehoben;
- Relay-Kostenschranke 4000: §70, 31.08.

Keine Prüfung hielt beide konsistent, und der Pilot-Szenariotest nutzte 2000 statt des Factory-Standards.

**Änderungen und Begründung:**

| Bereich | Vorher | Nachher | Begründung |
|---|---|---|---|
| `Settings.claude_max_tokens` | 4096 | **4000** | Das Relay ist die maßgebliche Kostenschranke; der Client folgt ihr. Kein Limit erhöht. |
| `.env.example` `CLAUDE_MAX_TOKENS` | 2000 | **4000** | 2000 schnitt nachweislich ab; Kommentar verweist auf die Relay-Obergrenze. |
| Relay-Schreibpfad: Denkmodus | nicht übertragbar (`RelayRequest` ohne Feld) | Client sendet `thinking: {"type": "disabled"}`; Relay reicht genau diesen Wert durch und lehnt jeden anderen mit 422 ab | Gleichstand mit `AnthropicClaudeWritingProvider` (Fund 05.10., `test_write_explicitly_disables_thinking`). Siehe „Denkmodus“ unten. |
| Review-Pfad über das Relay | ohne `thinking` | **unverändert** ohne | Auch der direkte Review-Provider setzt kein `thinking`; ohne belegten Fehler keine Änderung. |
| Abschneiden | `stop_reason` verworfen, Abbruch unsichtbar | Relay gibt `stop_reason` zurück; der Client protokolliert `max_tokens` inhaltsfrei | Sichtbarkeit; der Text bleibt wie im direkten Pfad beim Aufrufer. |
| Relay-Upstream | 60 s Timeout, SDK-Standard **2 Wiederholungen** | **55 s, 0 Wiederholungen** (`ANTHROPIC_TIMEOUT_SECONDS`, `ANTHROPIC_MAX_RETRIES`, Bereich 0–2) | Der Client gibt nach 60 s auf. Vorher konnten bis zu zwei weitere kostenpflichtige Versuche ohne Abnehmer laufen. Regel: `(Wiederholungen + 1) × Upstream-Timeout < Client-Timeout`. |
| `GatewayRelayError` | ohne Status | `status_code` (None bei Netzwerkfehler/Timeout) | `_log_provider_exception` protokolliert den Status inhaltsfrei (z. B. 400, 502). |

- Ein älteres Relay ignoriert das neue Feld `thinking`; die neue `RelayResponse` ist nur additiv.
- Datenschutz- und Zweckprüfungen sind unverändert: Siegel-Pflicht im Provider, keine Inhalte in Fehlermeldungen
  und Logs.

**Denkmodus – Prüfung:**
- **Belegt:** Live-Messung vom 05.10. im direkten Pfad (Kommentar und Test in `anthropic_writing_provider.py`): Ohne
  Abschalten verbrauchte das Modell bis zu 82 % von `max_tokens` für unsichtbares Denken, und Texte brachen mitten im
  Satz ab. Der direkte Schreibpfad schaltet ihn deshalb ab.
- Der Relay-Schreibpfad konnte das bisher nicht: kein Feld, kein Parameter im SDK-Aufruf. Er wich damit von einer
  bereits getroffenen, belegten Entscheidung ab.
- Gegen das echte Modell über das Relay wurde **nicht** gemessen, weil keine Cloud-Aufrufe erlaubt sind.
- Die Änderung ist auf den Schreibpfad begrenzt und clientgesteuert. Das Relay lässt nur das Abschalten zu, nie ein
  Denkbudget (Kostenkontrolle).

**Offene Punkte und Annahmen:**
- **Lange Entwürfe:** Bei einer Ausgaberate von rund 60–80 Token/s (Annahme, nicht gemessen) dauern 4000 Token etwa
  50–65 s. Sehr lange Entwürfe können den Upstream-Timeout von 55 s erreichen und werden dann als Fehler (502)
  gemeldet, ohne Wiederholung.
  - Abhilfe wäre ein größeres Paar aus Client- und Relay-Timeout (z. B. 120/110 s) oder Streaming über das Relay.
  - Beides ist eine eigene Entscheidung.
- **Abrechnung abgebrochener Anfragen:** Ob Anthropic bei einem vom Relay abgebrochenen Aufruf bereits erzeugte Token
  berechnet, ist nicht geprüft (Annahme: ja, bis zum Abbruch).
- **Bestehende Bereitstellungen:** Eine `.env.gateway` mit kleinerem `MAX_TOKENS_CEILING` oder eine `.env` mit
  größerem `CLAUDE_MAX_TOKENS` erzeugt weiterhin 400. Die Konsistenzregel ist jetzt in beiden Vorlagen und einem Test
  festgehalten, wird zur Laufzeit aber nicht zwischen den getrennten Prozessen abgeglichen.
- **Abgeschnittene Antworten** werden protokolliert, aber (wie im direkten Pfad) nicht in der Oberfläche markiert.
- **Relay als allgemeiner Proxy** (beliebige System- und Nutzertexte) und fehlende Inhaltsprüfung im Relay bleiben
  bestehen, siehe Zweitmeinungs-Branch §43.11.

**Tests und QA:**

| Prüfung | Ergebnis |
|---|---|
| `tests/test_relay_write_path.py` (11): Erfolg mit Standard-Settings bis `DraftingService`, Konsistenz der Grenzen in Code und Vorlagen, Token-Grenze mit Statusprotokoll, nur `thinking: disabled`, Review-Pfad unverändert, Abschneiden sichtbar, Upstream-Timeout genau 1 Versuch, Client-Timeout ohne Wiederholung, unversiegelte Payload erreicht das Relay nicht | PASS |
| Mutationsprobe: 10 Rücknahmen (Token-Standard, Denkmodus Client/Relay, Wiederholungen, Timeout, Durchreichen der Wiederholungen, `thinking`-Validator, `stop_reason`, Statuscode, `.env.example`) | 10/10 gefangen |
| Gezielte Gruppen (57 Dateien: Gateway, Relay-Client, Provider, Factory, Settings, Drafting, Review, Chat, E2E-Pilot, Kosten, Proxy-Guard) | **1406 bestanden**, 0 fehlgeschlagen |
| Volle Suite, in sechs Teilen im Vordergrund | **4772 bestanden**; fehlgeschlagen nur die 4 bekannten Logo-Tests (`test_design_refresh.py`, fremde Asset-Änderungen); 1 übersprungen, 6 xfailed, 1 xpassed |
| Legal-E2E (Datenschutz-Regression) | identisch mit `295f591`: 0 Lecks, keine fremde Akte, fremder Anhang 7/7 |
| Echter Anthropic-Aufruf über das Relay, installierter Build, Relay-Deployment | **NICHT VERIFIZIERT** (keine Cloud-Aufrufe erlaubt bzw. nicht Teil der Aufgabe) |
| Lint | **NICHT VERIFIZIERT** (kein Linter konfiguriert) |

### 44.1 Laufzeit- und Wiederholungsbudget des Relay-Schreibpfads (15.10.2026, lokaler Branch `fix/relay-retry-budget`, nicht committet)

**Nachgewiesenes Verhalten** (echtes Anthropic-SDK 1.5.0 über `gateway.relay.call_anthropic` gegen einen lokalen
Fake; kein externer Netzverkehr):

| Fall | Ergebnis |
|---|---|
| 0 Wiederholungen bei 500, 529, 429 mit `retry-after`, 400 mit `x-should-retry: true`, Timeout | je genau 1 Upstream-Versuch |
| 1 Wiederholung, 429 mit `retry-after: 3` | 2 Versuche, Abstand 3,02 s – das SDK befolgt `retry-after` (bis 60 s) statt des kurzen Backoffs |
| 2 Wiederholungen, 429 ohne `retry-after` | 3 Versuche, Abstände 0,5 s und 0,88 s (Backoff 0,5 s / 1 s mit Jitter, höchstens 8 s) |
| 1 Wiederholung, Timeout 2 s, Fake 3 s | 2 Versuche, 4,47 s gesamt |
| 0 Wiederholungen, Timeout 2 s, Antwort in 4 Stücken je 1,5 s | **6,02 s** – der Timeout gilt je Lesevorgang, nicht als Gesamtfrist |

**Lücke:** Mit `ANTHROPIC_MAX_RETRIES` 1 oder 2 waren Relay-Laufzeiten weit über dem Client-Timeout konfigurierbar:
- 1 Wiederholung: 2 × 55 s + 60 s = 170 s;
- 2 Wiederholungen: 285 s.

Weitere kostenpflichtige Versuche liefen nach dem Client-Abbruch ohne Abnehmer weiter, und das Relay startete trotzdem.

**Korrektur** (minimal):
- `GatewaySettings.client_timeout_seconds` (Standard 60, spiegelt `LEXONO_GATEWAY_TIMEOUT_SECONDS`).
- Startprüfung `retry_budget_fits_client_timeout`:
  `(Wiederholungen + 1) × Timeout + Wiederholungen × 60 s < Client-Timeout`. Sonst bricht der Relay-Start mit klarer
  Meldung ab. Das ist im echten Prozessstart belegt: Exit-Code 1, kein offener Port, keine Datenbank.
- Kein Timeout erhöht, kein Streaming, keine Architekturänderung. Die Standardwerte (55 s, 0 Wiederholungen, 60 s)
  bleiben gültig.

**Laufzeitaussagen:**
- **Garantiert:**
  - Mit `ANTHROPIC_MAX_RETRIES=0` (Standard) gibt es **genau einen** Upstream-Versuch. SDK-Schleife
    `for retries_taken in range(max_retries + 1)`; bei `remaining_retries <= 0` wird nicht wiederholt
    (`anthropic/_base_client.py` Z. 1130, 1161, 1177). Gemessen für 500, 529, 429 mit `retry-after`,
    400 mit `x-should-retry`, Timeout und Verbindungsfehler.
  - Die Startprüfung weist jede Konfiguration ab, deren Regelfall-Budget das Client-Timeout erreicht:
    `(Wiederholungen + 1) × Timeout + Wiederholungen × 60 s ≥ Client-Timeout`. Das ist eine **notwendige
    Bedingung**, **keine Höchstdauer**.
- **Nicht garantiert:**
  - Eine harte Gesamtfrist je Versuch: Der Timeout gilt je Verbindungs-, Schreib- und Lesevorgang; eine stückweise
    Antwort verlängert ihn (gemessen 6,02 s bei 2 s Timeout).
  - Dass der Client-Timeout eingehalten wird: Auch er ist pro Phase und wird zur Laufzeit nicht mit dem Relay
    abgeglichen.
  - Der sofortige Abbruch eines laufenden Relay-Versuchs beim Client-Abbruch (Stufe B, T7: Er läuft bis zum eigenen
    Timeout weiter).
- **Schätzung:** Die Messages-API ohne Streaming sendet die Antwort typischerweise am Ende. Ein einzelner Versuch dürfte
  daher etwa beim Timeout enden. Nicht mit der echten API geprüft (Stufe C).

**Kritische Nachprüfung (SDK 1.5.0, `httpx2` 2.12.0, Quelltext gelesen):**

| Thema | Beleg im installierten Code | Folge |
|---|---|---|
| Timeout-Semantik | Das Relay übergibt `timeout=55.0`; `httpx2.Timeout(55.0)` setzt **connect, read, write, pool je 55 s** (`httpx2/_config.py` Z. 97–130). Die 10-Minuten-Logik ohne Streaming greift nur bei Standard-Timeout (`resources/messages/messages.py` Z. 950); beim Relay ist sie nicht aktiv (geprüft: `client.timeout != DEFAULT_TIMEOUT`). | Ein Versuch ist **nicht** auf 55 s begrenzt: Phasen addieren sich, und der Lese-Timeout gilt je Lesevorgang. Gemessen: Antwort in 4 Stücken → 6,02 s bei 2 s Timeout. |
| Retry-Wartezeit | `retry-after-ms`, dann `retry-after` (Sekunden oder HTTP-Datum) (Z. 777–809). Befolgt wird `0 < Wert ≤ 60`, sonst Backoff `min(0,5 × 2^n, 8)` × Jitter 0,75–1 (Z. 811–833); `time.sleep` (Z. 1329). | Maximal 60 s je Wiederholung. Gemessen: 3,02 s bei `retry-after: 3`. |
| Was wiederholt wird | Statusantworten: `x-should-retry` true/false hat Vorrang, sonst 408, 409, 429, ≥ 500 (Z. 835–868). Ausnahmen: `APIConnectionError` und `APITimeoutError` (Z. 870–893); Timeout → `APITimeoutError` (Z. 1293–1295). | 400 nur mit `x-should-retry: true`, aber nur wenn Wiederholungen erlaubt sind (gemessen: 0 → 1 Versuch). |
| Abbruch durch den Client | Der Relay-Endpunkt ist synchron (`def relay_messages`) und läuft in einem Thread. Es gibt keinen Abbruchpfad zum SDK-Aufruf. | Bricht der Kanzlei-Client ab, läuft der Upstream-Versuch bis zu seinem eigenen Timeout weiter (Stufe B, T7). |
| Lexono-Client | `httpx.post(..., timeout=60.0)` mit derselben Phasen-Semantik | Auch die Wartezeit des Clients ist keine harte Gesamtfrist (NICHT VERIFIZIERT durch Messung, aus httpx-Semantik abgeleitet). |

**Kopplung an den tatsächlichen Client-Timeout (geprüft):**
- **Wirksamer Client-Timeout:** `Settings.lexono_gateway_timeout_seconds` (Umgebungsvariable bzw. `.env`
  `LEXONO_GATEWAY_TIMEOUT_SECONDS`, Standard 60, ohne Wertebereichsprüfung). Die Factory gibt ihn an den Relay-Schreib-
  und den Relay-Review-Provider weiter (`factory.py` Z. 105, 128); wirksam wird er in
  `httpx.post(timeout=…)` (`gateway_relay_client.py`).
- **Relay-Seite:** `CLIENT_TIMEOUT_SECONDS` ist eine **eigene** Einstellung des Relays (`.env.gateway`, getrennter
  Prozess und Server). Sie wird nur **von Hand gespiegelt**; es gibt keinen Abgleich, und jede Kanzlei-Installation hat
  ihre eigene `.env`.
- **Folge, per Test belegt** (`test_relay_cannot_see_a_shorter_client_timeout`): Setzt eine Kanzlei z. B. 30 s,
  verwendet ihr Client 30 s. Die Startprüfung des Relays besteht mit den Standardwerten trotzdem; der Relay-Versuch kann
  bis 55 s weiterlaufen, ohne Abnehmer. Das betrifft Kosten, nicht den Datenschutz.
- **Konsistent und per Test gesichert:** Standardwerte und Vorlagen (60 = 60).
- **Sicherer Abgleich** wäre nur über eine Protokollerweiterung möglich: Der Client übermittelt seinen Timeout pro
  Anfrage, und das Relay kürzt seinen Upstream-Timeout darauf. Das ist eine Schnittstellenänderung und hier nicht
  umgesetzt. Bis dahin: `CLIENT_TIMEOUT_SECONDS` auf den **kürzesten** Client-Timeout aller angebundenen Kanzleien
  setzen und den Client-Timeout dort nicht unter diesen Wert senken.

**Harte Gesamtfrist – geprüfte Optionen, keine umgesetzt:**
- **Thread mit `future.result(timeout)`:** gibt dem Client früher eine Antwort, beendet den Upstream-Aufruf aber
  **nicht**. Kosten liefen weiter – das wäre eine vorgetäuschte Sicherheit.
- **Wächter, der den HTTP-Client nach Fristablauf schließt:** würde die Verbindung trennen. Die Thread-Sicherheit von
  `httpx2.Client.close()` während eines laufenden `send` ist nicht dokumentiert – unsicher, ohne eigene Tests nicht
  vertretbar.
- **Asynchroner Endpunkt mit `AsyncAnthropic` und Frist per Abbruch:** trennt die Verbindung sauber, ist aber eine
  Architekturänderung (Endpunkt und Relay-Aufruf).
- **Streaming mit Zeitprüfung:** ausgeschlossen (kein Streaming).
- Ob Anthropic einen vom Relay getrennten Aufruf abbricht oder trotzdem berechnet, ist **NICHT VERIFIZIERT**.

**Bewertung:** Die Startprüfung ist korrekt und schließt die nachgewiesene Lücke (Wiederholungen mit 55 s). Sie ist
aber nur eine notwendige Bedingung. Die Formulierungen in Docstring und Fehlermeldung wurden entsprechend
präzisiert.

**Rückwärtskompatibilität:**
- Deployments ohne die neuen Variablen sind unverändert gültig.
- `ANTHROPIC_MAX_RETRIES` und `ANTHROPIC_TIMEOUT_SECONDS` gibt es erst seit `58343e1`.
- Wer Timeout oder Wiederholungen anhebt, muss `CLIENT_TIMEOUT_SECONDS` – und in den Kanzlei-Installationen
  `LEXONO_GATEWAY_TIMEOUT_SECONDS` – gemeinsam anheben. Die Meldung nennt das.

**Tests:**
- `tests/test_relay_retry_budget.py` (16): Startprüfung mit Gegenproben, Relay-Start, SDK-Charakterisierung.
- Mutationsprobe 3/3.
- Alle Relay-Testdateien: 108 bestanden.

## §45 Rückfrage-Kandidaten: Kontaktbezeichnung und Ampelfarbe sind keine Namen (16.10.2026, Branch `feature/feedback-diagnose`)

**Reproduziert (echter Gateway, `review_mode="mask"`):**
- „Rückfragen bitte an Herrn Gustav Wendelin, **Telefon** 0301 2345678.“ → „…, [PERSON_02] [TELEFON_01].“
- „Der Fahrer fuhr **bei Grün** über die Kreuzung …“ → „… bei [PERSON_01] …“ (seit §34.6 als gemessen, offen geführt)

**Ursache:** Nicht Presidio, nicht Regex, nicht Pseudonymizer – beide stammen aus der Stufe „unsichere Kandidaten“
(`app/privacy/review.py::find_review_candidates`), deren Kandidaten im Maskier-Modus vorsichtshalber ersetzt werden
(`auto_masked_terms`):
- „Telefon“: Rolle `aufzaehlung_mit_person` (Wort direkt nach „[PERSON_01],“), Namensprobe 2 → Kandidat. Das Wort ist
  die Bezeichnung der folgenden, bereits maskierten Rufnummer. „Fax“ verhielt sich gleich.
- „Grün“: Rolle `nach_praeposition` („bei“ zählt als Namenspräposition), Namensprobe 2 (Grün ist ein Nachname).

**Korrektur (zwei enge Ausnahmen, beide Bedingungen müssen gelten):**
1. Kontaktbezeichnung (`telefon`, `tel`, `fax`, `mobil`, `handy`, `mail`, `email`, `rufnummer`, `durchwahl` …) **und**
   direkt dahinter ein Kontakt-Platzhalter (`[TELEFON_..]`, `[EMAIL_..]`, optional mit Doppelpunkt). Geprüft am
   Roh-Scantext (Platzhalter sichtbar, positionsgleich zur Analysefassung).
2. „Grün“/„Rot“/„Gelb“ **direkt nach „bei“** **und** unmittelbar danach ein Fahr-/Querungsvorgang (siehe §45.1 –
   die erste Fassung „Verkehrswort irgendwo im Satz“ war zu weit und wurde vor dem Merge korrigiert).
Unverändert: Presidio, Regex-Erkennung, exakter Aktendaten-Abgleich (eine bekannte Beteiligte „Grün“ wird weiter
ersetzt – per Test), Restrisiko-Scan, Leak-Check, Siegel, Fail-closed.

**Gegenproben (per Test):** „Der Vertrag wurde bei Grün unterschrieben.“ und „Das Gespräch mit Grün …“ bleiben
Kandidaten; ein Name direkt vor einer Rufnummer („Lehmann [TELEFON_01]“) bleibt Kandidat; „Telefon“ ohne folgenden
Kontakt-Platzhalter bleibt wie bisher.

**Messung vorher/nachher (identische Fälle):**
| Benchmark | vorher | nachher |
|---|---|---|
| `scripts/bench_name_detection.py` (1100 Namensfälle, mask) | Leck 0,0 %, Block 0,0 %, sicher 100 %; Fehlalarme 2/132; Anweisungen 1/17 | identisch (nur Laufzeiten abweichend) |
| `scripts/bench_pseudonymization.py` (pseudonymization_v1) | Ergebnisdatei | `summary`, `field_results`, `e2e_results` identisch |

**Neue, NICHT behobene Befunde** (andere Stufe – vorgelagerte NER; als `known_open` in
`tests/regression_cases/dokumentierte_befunde.json`):
- „Fahrbahn **bei Rot**“ → „bei [ORT_01]“ (Presidio liest „Rot“ als Ort).
- „**Mobil** 0170 1234567“ → als Ganzes „[PERSON_02]“ (Nummer bleibt geschützt, Bezeichnung geht verloren).

### §45.1 Gegenprüfung vor dem Merge (16.10.2026): Ausnahme 2 war zu weit – präzisiert

**Befund (FAIL der ersten Fassung):** Gegenproben mit Sätzen, in denen das Farbwort ein **Name** ist und der Satz ein
Verkehrswort enthält, liefen über den echten Gateway (`review_mode="mask"`), jeweils mit und ohne Ausnahmen (alter
Stand = Ausnahmen abgeschaltet, einziger Code-Unterschied):

| Satz (synthetisch) | Stand 5735e5b | erste Fassung §45 | präzisiert §45.1 |
|---|---|---|---|
| „…an der Kreuzung meldete der Mandant … bei Grün.“ | maskiert | **Klartext** | maskiert |
| „Nach dem Unfall an der Kreuzung war er noch eine Stunde bei Grün.“ | maskiert | **Klartext** | maskiert |
| „Die Zeugin übernachtete bei Rot, direkt an der Kreuzung.“ | maskiert | **Klartext** | maskiert |
| „Die Unterlagen zum Unfall an der Ampel liegen bei Grün.“ | maskiert | **Klartext** | maskiert |
| „Sie wohnt bei Grün über die Straße, gleich neben der Ampel.“ | – | – | maskiert |
| „… ist er bei Grün angefahren und hat geklingelt.“ / „… bei Grün mitgefahren.“ | – | – | maskiert |
| „…weil er bei Gelb im Auto saß…“ | **Klartext** | Klartext | Klartext (vorbestehend, nicht durch §45) |

**Präzisierung:** Die Ausnahme greift nur noch, wenn **unmittelbar** nach der Farbe folgt: „über/in die|den Kreuzung,
Ampel, Fahrbahn, Einmündung, Haltelinie, Fußgängerfurt“ oder „gefahren/losgefahren/weitergefahren/durchgefahren/
überquert“. Bewusst ausgenommen, weil eine Personenlesart möglich ist: „über die Straße“, „angefahren“, „abgebogen“,
„eingebogen“, „mitgefahren“. Die Sätze der Tabelle stehen als Negativtests in `tests/test_privacy_review.py`.
Ampelfarben, die lesbar bleiben (per Test): „bei Grün über die Kreuzung“, „bei Gelb noch in die Kreuzung gefahren“,
„bei Rot durchgefahren“, „bei Grün losgefahren“.

**Ausnahme 1 (Kontaktbezeichnung):** Gegenproben mit Kontaktwörtern als Nachnamen („Frau Mobil, 0301 …“,
„Zeuge Handy …“, „Herr Kontakt 0301 …“) – alle maskiert, kein neues Leck.

**Messung nach der Präzisierung (identische Fälle zur Messung vor §45):** `bench_name_detection` (1100 Fälle) –
Gruppen, Lecks, Blocks, Fehlalarme (2/132), Anweisungen (1/17) identisch; `bench_pseudonymization` – `summary`,
`field_results`, `e2e_results` identisch. **Grenze dieses Belegs:** Beide Korpora enthalten keine Farbwort-Nachnamen
im Verkehrskontext und keine Kontaktwort-Nachnamen; „identisch“ belegt nur, dass auf diesen Korpora nichts schlechter
wird. Der gezielte Beleg für die Ausnahmen sind die Negativtests. Vollständige Leckfreiheit ist damit nicht belegt.

**Verbleibendes Restrisiko:** Ein der Akte unbekannter Dritter mit Nachnamen Grün/Rot/Gelb **unmittelbar** vor einem
der genannten Fahrvorgänge („… bei Grün über die Kreuzung …“ als Person) würde nicht mehr vorsichtshalber maskiert;
Presidio, Restrisiko-Scan, Leak-Check und Fail-closed greifen weiterhin, bekannte Beteiligte deckt der
Aktendaten-Abgleich ab.

## §46 Kleingeschriebener Name direkt nach einer Anrede (16.10.2026, Branch `feature/feedback-diagnose`)

**Reproduziert im aktuellen Stand (`c52bc0a`):** `pseudonymization_v1` P09 „herr **sauer** hat angerufen und bittet um
rückruf wegen der räumung.“ – „sauer“ im Klartext im Payload (Grenze „Kleinschreibung“, §20 „Namen in Kleinschreibung
(Modellgrenze)“). Das Modell erkennt kleingeschriebene Namen nicht; in getipptem Chat-Text ist Kleinschreibung üblich.

**Korrektur (nur Kandidaten-Erzeugung in `app/privacy/review.py`, Richtung mehr Schutz):** Ein kleingeschriebenes Wort
**unmittelbar** nach „herr/herrn/frau“ (im Rohtext direkt benachbart, kein Platzhalter dazwischen) wird Rückfrage-
Kandidat – im Maskier-Modus vorsichtshalber ersetzt, im Frage-Modus nachgefragt. Ausgenommen: Stoppwörter, Rollen-,
Titel- und Berufswörter („Herr der Lage“, „frau kollegin“, „herr doktor“, „frau anwältin“, „herr richter“).

**Geprüft und verworfen:** „Richter“ & Co. als Rollenwörter aufzunehmen (für P10 „Der Richter **Zweigelt** hat
angerufen“) – ohne Nutzen, weil die Namensprobe „Zweigelt“ nicht als Namen liest; die Probe nach Rollenwörtern
abzuschalten würde Fachinhalt verfälschen („…der Sachverständige **Mängel** festgestellt…“). P10 bleibt offen.

**Fehlalarm der ersten Fassung, vor dem Commit behoben:** „Frau [PERSON_01] **widerspricht**“ (P02) – in der
Analysefassung sind Platzhalter ausgeblendet, die Anrede schien direkt vor „widerspricht“ zu stehen. Jetzt Prüfung der
Nachbarschaft im Rohtext; Regressionstest vorhanden.

**Messung vorher/nachher (identische Fälle):**
| Benchmark | vorher (`c52bc0a`) | nachher |
|---|---|---|
| `bench_pseudonymization` (pseudonymization_v1, 63 Feldfälle) | Klartext-Werte **8** (A06, B03, I07, K04, P08, P09, P10, X02) | **7** (P09 behoben); Fehlalarme, verlorene Behalte-Werte (X05 „Hauptbahnhof“), Blocks (0), E2E identisch |
| `bench_name_detection` (1100) | Leck 0,0 %, Block 0,0 %, Fehlalarme 2/132, Anweisungen 1/17 | identisch |
| `bench_chat_without_file` (440, Produktivcode) | Lecks 0/440, Blocks 0, Fachfragen verändert 0/14, Fachbegriffe 20/100 | identisch |
| `bench_role_object_negatives` | 0/12, 0 Personen-Platzhalter | identisch |

Tests: 9 neue in `tests/test_privacy_review.py` (Leck-, Frage-Modus-, Negativ- und Fehlalarm-Fälle).

## §47 Nachweis auf Netzwerkebene und Schlüsselprüfung (16.10.2026)

**Unterschied, ausdrücklich:** Bisherige Repo-Tests prüften das Payload-**Objekt** vor dem SDK (und den Relay-Pfad gegen
einen lokalen Fake, §44). Neu `tests/test_privacy_wire_payload.py`: echter Gateway (Presidio) → versiegelte Payload →
echter `AnthropicClaudeWritingProvider` aus der echten Factory (inkl. Endpunkt-Politik) → echtes Anthropic-SDK →
lokaler Aufzeichnungsserver (127.0.0.1, nur mit `LEXONO_QA_LOOPBACK_CLOUD_ENDPOINT=1`; toter Proxy gegen jeden Weg
nach außen; gefälschter Schlüssel). Belegt (`write` und `write_stream`): die **tatsächlich gesendeten HTTP-Bytes**
enthalten keinen der synthetischen Werte (Name, Geburtsdatum, Anschrift, PLZ, Telefon, E-Mail, IBAN, Gegner), wohl
aber Platzhalter und die juristisch relevanten Werte (Betrag 4.250,00 EUR, Daten, „Kaution“). Unversiegelte oder nach
dem Siegel veränderte Payload und Loopback ohne QA-Freigabe: **keine einzige HTTP-Anfrage**.
**Nicht belegt:** TLS zum echten Endpunkt, tatsächliches Verhalten von Anthropic, Relay/Gateway gegen einen realen
Server.

**API-Schlüssel (Wert nie ausgegeben, Vergleich nur über exakte Übereinstimmung/Fingerabdruck):** `.env`/`.env.*` in
`.gitignore`; nur `*.example` versioniert. Der echte Schlüssel (106 Zeichen; Repo-`.env` und `%PROGRAMDATA%\Lexono\.env`
identisch) kommt in **keinem** Git-Objekt (alle, auch unerreichbare; 217 MB) und in **keiner** von 19 781 Dateien von
Arbeitsbaum, `dist\`, Datenverzeichnis (Logs; echte DB nicht geöffnet) und Job-Artefakten vor; die schlüsselartigen
Zeichenketten in sechs Testdateien sind Attrappen. `Lexono_Setup.exe` ist LZMA-komprimiert – eine Klartextsuche dort
ist nicht aussagekräftig; sein Inhalt stammt aus dem geprüften `dist\Lexono`.

## §48 Nachweisübersicht Datenschutz (Stand 16.10.2026, Branch `feature/feedback-diagnose`, ausgeführt)

Nur, was heute tatsächlich gelaufen ist; synthetische Daten; endliche Korpora – **keine** Aussage über vollständige
Leckfreiheit und **keine** Datenschutzfreigabe.

| Anforderung | Test/Benchmark (heute ausgeführt) | Ergebnis | Beleg | Offenes Risiko |
|---|---|---|---|---|
| Pseudonymisierung/Erkennung | Pseudonymisierungs-/Erkennungs-Tests (6 Dateien); `bench_pseudonymization`; `bench_name_detection` (1100); `bench_chat_without_file` (440) | 244 bestanden; 7 Klartext-Werte, alle als bekannte Grenze markiert; Namen 0,0 %; Chat 0/440. **Nachtrag §49:** 3 Klartext-Werte (B03, K04, P08, X02 behoben) | §46, §49 | P10 Rollenwort+Name ohne Anrede; Ortsname im Grundbuchtext (A06); kurze unbeschriftete Nummern (I07, §22.6); gesperrte IBAN mit OCR-Ziffernfehler (§49.5) |
| Pre-Cloud, Siegel, Fail-closed | Gateway-/Siegel-/Prüfungs-Tests (28 Dateien) | 1214 bestanden, 5 xfail (dokumentierte Grenzen) | Testsuite | – |
| Request-Manipulation, Platzhalterkollision | Platzhalter-Integritäts-Tests (3 Dateien); veränderte Payload auf Netzwerkebene | 39 bestanden; veränderte/unversiegelte Payload: 0 HTTP-Anfragen | §47 | X02 (gesperrte Schreibweise) |
| Unberechtigte Blockaden / False Positives | `bench_pseudonymization` (0 Blocks, 1 verlorener Behalte-Wert X05); `bench_name_detection` (Fehlalarme 2/132, Anweisungen 1/17); `bench_role_object_negatives` 0/12 | unverändert gegenüber vorher | §45.1, §46, §50 | „bei Rot“ ohne direkt folgenden Fahrvorgang → Ort (`known_open`); „Mobil“ behoben (§50) |
| Netzwerkebene direkter Pfad | `test_privacy_wire_payload.py` + Endpunkt-Politik | 30 bestanden (gemeinsam mit Endpunkt-Tests) | §47 | TLS/echter Endpunkt, Relay realer Server NICHT VERIFIZIERT |
| Rückfrage (HITL) | `test_privacy_review.py` | 97 bestanden | §45, §46 | Rückfragequote in echten Akten unbekannt |
| Feedback/Diagnose, Minimierung, Löschung | `test_quality_reports.py`, Regressionsfälle | 47 bestanden, 2 xfail (`known_open`) | ARCHITECTURE.md §75 | Meldung nur aus der Dokumentansicht; Frist 90 Tage = Vorschlag |
| Schlüssel nicht in Git/Logs/Artefakten | Fingerabdruck-Scan | kein Treffer | §47 | komprimiertes Setup nur indirekt |

**Später erforderlich (ausdrücklich nicht jetzt, keine Kosten):** Gateway auf realem Server (Hetzner) mit TLS,
Authentifizierung der Kanzlei-Clients, Schlüsselverwaltung nur auf dem Server, Zugriffskontrolle, Rate-Limits, Logging
ohne Inhalte; Integrationstests Client ↔ Gateway ↔ Anthropic über HTTPS (inkl. Zertifikatsprüfung, Fehler-/Timeout-
Pfade, Payload-Mitschnitt vor dem Upstream); erst danach Stufe B. Der Gateway existiert bisher nur als lokal getesteter
Code; Cloud-Aufrufe laufen aktuell über den direkten Pfad mit lokalem Schlüssel.

## §49 Offene Datenschutzgrenzen priorisiert: OCR-/Sperrsatz-Varianten (16.10.2026, Branch `feature/feedback-diagnose`, Basis `971ad7f`)

**Ziel:** vermeidbare Klartext-Lecks aus §48 verringern, ohne juristische Inhalte zu verändern und ohne einen
Schutzmechanismus abzuschwächen. Daten nur synthetisch, alles lokal, keine Cloud-Aufrufe.

### 49.1 Reproduktion und Priorisierung (qualitativ, keine Messwerte erfunden)

Alle sieben Fälle aus `benchmarks/pseudonymization_v1/cases.json` wurden über den echten Gateway (Maskier-Modus)
reproduziert. Bei allen stand der Wert vor dieser Änderung im Klartext im Payload. Alle sind echte Lecks, kein Fall
war ein Fehlalarm.

| Rang | Fall | Muster | Schaden | Wahrscheinlichkeit | Sichtbar für Anwender | Gefahr der Übermaskierung | Sicher behebbar? |
|---|---|---|---|---|---|---|---|
| 1 | B03 | gesperrte IBAN `I B A N  D E 8 9 …` | hoch (Konto direkt zuordenbar) | mittel (OCR, Formulare) | gering | gering: die Prüfziffer filtert Zufallsfolgen | **ja**, über die ISO-13616-Prüfziffer |
| 2 | K04 | E-Mail mit OCR-Leerzeichen `… @ beispiel-mail . de` | hoch (direkt identifizierend) | mittel (OCR) | gering | gering, wenn `@` und eine Domain mit Endung Pflicht sind | **ja** |
| 3 | P10 | „Der Richter Zweigelt“ (Rollenwort + seltener Nachname, ohne Anrede) | mittel (Person aus dem Gericht, nicht der Mandant) | mittel | mittel | **hoch** | **nein** (s. u.) |
| 4 | X02 | Name in gesperrter Schreibweise in einer Prompt-Injection | mittel bis hoch (gezielte Umgehung) | gering bis mittel | gering | gering: nur Personen-Treffer an veränderten Stellen | **ja** |
| 5 | P08 | OCR-Ziffern im Namen `Kar1 Brenn3r` | mittel | mittel (OCR) | mittel | mittel: Kennungen wie „B3-Formular“ müssen bleiben | **ja**, mit Negativtests |
| 6 | A06 | Ortsname im Grundbuchtext („Grundbuch von Musterstadt“) | gering (Gemarkung; Blatt und Flurstück sind maskiert) | hoch | hoch | hoch (Ortsnamen sind oft Fachinhalt) | nein, Grenze dokumentiert |
| 7 | I07 | kurze unbeschriftete Nummer („Startnummer 815“) | gering | gering | hoch | sehr hoch (Mengen, Beträge, Paragrafen) | nein, Designgrenze §22.6 |

**Warum P10 nicht behoben wird:** spaCy (`de_core_news_lg`) markiert „Zweigelt“ in diesem Satz genau wie
„Termin“/„Recht“: NOUN, Abhängigkeit `nk`, Nominativ Singular. Es gibt kein unabhängiges Signal. Eine Regel
„Rollenwort + großgeschriebenes Wort“ würde „der Richter Termin …“ und „der Verwalter Bericht …“ ebenso maskieren.
Das entspricht der dokumentierten Grenze „Richter/Fürst“ (§46, xfail). **Schutzmaßnahme:** Gerichtspersonen als
Beteiligte der Akte erfassen; dann greifen die bekannten Entitäten (voller Name und Nachname).

### 49.2 Änderungen

1. **Gesperrte IBAN** (`app/privacy/detectors.py`, `_spaced_ibans`): eine Folge einzelner Buchstaben/Ziffern mit
   1–3 Leerzeichen dazwischen wird zusammengezogen. Treffer ist nur ein Teilstück, das mit Ländercode + 2 Ziffern
   beginnt und die Prüfziffer mod 97 = 1 besteht. Dieser Bereich wird auf den Originaltext zurückgeführt. Kompakte
   Treffer bleiben unverändert.
2. **E-Mail mit Leerzeichen** (`_SPACED_EMAIL_PATTERN`): Leerraum um `.` und `@` ist erlaubt. Pflicht sind ein `@` und
   eine Domain mit mindestens einem Punkt und einer alphabetischen Endung. Zusätzlich gemeldet wird nur, was Leerraum
   enthält und nicht schon von der kompakten Erkennung abgedeckt ist. Leerraum **nach** einem Punkt zählt nur, wenn
   auch **davor** Leerraum steht (OCR-Form „ . “). **In der QA gefunden und behoben:** In der ersten Fassung wurde aus
   „info@beispielkanzlei.de. Erneut“ die Adresse samt Folgewort („Erneut“ als vermeintliche Endung). Das hätte
   Inhalt nach jedem Satzende hinter einer E-Mail-Adresse mitmaskiert. Die Benchmarks zeigten es nicht, erst
   `tests/test_document_preview.py` in der Gesamtsuite. Der Fall ist jetzt als eigener Negativtest abgedeckt.
   Verbleibende Grenze: „Wort @ Wort . Wort“ mit Leerzeichen vor dem Punkt würde in Fließtext als E-Mail gelesen; in
   normaler Schreibweise kommt das kaum vor.
3. **Zweite NER-Analysefassung für Personen** (`app/privacy/presidio_ner.py`, `_ocr_variant_for_analysis`):
   gesperrte Wörter (Einzelbuchstaben mit Leerzeichen) werden zusammengezogen; die Lücke zwischen zwei gesperrten
   Wörtern wird zu einem Leerzeichen. In Wörtern mit 1–2 OCR-typischen Ziffern (0→o, 1→l, 3→e, 4→a, 5→s, 8→b) und
   mindestens 3 Buchstaben werden die Ziffern 1:1 ersetzt. Die Positionen werden wie bei `_restore_for_analysis` auf
   das Original zurückgeführt. Übernommen werden **nur Personen-Treffer, die eine veränderte Stelle berühren**. An
   unveränderten Stellen erzeugt die Fassung also keine neuen Treffer. Der Block läuft **nach** der
   Grenzennormalisierung, weil diese „K a r l  B r e n n e r“ sonst beim Doppelleerzeichen abschnitt. Schließt ein
   Varianten-Treffer bestehende Treffer vollständig ein, ersetzt er sie. Beispiel: „Gu5tav Wende1in“ – vorher war nur
   „Wende1“ als Organisation erkannt, „Gu5tav“ und „in“ blieben lesbar. Das kann den Schutz nur erweitern, nie
   verringern.

Unverändert: Fail-Closed, Siegel, Platzhalter-Integrität, Restrisiko-Scan, Final Payload Gate, Rückfrage-Logik.
Keine Ausnahme für bestimmte Wörter.

### 49.3 Tests

Neu `tests/test_privacy_ocr_variants.py` (21 bestanden, 1 strikter xfail; darunter „Satzende nach kompakter
E-Mail verschluckt kein Folgewort“). Die Positivfälle nutzen **andere**
synthetische Werte als der Benchmark: gesperrte IBAN (GB/DE, auch in Vierergruppen), E-Mail mit OCR-Leerzeichen,
gesperrter Name in einer Prompt-Injection, Namen mit OCR-Ziffern. Negativfälle bleiben wörtlich unverändert:
gesperrte Überschriften („U R T E I L  I M  N A M E N …“), Aufzählungen ohne gültige Prüfziffer, „z . B . § 280 Abs . 1
BGB“, „Treffen @ 10 Uhr“, „Raum1“, „B3-Formular, Typ A4, Kd1-Kennung, COVID19-Verordnung, Abschnitt 1a“, Beträge,
Daten und Paragrafen.

### 49.4 Vorher/Nachher (heute ausgeführt, gleiche Korpora)

| Benchmark | vorher (`971ad7f`) | nachher | Bemerkung |
|---|---|---|---|
| `pseudonymization_v1` Klartext-Lecks | 7 (A06, B03, I07, K04, P08, P10, X02) | 3 (A06, I07, P10) | geschützt 81 → 85 von 88 |
| ‒ Fehlalarme (FP) | 7 | 7, identische Menge | keine neuen, keine weggefallenen |
| ‒ Behalte-Werte / Blocks | 67/68 (X05 „Hauptbahnhof“) / 0 | 67/68 / 0 | unverändert |
| ‒ Teil-Klartext | 1 | 2 | neu K04: **Artefakt der Messung** (s. u.), kein Leck |
| ‒ E2E-Fälle (7) | wie erwartet | identisch | |
| `bench_name_detection` (1100) | 0,0 %; Fehlalarme 2/132; Anweisungen 1/17 | identisch | |
| `bench_chat_without_file` | 0/440; Fachfragen 0/14 | identisch | |
| `bench_role_object_negatives` | 0/12 | identisch | |
| Legal-E2E dev/test | pii_leaks 0/0 | 0/0, keine abweichende Kennzahl | |

**K04 Teil-Klartext:** Der Benchmark zerlegt Werte an `[\s,.-]` in Teile ab 4 Zeichen. Der Domainteil „mail“ trifft
das Wort „E-Mail“ im Etikett. Der Payload lautet vollständig `E-Mail: [EMAIL_01], …`.

Datenschutz-Testdateien (Privacy, Platzhalter, Regression, Benchmark-Tests; vor der E-Mail-Korrektur): 1804
bestanden, 9 xfail, 1 xpass. **Gesamtsuite nach der Korrektur** (6 Teile nacheinander): 4948 bestanden, 9 xfail,
1 xpass, 1 übersprungen, 4 fehlgeschlagen. Die vier Fehlschläge sind die `test_design_refresh`-Logo-Tests; sie
hängen an fremden, nicht angefassten Logo-Dateien im Arbeitsbaum. Alle Benchmarks oben wurden nach der Korrektur
erneut ausgeführt und sind identisch.
Der xpass `…common_noun[Richter]` tritt auch auf `971ad7f` ohne diese Änderung auf (in einem eigenen Arbeitsbaum
geprüft); er ist vorbestehend und wurde nicht verändert.

### 49.5 Offene Grenzen (Status: teilweise nachgewiesen)

- **P10** Rollenwort + seltener Nachname ohne Anrede (s. o.). Schutz: Beteiligte erfassen.
- **A06** Gemarkung/Ortsname im Grundbuchtext; **I07** kurze unbeschriftete Nummern (§22.6).
- **Gesperrte IBAN mit OCR-Ziffernfehler:** besteht die Prüfziffer nicht und bleibt erkennbar (strikter xfail). Eine
  Lockerung ohne Prüfziffer würde Aufzählungen und Messreihen treffen. Deshalb bleibt es bewusst so.
- **Namen mit OCR-Buchstaben-Verwechslungen** (rn↔m, cl↔d) und mit mehr als 2 Ziffern sind nicht abgedeckt. Die
  Varianten erkennen nur Personen und nur das, was das NER-Modell nach dem Ersetzen erkennt.
- **„bei Rot“ → Ort, „Mobil“+Nummer → Person** (`known_open`, §75) wurden nicht angefasst; die Ursache in der
  vorgelagerten NER-Stufe ist nicht abschließend geklärt. *(Nachtrag: in §50 geklärt und teilweise behoben.)*
- Endliche, handverfasste Korpora: **keine** Aussage über vollständige Leckfreiheit.

## §50 Gerichtspersonen als Aktenbeteiligte (P10), „Mobil“ und „bei Rot“ (16.10.2026, Branch `feature/feedback-diagnose`, Basis `febd5e2`)

Daten nur synthetisch, alles lokal, keine Cloud-Aufrufe. Status bleibt **teilweise nachgewiesen**.

### 50.1 Reproduktion und Ursachen (belegt, roh im Modell und Stufe für Stufe)

| Fall | Reproduziert (Maskier-Modus) | Ursache |
|---|---|---|
| P10 „Der Richter Zweigelt …“ | Klartext; auch im Rückfrage-Modus keine Rückfrage | spaCy/Presidio: keine Entität; keine Folgestufe greift (vgl. §49.1) |
| seed-008 „… überquerte die Fahrbahn bei Rot.“ | `bei [ORT_01]` | spaCy `de_core_news_lg` setzt „Rot“ **selbst** als `LOC` (es gibt Orte dieses Namens); Presidio übernimmt es. Die Ampelregel aus §45.1 galt nur für Rückfrage-Kandidaten, nicht für NER-Treffer. Auch die eindeutige Form „bei Rot in die Kreuzung“ wurde maskiert. |
| seed-009 „Herrn Gustav Wendelin, Mobil 0170 1234567.“ | `[PERSON_02] 1234567` – **Teil-Leck**, nicht nur Übermaskierung | (1) spaCy setzt „Mobil“ als `PER`; im zusammengesetzten Gateway-Text (Feldmarker danach) sogar „Mobil 0170“. (2) `_resolve_overlaps` verwarf bei Überlappung **verschiedener** Kategorien den später beginnenden Treffer vollständig (Telefon „0170 1234567“), dessen Rest blieb im Klartext. Die Beschreibung des Regressionsfalls („Nummer geschützt“) war falsch und ist korrigiert; die Gegenprobe seed-010 prüfte nur die ganze Nummer und übersah den Rest. |

**Erfassung von Gerichtspersonen (Ist-Zustand):** Beteiligte werden je Akte als `Party` mit freier Rolle erfasst
(Reiter „Beteiligte“). `_build_known_entities` ordnet sie per Stichwort zu und speist voller Name + Nachname in die
Pseudonymisierung ein. **Mit erfasster Partei ist P10 geschützt** (jede getestete Rolle). Gefundene Schwächen:
(a) „Richter am Amtsgericht“, „Gerichtsvollzieher“, „Gerichtssachverständiger“ fielen über „gericht“ in `gericht`,
„Staatsanwalt“ über „anwalt“ in `anwalt` – Platzhalter `[GERICHT_01]` für eine Person, und die Aktenisolation (§27,
`foreign_matter_names`) zählte bei diesen Rollen nur den **vollen** Namen: „Richter Zweigelt“ in einem Wissenseintrag
einer fremden Akte wäre nicht zurückgehalten worden (die NER erkennt den Namen nicht). (b) „Zweigelt, Hans“ ergab als
„Nachnamen“ den Vornamen „Hans“. (c) „Richter Dr. Zweigelt“ erhält neben `[BETEILIGTER_01]` einen zweiten Platzhalter
(Titel-Treffer länger als der bekannte Name) – kein Leck. **Ohne Akte** (Chat ohne Akte) gibt es keine Beteiligten
und damit kein Signal.

### 50.2 Änderungen

1. **Gerichtspersonen** (`app/ai_providers/local_ai_provider.py`): `_COURT_PERSON_ROLE` (ganze Wörter: Richter:in,
   Rechtspfleger:in, Sachverständige:r, Gutachter:in, Gerichtsvollzieher:in, (Ober-)Staatsanwält:in, Urkundsbeamt:in,
   Protokollführer:in, Schöff:in, Dolmetscher:in) geht der Institutionszuordnung vor, **sofern der Name nicht nach
   Institution aussieht** (dieselbe Prüfung wie bisher in `_name_variants`). Ergebnis: Kategorie `beteiligter`
   (Person) mit Nachname, auch in der Aktenisolation. „Amtsgericht …“, „Staatsanwaltschaft …“, „Finanzamt …“ und
   Kanzleien bleiben unverändert. Keine Regel für Wörter nach Rollenbezeichnungen im Text.
2. **„Nachname, Vorname“** in `_build_known_entities` wie in `_name_variants`: Nachname vor dem Komma.
3. **Kontaktbezeichnung** (`app/privacy/detectors.py`, `_drop_contact_labels`): ein NER-Treffer (Person/Ort/
   Organisation), dessen erstes Wort eine Kontaktbezeichnung ist (dieselbe Liste wie die Rückfrage §75.2, jetzt
   gemeinsam genutzt), entfällt nur, wenn **unmittelbar** dahinter eine erkannte Rufnummer/E-Mail bzw. – im
   Restrisiko-Scan – ein `[TELEFON_…]`/`[EMAIL_…]`/`[KENNUNG_…]`-Platzhalter beginnt und der Treffer nicht über sie
   hinausreicht. Die Nummer bleibt eigener Treffer.
4. **Überlappung ohne Zeichenverlust** (`_resolve_overlaps`): ragt ein Treffer einer **anderen** Kategorie über den
   vorherigen hinaus, wird er nicht mehr verworfen. Schneidet ein NER-Treffer eine strukturierte Angabe (Telefon,
   E-Mail, IBAN, BIC, URL) an, endet der NER-Treffer davor und die Angabe bleibt vollständig; sonst wird der
   überstehende Rest als eigener Treffer seiner Kategorie maskiert. Das bestehende Testbeispiel für „andere Kategorie:
   bisheriges Verhalten“ wurde **bewusst** auf das neue Verhalten umgestellt (begründet im Test).
5. **Ampelfarbe** (`app/privacy/presidio_ner.py`, `is_traffic_light_colour_at`): die gegengeprüfte §45.1-Bedingung
   („bei“ + Farbe + **unmittelbar** Fahr-/Querungsvorgang) gilt jetzt auch für NER-Treffer; `review.py` nutzt dieselbe
   Regel statt einer Kopie.
6. **Hinweis im Beteiligten-Formular** (`matter_detail.html`): Gerichtspersonen mit Rolle erfassen; Beispielrollen.

**Verworfen (in der QA gefunden, nicht übernommen):** akademische Titel („Dr.“, „Prof.“) wie eine Anrede abtrennen,
damit „Dr. Zweigelt“ den bekannten Platzhalter erhält. Der stehen gebliebene Titel ließ den Restrisiko-Scan anschlagen –
**neue Blockaden** („Herr Prof. Dr. Zweigelt …“, „Prof. Dr. Erika Wiebe …“) und geändertes Verhalten ohne bekannte
Namen. Vollständig zurückgenommen; (c) bleibt offen. Ebenfalls in der QA gefunden und behoben: die erste Fassung von 3.
blockierte, weil der Restrisiko-Scan „Mobil“ vor dem Platzhalter erneut als Person las (Platzhalter als Anker
ergänzt), und K04 („Mobil 0171-234 56 78“, als Kennnummer länger erfasst) blockierte, bis `KENNUNG` als Anker galt.

### 50.3 Tests

Neu `tests/test_privacy_court_persons_and_labels.py` (Rollen je Schreibweise, Institutionen unverändert,
Komma-Nachname, bekannte Gerichtsperson in vier Schreibweisen inkl. Genitiv und Versalien, Fachwörter mit erfasster
Gerichtsperson wörtlich unverändert, Aktenisolation fremd/eigen, unbekannte Gerichtsperson ohne Akte als **strikter
xfail**, Kontaktbezeichnungen samt vollständiger Nummernmaskierung, Namen neben Nummern weiter maskiert,
Überlappungs-Einheitstests, Ampel positiv/negativ inkl. „übernachtete bei Rot“, „Herr Rot“, „Rot am See“, „Herr Grün“).
Regressionsfälle: seed-009 behoben (`known_open` entfernt, `must_mask` „1234567“), seed-010 um den Nummernrest
ergänzt, neu seed-011 (eindeutiger Fahrsatz), seed-008 bleibt `known_open` mit präziser Ursache. Gegenprobe: auf
`febd5e2` schlagen 19 der neuen Prüfungen fehl, alle Negativproben bestehen dort wie hier.

### 50.4 Vorher/Nachher (gleiche Korpora, heute ausgeführt; vorher = Stand nach §49)

| Prüfung | vorher | nachher |
|---|---|---|
| `pseudonymization_v1` Klartext-Lecks | 3 (A06, I07, P10) | 3 (unverändert; P10 ohne Beteiligte im Korpus) |
| ‒ Fehlalarme | 7 | **6** (K04 „Mobil“ als Person entfällt) |
| ‒ Blocks / Behalte-Werte / Konsistenz | 0 / 67/68 / 2/2 | 0 / 67/68 / 2/2 |
| ‒ E2E-Fälle | – | identisch |
| `bench_name_detection` (1100), `bench_chat_without_file` (440), Rollenobjekte (12) | – | identisch |
| Legal-E2E dev | pii_leaks 0 | 0, keine abweichende Kennzahl |
| Legal-E2E test | pii_leaks 0 | 0; **zwei zusätzlich maskierte Reste** (`-0533` einer Gutachten-Nummer, `7` einer Hausnummer), Schriftsatz-Payload Median 2476 → 2487 Zeichen; Fristen/Fakten unverändert |

Die beiden Legal-E2E-Reste sind die beabsichtigte Wirkung von Änderung 4: isoliert nachgestellt sendete `febd5e2`
„Laut [ORT_01]-0533 …“, jetzt „[ORT_01][KENNUNG_01]“. (Die Ortsmaske für „Kfz-Schadensgutachten Nr. G-26“ ist ein
vorbestehender Fehlalarm.) Gesamtsuite (6 Teile nacheinander): **4990 bestanden**, 9 xfail, 1 xpass (vorbestehend,
§49.4), 1 übersprungen, 4 fehlgeschlagen – die fremden `test_design_refresh`-Logo-Tests.

### 50.5 Offene Grenzen und erforderliches UX-Verhalten

- **P10 ohne erfasste Partei** – auch im **Chat ohne Akte** – nicht sicher lösbar: kein unabhängiges Signal, eine
  Rollenwort-Regel würde Fachwörter maskieren. Erforderliches Verhalten: Gerichtspersonen im Reiter „Beteiligte“ mit
  Rolle erfassen (Hinweis ergänzt); für den Chat ohne Akte empfohlen (nicht umgesetzt): bei Nennung eines
  Rollenworts mit Großwort eine **freiwillige** Möglichkeit „als Namen schützen“ statt automatischer Maskierung, oder
  den Chat einer Akte zuordnen. Owner-Entscheidung.
- **„Dr. Zweigelt“ neben bekanntem „Zweigelt“:** zweiter Platzhalter (kein Leck); Lösung über Titel-Abtrennung
  verworfen (Blockaden), anderer Ansatz offen.
- **seed-008 „überquerte die Fahrbahn bei Rot.“** bleibt maskiert (Ortslesart möglich); ebenso „Die Ampel zeigte
  Rot“, „Er überfuhr die Ampel bei Rot“, „Bei Rot darf nicht gefahren werden“ (Inhaltsverlust im Verkehrsrecht).
  Eine breitere Regel bräuchte ein Verkehrskontext-Signal, das §45.1 bereits als unzuverlässig verworfen hat.
- „Richter“ am Satzanfang wird teils als Person maskiert (bekannte Grenze „Richter/Fürst“, §46).
- Diagnosepfad der Qualitätsmeldungen (`matter_known_entities`) ordnet weiterhin alle Beteiligten als `beteiligter`
  ein und kennt die Komma-Regel nicht (nur lokale Diagnose, kein Cloud-Pfad).
- Rollen bleiben Freitext; Namen mit Institutions-Teilwort (z. B. „…amt…“ in einem Nachnamen) fallen bei
  Gerichtsrollen weiter in die alte Zuordnung (vorbestehende Prüfung, geschützt bleibt der Name).
- Endliche, handverfasste Korpora: **keine** Aussage über vollständige Leckfreiheit.
- *Nachtrag §51:* P10 im Chat ohne Akte ist jetzt durch manuelle Markierung abgedeckt („Namen schützen“).

## §51 Manuelle Namensmarkierung als Erweiterung der bestehenden Rückfrage (16.10.2026, Branch `feature/feedback-diagnose`, Basis `3994019`)

Daten nur synthetisch, alles lokal, keine Cloud-Aufrufe. Status bleibt **teilweise nachgewiesen**.

### 51.1 Vorhandene Funktionalität (Ist-Analyse)

- Die Rückfrage (`chat.html` `showPrivacyReview`; `POST /dashboard/chat/{id}/privacy-review`) zeigt nur
  **von Lexono gefundene** Kandidaten mit drei Optionen. „Als Person behandeln“ und „Unsicher“ werden aktenweit
  gespeichert (`PrivacyDecision`, `conversation_id = NULL`), „Kein personenbezogenes Datum“ nur für die Unterhaltung.
  Der Gateway maskiert entschiedene Begriffe über `detect_decision_terms`: exakt, ganze Wörter, alle Vorkommen.
- Eine **manuelle Markierung beliebiger Textstellen gab es nicht** (keine Textauswahl, kein Freitextfeld). Bei P10
  („Der Richter Zweigelt“) findet Lexono keinen Kandidaten, also erscheint keine Rückfrage, und der Name ging im
  Klartext hinaus (Gegenprobe-Test belegt das).
- Ein Chat „ohne Akte“ hat immer eine automatisch angelegte Schnellakte; Entscheidungen gelten dort.
- **Lücke beim Fund:** nur die Chat-Pfade luden die gespeicherten Entscheidungen. Entwurfsprüfung
  (`app/review/engine.py`, schickt den **re-identifizierten** Entwurf), Schriftsatz- und Formularpfade
  (`DraftingService.create_draft` ohne `privacy_decisions`) und `AIOrchestrator` wendeten „Als Person behandeln“
  **nicht** an. Ein so geschützter Name ging dort im Klartext hinaus, wenn die Erkennung ihn nicht fand.

### 51.2 Änderungen (keine zweite Rückfrage, keine Änderung an Presidio)

1. **UI** (`chat.html`, `app.css`): In der bestehenden Fußzeile des Eingabefelds steht „Namen schützen“. Der Nutzer
   markiert den Namen im Text und klickt; es erscheint ein Chip mit ×. Eine Mehrwort-Auswahl wird in einzelne
   Wörter zerlegt, nur Buchstaben sind zulässig, höchstens 20 Namen. Ändert sich der Text und ein markierter Name
   kommt nicht mehr vor, wird der Chip als veraltet angezeigt und **nicht gesendet**. Bei einem Fehler vor der
   Verarbeitung kommen Text **und** Markierung zurück. Die Rückfrage mit ihren drei Optionen bleibt unverändert.
2. **Server** (`chat_router._parse_manual_terms`/`_record_manual_terms`, `decisions.validate_manual_terms`):
   Das Feld `protected_terms` wird in `send-stream` **und** im klassischen `/send` **vor jeder Persistenz** geprüft:
   gleiche Form wie eine Rückfrage-Entscheidung, ein Wort, der Begriff kommt in der Nachricht vor. Ein Fehler
   bedeutet: keine Nachricht, keine Entscheidung, kein Cloud-Aufruf. Danach wird der Begriff als **dieselbe**
   Entscheidung „Als Person behandeln“ gespeichert (Audit-Text „manuell markiert“, ohne Begriff; HMAC-Statistik wie
   bisher). Damit greifen ohne neuen Mechanismus: erneute Gesamtprüfung, Wiederherstellung nach Neuladen,
   Doppelversand-Sperre, Geltung für spätere Nachrichten und den Verlauf.
3. **Schreibweisen** (`review.detect_decision_terms`): bei Personen-Begriffen zusätzlich Versalien
   („ZWEIGELT“) und Genitiv („Zweigelts“; ersetzt wird nur das Wort). Kleinschreibung bewusst nicht. Im Gateway
   weicht ein NER-Treffer, der **exakt** den Bereich eines entschiedenen Namens (ggf. mit Genitiv) hat, dem
   Entscheidungs-Treffer. So erhält eine Person **einen** Platzhalter. Vorher las die NER „Zeugen Brummelbeck“ als
   Ort, „widersprach Brummelbeck“ als Person, und „Zweigelts“ bekam einen zweiten Platzhalter.
4. **Alle Pfade derselben Akte**: `_prepare_and_gate` (ohne mitgegebene Entscheidungen), Entwurfsprüfung und
   Orchestrator laden die aktenweiten Entscheidungen. „Kein personenbezogenes Datum“ bleibt unterhaltungsbezogen
   und wirkt dort nicht.
5. **Fehlermeldungen ohne Begriff**: im `/send`-Pfad landet die Meldung in der Redirect-URL (`?error=…`) und damit
   im Zugriffsprotokoll bzw. Browserverlauf. Der Browser nennt den Namen vorher lokal.

**Geprüfte Abflusswege:** Logs (keine Logger-Aufrufe mit Begriff; Audit/Block-Log ohne Begriff), Diagnoseberichte
(`quality_reports`: nur Zähler), temporäre Dateien (keine), alternative Cloud-Pfade (alle Provider verlangen das
Siegel, das nur `prepare_request` erzeugt; dessen drei Aufrufer laden jetzt die Entscheidungen), Redirect-URL
(behoben). Der Begriff steht wie jede Rückfrage-Entscheidung im Klartext in `privacy_decisions` (Aktendatum, lokal).

### 51.3 Tests und QA

`tests/test_web_manual_name_protection.py` (über die echten Endpunkte und den echten Gateway):

- Gegenprobe: ohne Markierung steht „Zweigelt“ in der Payload.
- P10 im Chat ohne Akte mit Markierung: maskiert; Datum, Betrag und § 3 ZPO unverändert; Entscheidung aktenweit;
  Audit ohne Begriff.
- Die Markierung gilt für Folgenachrichten.
- Markierung eines ohnehin erkannten Namens ist unschädlich.
- Mehrere Namen, Wiederholungen, Genitiv, Versalien: je Person ein Platzhalter.
- Veraltete oder ungültige Markierung in 7 Varianten: nichts gesendet, nichts gespeichert.
- Klassischer `/send`-Pfad: Markierung wirkt; eine veraltete Markierung bricht ab, die URL enthält keinen Namen.
- Zusammenspiel mit der bestehenden Rückfrage, Abbruch, Neuladen und Wiederaufnahme.
- Fehler bei der Anwendung blockiert den Versand (fail-closed).
- Entwurfsprüfung und `create_draft` wenden aktenweite Entscheidungen an.
- **HTTP-Nachweis:** echter Web-Endpunkt → echter Drafting-Service → echter Anthropic-Provider → lokaler
  Aufzeichnungsserver (§47). „Zweigelt“ ist nicht in den gesendeten Bytes, Datum/Betrag/§ schon.
- UI-Render.
- **Strikter xfail (vorbestehend):** „Herr Lange wartete lange“ – ein als Person entschiedener Name wird auch
  kleingeschrieben weitergetragen. Das gilt auf `3994019` ebenso für Rückfrage-Entscheidungen.

**Browser-QA** (Edge headless, eigener Server auf temporärer SQLite-DB, kein Schlüssel, `fetch` durch eine
Attrappe ersetzt – keine Anfrage verließ den Browser): Hinweis ohne Auswahl, Chip und Feldwert, veraltete Markierung
blockiert ohne Anfrage, Formular trägt `protected_terms`, Wiederherstellung nach Serverfehler, Mehrwort-Auswahl,
Entfernen, Ziffern-Auswahl abgewiesen; Screenshots geprüft.

**Vorher/Nachher (gleiche Korpora, Vergleich mit dem Stand nach §50):** `pseudonymization_v1` Feld- und
E2E-Fälle identisch; `bench_name_detection` (1100), `bench_chat_without_file` (0/440), Rollenobjekte (0/12)
identisch; Legal-E2E dev/test: keine abweichende Kennzahl, pii_leaks 0. Ein erster Legal-E2E-Lauf unter
Speicherdruck (Hintergrundlauf vom System beendet, Dokumentfehler 0 → 3) war ein Artefakt; der Wiederholungslauf im
Vordergrund ist identisch. Neue/betroffene Tests: 131 bestanden (+1 strikter xfail), Chat-/Drafting-/Review-Tests 385
bestanden. **Gesamtsuite** (6 Teile nacheinander): **5011 bestanden**, 10 xfail, 1 xpass (vorbestehend, §49.4),
1 übersprungen, 4 fehlgeschlagen – die fremden `test_design_refresh`-Logo-Tests.

### 51.4 Restrisiken

- Die Markierung ist eine **zusätzliche** Schutzmaßnahme, kein Nachweis, dass alles Übrige erkannt wurde.
- Nur ganze Wörter aus Buchstaben. Bindestrich-Namen („Müller-Lüdenscheidt“) lassen sich nur als Einzelteile
  markieren. Mehrwort-Namen erhalten je Wort einen Platzhalter.
- Andere Flexionsformen als Genitiv-s sowie Kleinschreibung werden nicht erfasst. Umgekehrt wird ein kleingeschriebenes
  Homograph-Wort mitmaskiert (vorbestehend, xfail).
- Eine Markierung gilt aktenweit und dauerhaft (wie „Als Person behandeln“). Aufheben geht nur über eine
  Rückfrage-Entscheidung, und „personal“ gewinnt immer (fail-closed). Eine eigene Verwaltung der Markierungen fehlt.
- Markierungen fließen in die HMAC-Lernstatistik ein wie Rückfrage-Entscheidungen (kein Klartext).
- Nur im Chat-Eingabefeld; Formular-Workflows (Schriftsatz, E-Mail) haben keine eigene Markier-UI, wenden aber
  vorhandene Entscheidungen der Akte jetzt an.
- *Nachtrag §52:* Mehrwort- und Bindestrichnamen sowie Verwaltung/Aufheben umgesetzt; Kleinschreibung untersucht.

## §52 Namensschutz: Mehrwort-/Bindestrichnamen, Verwaltung der Entscheidungen, Kleinschreibung (16.10.2026, Branch `feature/feedback-diagnose`, Basis `f59e3e0`)

Daten nur synthetisch, alles lokal, keine Cloud-Aufrufe. Status bleibt **teilweise nachgewiesen**.

### 52.1 Mehrwort- und Bindestrichnamen

**Ist-Zustand (reproduziert):**

- „Hans-Peter Müller“ und „Anna von Berg“ ließen sich nicht als Entscheidung speichern. Die Oberfläche zerlegte die
  Auswahl in Einzelwörter; „von“ allein wäre gültig gewesen und hätte jedes „von“ maskiert.
- Rohe Mehrwort-Entscheidungen hätten die Konsistenz **verschlechtert**. Der Titel-Detektor (`detect_titled_person`)
  liefert „Dr. Hans-Peter Müller“, die Entscheidung „Hans-Peter Müller“. Zwei Vollnamen-Formen machten die
  Nachnamen-Zuordnung mehrdeutig, und „Herr Müller“ erhielt einen eigenen Platzhalter.

**Änderungen:**

1. `review.is_name_shaped_term`: Teile aus Buchstaben mit inneren Bindestrichen, durch Leerzeichen getrennt, 1–5
   Teile. Kleingeschrieben sind nur Namenszusätze erlaubt (`NAME_PARTICLES`: von, van, de, der, zu …). Der letzte
   Teil beginnt groß, ein Zusatz allein ist kein Name. Ziffern, Punkte und Apostrophe sind ausgeschlossen, damit
   kann ein solcher Begriff keine Rufnummer, E-Mail, IBAN oder Fundstelle anschneiden.
   - Zulässig **nur für die manuelle Markierung**; Rückfrage-Antworten bleiben Einzelwörter (Audit 12.10.2026,
     bestehender Test unverändert).
   - Beginnt der Begriff mit Anrede, Titel oder Rolle (`MANUAL_NAME_PREFIX_WORDS`), lehnt der Server ihn ab,
     damit z. B. „Richter“ nicht im Platzhalter verschwindet. Der Browser entfernt diese Wörter schon beim Markieren;
     beide nutzen dieselbe Liste, an die Seite übergeben über Jinja-Globals von `chat_router`.
2. `detect_decision_terms`: Ein mehrteiliger Name trifft auch über Leerraum oder Zeilenumbruch (Adressblock, PDF),
   dazu wie bisher Versalien und Genitiv.
3. Gateway-Filter: Ein NER-Treffer, der nur Anrede/Titel/Rolle plus den markierten Namen enthält („Richter
   Brummelbeck“), weicht der Entscheidung. Die Rolle bleibt lesbar.
4. Pseudonymizer, nur die Platzhalter-Zuordnung, nicht die Erkennung:
   - Führende Anrede/Titel/Rolle gehören nicht zur Identität. „Dr. Hans-Peter Müller“ und „Hans-Peter Müller“ sind
     ein Vollname. Dadurch ist auch „Herr Müller“ wieder eindeutig zugeordnet.
   - Eine Kurzform mit Namenszusatz („von Berg“) wird wie ein Nachname dem einzigen passenden Vollnamen zugeordnet.
     Zwei verschiedene Personen („Anna von Berg“, „Bernd von Berg“) werden nie zusammengelegt.
5. Restrisiko-Scan: In der Analysefassung wird die Genitiv-Endung direkt am Platzhalter mit neutralisiert. Vorher las
   die NER in „[PERSON_02]s Zeugin“ die Folge „s Zeugin“ als Person und **blockierte** (in der QA dieser Runde
   gefunden). Der gesendete Text ändert sich dadurch nicht.

Eine Markierung zeigt nie auf eine falsche Stelle: Sie wirkt auf jede exakte Fundstelle desselben Namens. Fehlt der
Name nach einer Textänderung (z. B. Bindestrich entfernt), sendet weder der Browser noch der Server etwas.

### 52.2 Verwaltung gespeicherter Entscheidungen

**Ist-Zustand:** Entscheidungen waren nirgends sichtbar und nur über eine neue Rückfrage-Entscheidung „überstimmbar“,
wobei „personal“ immer gewinnt. Ein fälschlich geschützter Begriff blieb für die Akte dauerhaft maskiert.

**Änderung:**

- **Ort:** Reiter „Beteiligte“ der Akte, Abschnitt „Geschützte Namen & Datenschutz-Entscheidungen“; im Chat führt
  „verwalten“ zur Akte bzw. Schnellakte (`matter_detail.html`, `app/web/privacy_decisions_router.py`).
- **Anzeige:** Begriff, Entscheidung, Geltung (ganze Akte / eine Unterhaltung), Datum.
- **Aktionen:**
  - „Schutz aufheben“ bzw. für „Kein personenbezogenes Datum“: „Rückfrage wieder aktivieren“ (Zeile löschen).
  - „Als Person bestätigen“ (Unsicher → Als Person behandeln).
- **Berechtigung:** neue `PERM_PRIVACY_DECISION_MANAGE`, nur Admin und Anwalt, weil das Aufheben den Schutz abschwächt.
  Mitarbeiter sehen die Liste ohne Schaltflächen.
- **Aktenisolation:** Die Entscheidung muss zur Akte der URL gehören. Fehlermeldungen und Audit enthalten keinen
  Begriff.
- **Wirkung und Fehlerverhalten:**
  - Entscheidungen werden bei jeder Anfrage frisch geladen (`load_decisions`, kein Zwischenspeicher). Ein
    aufgehobener Schutz wirkt ab der nächsten Prüfung in Chat, Entwurfsprüfung, Schriftsatz und Orchestrator nicht
    mehr; die automatische Erkennung bleibt aktiv.
  - Scheitert das Speichern, wird zurückgerollt und der Schutz bleibt bestehen (Test simuliert den Fehler nach dem
    Löschen, vor dem Commit).

### 52.3 Kleinschreibung („Herr Lange wartete lange“)

**Ursache (belegt):** nicht die Entscheidung, sondern `detectors._extend_with_repeated_occurrences`. Ein einmal
erkannter Wert wird ohne Rücksicht auf Groß-/Kleinschreibung auf jedes weitere Vorkommen übertragen, auch ohne jede
Entscheidung, sobald die NER „Herr Lange“ erkennt. Bei bekannten Namen kommt die ebenfalls schreibungsunabhängige
Suche (`detect_known_entities`) hinzu.

**Keine Änderung, weil nicht sicher unterscheidbar (gemessen):** spaCy taggt kleingeschriebene **Namen** genauso wie
gewöhnliche Wörter. Beispiele: „ich habe mit lange telefoniert“ → ADV, „dass weiß die Kaution zurückfordert“ → VERB,
„groß will vergleichen“ → ADV; die gewöhnlichen Wörter tragen dieselben Tags. Eine Regel, die kleingeschriebene
Wiederholungen freigibt, würde schlampig getippte Namen im Chat offenlegen. Die Grenze bleibt als strikter xfail
festgehalten; zwei Gegenproben belegen, dass solche Namen maskiert bleiben.

### 52.4 Tests und Vorher/Nachher

`tests/test_privacy_manual_names_v2.py` (33 Tests):

- Namensform positiv und negativ (Zusatz allein, Titel, Rolle, Doppel-Bindestrich, Ziffern, zu viele Teile).
- Die Rückfrage bleibt bei Einzelwörtern.
- Ein Platzhalter je Person über Titel, Nachname, Genitiv und „Frau von Berg“; zwei Personen mit gleichem
  Familiennamen getrennt; markierter Nachname neben markiertem Vollnamen.
- Zeilenumbruch und Versalien; Rolle vor markiertem Namen bleibt lesbar; Beträge, Daten, § 535 BGB und Aktenzeichen
  bleiben korrekt.
- Veränderte Schreibweise sendet nichts.
- **HTTP-Nachweis** über den echten Anthropic-Provider und den lokalen Aufzeichnungsserver.
- Wortlisten aus einer Quelle (Seite = Server).
- Verwaltung: Liste, Aufheben mit Wirkung auf die nächste Chat-Anfrage und auf `create_draft`; Mitarbeiter ohne
  Recht; fremde Akte; Bestätigen; Rückfrage reaktivieren; Fehler beim Aufheben lässt den Schutz bestehen.
- Kleinschreibung als Gegenprobe.

Ein bestehender §51-Test („Hans Zweigelt“ ungültig) wurde auf das neue, gewollte Verhalten umgestellt (Rolle bzw.
Zusatz allein ungültig).

**Browser-QA** (Edge headless, eigener Server auf temporärer SQLite-DB, kein Schlüssel, `fetch` im Chat durch eine
Attrappe ersetzt): Bei der Auswahl „Dr. Hans-Peter Müller“ und „Richter Brummelbeck“ entfallen Titel bzw. Rolle;
„Anna von Berg“ wird über den Zeilenumbruch erkannt; ein Zusatz allein wird abgewiesen; ein veränderter Bindestrich
blockiert ohne Anfrage. Auf der Aktenseite funktionieren Bestätigen und Aufheben gegen die temporäre DB, mit
Rückkehr zu `#beteiligte`. Screenshots geprüft.

**Vorher/Nachher** (gleiche Korpora, Vergleich mit §51), getrennt nach Art:

- **Lecks:** `pseudonymization_v1` 3 (unverändert, kein Feldfall geändert); Namen 0,0 % (1100); Chat ohne Akte
  0/440; Legal-E2E dev/test pii_leaks 0.
- **Fehlalarme:** 6 (unverändert); Namen-Benchmark 2/132; Rollenobjekte 0/12.
- **Blockaden:** 0 (unverändert); eine in dieser Runde gefundene Blockade (Genitiv am Platzhalter) wurde vor dem
  Commit behoben.
- **Juristische Inhalte:** Behalte-Werte 67/68 (unverändert); Legal-E2E ohne abweichende Kennzahl.
- **Gesamtsuite** (6 Teile nacheinander, Vordergrund): **5045 bestanden**, 10 xfail, 1 xpass (vorbestehend),
  1 übersprungen, 4 fehlgeschlagen – die fremden `test_design_refresh`-Logo-Tests.

### 52.5 Offene Grenzen

- Kleinschreibung: ein als Name erkannter oder entschiedener Begriff maskiert auch das kleingeschriebene Homograph
  (vorbestehend, nicht sicher lösbar, s. 52.3).
- Apostroph-Namen („O'Brien“) und Namen mit mehr als 5 Teilen sind nicht markierbar.
- Erkennt der Titel-Detektor „Dr. Hans-Peter Müller“, verschwindet der Titel weiterhin im Platzhalter
  (vorbestehend). Die Rückführung stellt ihn wieder her.
- Bei „Frau von [PERSON_02]“ (NER erkennt nur „Berg“) bleibt „von“ lesbar, und die Rückführung ergibt „von Anna von
  Berg“ (vorbestehend).
- Die Verwaltung gibt es nur auf der Aktenseite; Massenaktionen und Suche fehlen.
- Die Markierung ist eine zusätzliche Schutzmaßnahme, kein Nachweis vollständiger Erkennung.
- *Nachtrag §53:* „von Anna von Berg“ behoben; Apostroph-Namen und bis zu 8 Namensteile markierbar.

## §53 Rückführung zusammengesetzter Namen, Apostrophe, längere Namen (16.10.2026, Branch `feature/feedback-diagnose`, Basis `0b7a616`)

Daten nur synthetisch, alles lokal, keine Cloud-Aufrufe. Status bleibt **teilweise nachgewiesen**.

### 53.1 Reproduzierte Rückführungsfehler

Prüfmaß ist der **Rundweg**: Schreibt die Cloud-KI den pseudonymisierten Text unverändert zurück, muss die lokale
Rückführung exakt das Original ergeben. Auf `0b7a616` fehlten 6 von 9 Fällen, auch ohne jede Markierung:

| Original | Rückführung vorher |
|---|---|
| „Frau von Berg widersprach“ (neben „Anna von Berg“) | „Frau von **Anna von** Berg widersprach“ (Duplikat) |
| „Herr Müller verlangte“ (neben „Dr. Hans-Peter Müller“) | „Herr **Dr. Hans-Peter** Müller verlangte“ (Titel eingeschleust) |
| „Frau Wiebe bestätigte“ (neben „Prof. Dr. Erika Wiebe“) | „Frau **Prof. Dr. Erika** Wiebe bestätigte“ |
| „und Frau Brenner kamen“ (neben „Mandant Karl Brenner“) | „und Frau **Karl** Brenner kamen“ |

**Ursache:** Ein Platzhalter steht für genau einen Wert, den Vollnamen (`_auto_alias`/`_canonicalize_alias` ordnen
Kurzformen bewusst demselben Platzhalter zu, damit eine Person einen Platzhalter hat). `Pseudonymizer.reconstruct`
setzte deshalb überall den Vollnamen ein.

### 53.2 Korrektur (nur lokale Rückführung, der gesendete Inhalt bleibt unverändert)

- Der Pseudonymizer merkt sich je Personen-Platzhalter die im Quelltext vorkommenden Formen **nach einer Anrede,
  einem Titel oder einer Rolle** (`PseudonymMapping.context_forms`): „herr“ → „Müller“, „frau“ → „von Berg“.
  Erkennung (`detectors.name_context`): letztes Wort aus `MANUAL_NAME_PREFIX_WORDS` vor höchstens Namenszusätzen,
  gleiche Zeile.
- `reconstruct` (`_form_for_context`) wählt je Vorkommen die Form, die im Quelltext nach demselben Kontextwort stand,
  sofern eindeutig („Herr“/„Frau“ untereinander austauschbar). Sonst gilt der Vollname. In beiden Fällen wird nichts
  doppelt geschrieben, was direkt davor schon steht: ein Titel („Dr. [P]“ ergibt kein „Dr. Dr.“) oder ein
  Namenszusatz nach einer Anrede („Frau von [P]“ → „Berg“).
- **Bewusst ohne Kontext der Vollname:** Im Rubrum eines Schriftsatzes („In Sachen [MANDANT_01] gegen …“) muss die
  Partei vollständig genannt werden. Eine Kurzform aus dem Quelltext wäre dort ein inhaltlicher Mangel. Deshalb
  ergibt „Brenners Anspruch“ ohne Anrede weiterhin „Karl Brenners Anspruch“ (bestehender Test unverändert).
- Es wird nie die Form eines **anderen** Platzhalters eingesetzt, und nichts wird erfunden (nur Formen dieser Person
  aus diesem Text). Präposition und Namenszusatz bleiben unterscheidbar: „Der Brief von [P]“ ergibt „Der Brief von
  Anna von Berg“, weil vor „von“ keine Anrede steht. Andere Kategorien (Ort, Aktenzeichen …) sind unberührt.
- `context_forms` gehört **nicht** zur Leck-Prüfung der Antwort (`variants`), weil eine Kurzform wie „Berg“ dort
  normale Antworten blockieren würde.

### 53.3 Apostrophe und längere Namen

- **Vorher:** „O'Brien“ und Namen mit mehr als 5 Teilen waren nicht markierbar.
- **Jetzt (`review._NAME_PART`, `MAX_NAME_PARTS`):**
  - ein Apostroph **innerhalb** eines Namensteils („O'Brien“, „D’Angelo“, „Dell'Anna Rossi“), nie am Anfang oder
    Ende, nie doppelt;
  - bis zu 8 Teile („Maria del Carmen de la Fuente y Santos“), weiterhin höchstens 80 Zeichen (DB-Spalte);
  - gerades und typografisches Apostroph gelten bei Suche, Gültigkeitsprüfung und Platzhalter-Zuordnung als
    dieselbe Schreibung; die Rückführung behält die jeweils originale Form.
- Die Rückfrage bleibt bei Einzelwörtern; an Presidio und der automatischen Erkennung ändert sich nichts.

Die Markierung wirkt weiterhin auf jede exakte Fundstelle desselben Namens und muss in der Nachricht vorkommen.
Fehlen nach einer Textänderung beide Apostroph-Formen, wird nicht gesendet. Manipulierte Werte (z. B. „O'Brien');
DROP …“, Zeilenumbruch, mehr als 8 Teile, mehr als 80 Zeichen) lehnt der Server ab.

### 53.4 Tests und Vorher/Nachher

`tests/test_privacy_name_reconstruction.py` (33 Tests):

- **Rundweg:** 9 Fälle exakt, darunter zwei Personen „von Berg“, „Prof. Dr. Erika Wiebe“, „O'Brien“/„O’Brien“, der
  8-teilige Name.
- **Gegenproben:**
  - ohne Anrede bleibt der Vollname (Rubrum);
  - frei formulierte Antworten ergeben weder „Dr. Dr.“ noch „von Anna von Berg“;
  - „Der Brief von [P]“ behält den Vollnamen;
  - Formen wechseln nie zu einem anderen Platzhalter;
  - andere Kategorien bleiben unverändert.
- **Apostrophe und lange Namen:** gültige Formen werden angenommen, ungültige oder manipulierte (8 Fälle) und mehr
  als 80 Zeichen abgewiesen.
- **HTTP-Nachweis:** Weder „O'Brien“ noch „O’Brien“ stehen in den gesendeten Bytes, Datum, Betrag und § 280 BGB schon.

Ein §52-Test (6 Teile ungültig) wurde auf die neue Grenze umgestellt (9 Teile ungültig).

**Browser-QA** (Edge headless, eigener Server, temporäre DB, kein Schlüssel, `fetch` durch eine Attrappe ersetzt):

- Apostroph-, lange und Zusatznamen werden markiert; vor „Dell'Anna Rossi“ wird „Frau“ abgetrennt.
- Eine ungültige Auswahl wird abgewiesen.
- Steht nur noch die typografische Form im Text, bleibt die Markierung gültig.
- Fehlen beide Formen, wird ohne Anfrage blockiert.

**Vorher/Nachher** (Vergleich mit §52), getrennt nach Art:

- **Lecks:** unverändert: `pseudonymization_v1` 3 (kein Feldfall geändert), Namen 0,0 % (1100), Chat ohne Akte
  0/440, Legal-E2E dev/test 0.
- **Fehlalarme:** 6, Namen 2/132, Rollenobjekte 0/12 (unverändert).
- **Blockaden:** 0 (unverändert).
- **Juristische Inhalte:** Behalte-Werte 67/68, Legal-E2E ohne abweichende Kennzahl.
- **Rückführung:** 3/9 → 8/9 exakte Rundwege; der neunte („Brenners“ ohne Anrede) ergibt bewusst den Vollnamen.
- **Gesamtsuite** (6 Teile, Vordergrund): **5078 bestanden**, 10 xfail, 1 xpass (vorbestehend), 1 übersprungen,
  4 fehlgeschlagen – die fremden Logo-Tests.

### 53.5 Offene Grenzen

- Ohne Anrede/Titel/Rolle setzt die Rückführung den Vollnamen ein, auch wo der Quelltext nur den Nachnamen hatte
  (bewusst, s. o.).
- Schreibt die Antwort eine Anrede, die im Quelltext nicht vorkam (z. B. „Frau [P]“ für eine Person, die nur mit
  „Herr“ vorkam), greift die Form von „Herr“/„Frau“. Bei fremdem Geschlecht ist das inhaltlich der Text der Antwort.
- Genitiv und Großschreibung im Antworttext werden wie bisher behandelt; andere Flexionsformen nicht.
- Erkennt der Titel-Detektor „Dr. Hans-Peter Müller“, steht der Titel im Platzhalter. Die Rückführung stellt ihn
  wieder her, ohne ihn zu verdoppeln; die Cloud-KI sieht ihn nicht.
- Kleinschreibung (§52.3) unverändert.

## §54 „Namen schützen“ einheitlich in Chat, Schriftsatz-, Entwurfs- und E-Mail-Formularen (16.10.2026, Branch `feature/feedback-diagnose`, Basis `0ea1918`)

Daten nur synthetisch, alles lokal, keine Cloud-Aufrufe. Status bleibt **teilweise nachgewiesen**.

### 54.1 Sendewege (ermittelt)

| Formular | Endpunkt | Text in der Anfrage | Weg zur Cloud |
|---|---|---|---|
| Schriftsatz-Generator (`schriftsatz_generator.html`) | `POST /dashboard/tools/schriftsatz/generate` | `stil`, `vorlage`, `attorney_anmerkungen` | `create_draft` → `_prepare_and_gate` |
| Änderungsauftrag (`draft_detail.html`) | `POST /dashboard/drafts/{id}/instructions[/apply]` | Auftrag (gemessen: der bisherige Entwurf geht **nicht** mit, nur Aktentitel und Auftrag) | `apply_instruction` → `create_draft` |
| Editor, KI-Bearbeitung (`draft_editor.html`, AJAX) | `POST /dashboard/drafts/{id}/ai-edit` | Anweisung und Auswahl | `apply_ai_suggestion` → `create_draft` |
| E-Mail: Zusammenfassen/Antworten (`partials/message_detail.html`) | `POST /dashboard/chat/from-message/{id}` | Betreff, Absender, E-Mail-Text | Chat (`send_message`), danach weiter im Chat |

- Ein eigenes E-Mail-Verfassen-Formular gibt es nicht (keine Versandfunktion). Der Dokumentengenerator und der
  Postausgang rufen keine KI auf.
- Alle Wege laden bereits die aktenweiten Entscheidungen (`_prepare_and_gate` bzw. Entwurfsprüfung, §51). Es fehlten
  nur das Bedienelement und die serverseitige Aufnahme.

### 54.2 Änderungen (eine Logik statt Kopien)

- **Server:** `decisions.parse_manual_terms` / `record_manual_terms` sind die einzige Stelle für Prüfung und
  Speicherung. Der Chat ruft sie jetzt ebenfalls auf (vorher private Funktionen im `chat_router`).
  - Geprüft wird gegen genau den Text, der in die Anfrage geht. Bei Entwürfen kommt der angezeigte Entwurf hinzu: ein
    dort stehender Name ist markierbar und gilt dann aktenweit für spätere Anfragen
    (`name_protection.protect_manual_terms`).
  - Ungültige, veraltete oder manipulierte Markierung: nichts wird angelegt (auch keine Schnellakte), gespeichert
    oder gesendet. Fehlermeldungen enthalten keinen Begriff.
  - Gespeichert wird als dieselbe Entscheidung „Als Person behandeln“ (aktenweit, Audit „manuell markiert“), committet
    **vor** der Anfrage. Scheitert das Speichern, wird zurückgerollt und nichts gesendet.
- **Browser:** `app_name_protection.js` ist ein gemeinsamer Baustein mit derselben Logik wie im Chat (Namensform,
  Abtrennen von Anrede/Titel/Rolle, Apostrophe, bis zu 8 Teile, Gültigkeit am aktuellen Text). Der Chat nutzt ihn
  jetzt ebenfalls; IDs und Verhalten sind unverändert, die Browser-QA aus §52/§53 ist identisch. Markup und
  Konfiguration kommen aus `partials/name_protection.html`. Die Wortlisten stellt `app/web/name_protection.py` in
  jedem betroffenen Router bereit, eine Quelle in `detectors.py`.
  - Formulare: veraltete Markierung blockiert das Absenden (Capture-Phase, vor der Ladeanzeige).
  - Editor: blockiert vor dem `fetch`.
  - Markierbar ist eine Auswahl in den Formularfeldern, im angezeigten Entwurf bzw. im E-Mail-Text
    (`selectionWithin`).
- **Unverändert:** Rückfrage, Berechtigungen zum Aufheben/Bestätigen (`PERM_PRIVACY_DECISION_MANAGE`, §52),
  Pseudonymisierung, Rückführung (§53), Fail-Closed, Siegel.
- **Korrigierte Annahme:** Ich war zunächst davon ausgegangen, der Änderungsauftrag sende den Entwurf mit. Gemessen
  sendet er ihn nicht; Code-Kommentare und Hinweistexte sind entsprechend formuliert.

### 54.3 Tests und QA

`tests/test_web_form_name_protection.py` (24 Tests, echte Endpunkte und Gateway, Schreib-Attrappe gibt die
Platzhalter der Anfrage zurück):

- **Schriftsatz:**
  - Gegenprobe ohne Markierung: „Zweigelt“ steht in der Payload.
  - Mit Markierung geschützt, Datum, Betrag und § 3 ZPO unverändert, Entscheidung aktenweit.
  - Bindestrich-, Apostroph- und Zusatznamen in allen drei Feldern; zwei Personen „von Berg“ getrennt.
  - 5 ungültige oder veraltete Varianten: keine Akte, kein Entwurf, keine Entscheidung, keine Anfrage.
  - Speicherfehler: keine Anfrage.
- **Änderungsauftrag:**
  - geschützt bzw. Gegenprobe;
  - Name nur im Entwurf markierbar und danach aktenweit wirksam;
  - veraltet: nichts gespeichert oder gesendet;
  - „Anmerkung speichern“ speichert die Markierung ohne Cloud-Aufruf.
- **Editor:** Namen aus Auswahl und Entwurf geschützt; ungültig → 422 ohne Begriff und ohne Anfrage;
  Speicherfehler → keine Anfrage.
- **E-Mail:** beide Aktionen geschützt; manipulierte Markierung → 400, keine Unterhaltung; Bedienelement an beiden
  Formularen, Ladeanzeige unverändert.
- **Aktenweit:** Eine Markierung aus der E-Mail gilt im Schriftsatz und im Chat derselben Akte.
- **HTTP-Nachweis** (Schriftsatz über echten Anthropic-Provider und lokalen Aufzeichnungsserver): weder „Zweigelt“
  noch „O'Brien/O’Brien“ in den gesendeten Bytes; juristische Werte enthalten.
- **Rückführung im gespeicherten Entwurf:** exakt das Original, ohne „von Anna von Berg“, „Herr Dr.“ oder „Dr. Dr.“.

**Browser-QA** (Edge headless, eigener Server, temporäre DB, kein Schlüssel; Absenden und `fetch` abgefangen –
keine Anfrage verließ den Browser), alle vier Wege ohne JS-Fehler:

- „Richter Zweigelt“ → „Zweigelt“, „Frau Dell’Anna Rossi“ → „Dell’Anna Rossi“; Auswahl im Entwurf und im E-Mail-Text.
- Veraltete Markierung blockiert ohne Anfrage, im Formular wie im Editor; das Feld `protected_terms` wird übertragen.
- In der QA gefunden und behoben: Ein früherer Hinweis blieb nach gültigem Absenden sichtbar.

**Vorher/Nachher**, getrennt nach Art:

- **Lecks:** Benchmarks unverändert (`pseudonymization_v1` kein Feldfall geändert, Chat ohne Akte 0/440,
  Legal-E2E 0). In den neuen Formularwegen geht ein markierter Name nicht mehr im Klartext hinaus (vorher ging
  „Zweigelt“ in Schriftsatz und Änderungsauftrag hinaus, s. Gegenproben).
- **Fehlalarme und Blockaden:** unverändert.
- **Juristische Inhalte:** unverändert.
- **Gesamtsuite** (6 Teile, Vordergrund): **5102 bestanden**, 10 xfail, 1 xpass (vorbestehend), 1 übersprungen,
  4 fehlgeschlagen – die fremden Logo-Tests.

### 54.4 Offene Grenzen

- Namen in **hochgeladenen Dokumenten** (Schriftsatz-Upload, Chat-Anhang) lassen sich nicht markieren, weil es keine
  Textansicht zum Auswählen gibt. Sie sind nur über eine Markierung desselben Namens im eingegebenen Text oder über
  die Rückfrage geschützt.
- Aktionen auf Dokumenten und Mandanten (`start_conversation_from_document`) haben noch keine Markierung (feste
  Aufforderungen; danach geht es im Chat mit Markierung weiter).
- Im Editor gilt der **gespeicherte** Entwurf. Ein Name, der nur in noch nicht automatisch gespeichertem Text steht,
  wird serverseitig abgewiesen, und es wird nichts gesendet.
- Ein Fehler in der E-Mail-Aktion zeigt eine schlichte Fehlerseite (400). Kein Datenabfluss, aber keine Rückkehr mit
  erhaltener Auswahl.
- Kleinschreibung (§52.3) und die übrigen §53-Grenzen unverändert. Die Markierung ist ein zusätzlicher Schutz, kein
  Nachweis vollständiger Erkennung.
- *Nachtrag §55:* Namen im noch nicht gespeicherten Editortext sind jetzt markierbar.

## §55 „Namen schützen“ im noch nicht gespeicherten Editortext (16.10.2026, Branch `feature/feedback-diagnose`, Basis `799679b`)

Daten nur synthetisch, alles lokal, keine Cloud-Aufrufe. Status bleibt **teilweise nachgewiesen**.

**Ursache:** Browser und Server prüften gegen **verschiedene** Texte. Der Browser prüfte gegen den aktuellen
Editorinhalt, der Server (`name_protection.protect_manual_terms`) gegen Anweisung, Auswahl und den **gespeicherten**
Entwurf. Ein Name, der nur im ungespeicherten Text stand, wurde deshalb serverseitig als „kommt nicht vor“ abgewiesen,
und die ganze KI-Bearbeitung brach ab. Das ist sicher, aber nicht nutzbar. Ein Leck gab es nicht: gemessen gehen in
die Cloud-Anfrage nur Anweisung und Auswahl, nicht der ganze Entwurf (§54.1).

**Änderung:**

- Der Editor (`app_draft_editor.js`) schickt mit der KI-Bearbeitung den **aktuellen** Editorinhalt als `editor_text`
  mit, aber nur, wenn Markierungen vorhanden sind. Der Browser hat die Markierung aus genau diesem Text abgeleitet,
  damit ist sie an dieselbe Textversion gebunden.
- Der Server prüft die Markierung mit derselben Funktion wie bisher (`parse_manual_terms`) gegen Anweisung, Auswahl,
  aktuellen Editortext und gespeicherten Entwurf, und speichert sie als dieselbe aktenweite Entscheidung, **vor** der
  Anfrage.
- `editor_text` ist **nur Prüfgrundlage**: Er wird nicht gespeichert, nicht in die Cloud-Anfrage übernommen und nicht
  protokolliert. Der Entwurf wird nicht automatisch gespeichert; auch das Markieren selbst löst keine Anfrage aus
  (Browser-QA).
- Größer als 200 000 Zeichen: sicherer Abbruch.
- Ohne `editor_text` bleibt das bisherige Verhalten (Gegenprobe: weiterhin Abweisung).
- Unverändert: Pseudonymisierung, Rückführung, Rückfrage, Fail-Closed, Siegel, Berechtigungen.

**Tests** (`tests/test_web_editor_unsaved_name_protection.py`, 13 Tests):

- Gegenprobe ohne Editortext.
- Name nur im ungespeicherten Text, in der Auswahl: geschützt; gespeicherter Entwurf unverändert; Editortext nicht in
  der Anfrage.
- Name nur im ungespeicherten Text außerhalb der Auswahl: aktenweit gespeichert und in der späteren Entwurfsprüfung
  maskiert.
- 5 veraltete, mehrdeutige oder ungültige Fälle (ersetzt, gelöscht, Wortteil, mit Rolle, manipuliert): Abbruch mit
  Meldung ohne Begriff, keine Anfrage, keine Entscheidung.
- Zu langer Editortext; Speicherfehler: keine Anfrage, Entwurf unverändert.
- Mehrwort-, Bindestrich-, Apostroph- und Zusatznamen, zwei Personen „von Berg“ getrennt.
- Exakte Rückführung der ungespeicherten Auswahl, ohne „von Anna“, „Dr. Dr.“ oder „Herr Dr.“.
- **HTTP-Nachweis** über den lokalen Aufzeichnungsserver: „Brummelbeck“ und der übrige Editortext sind nicht in den
  gesendeten Bytes, das Datum ist enthalten.

**Browser-QA** (Edge headless, eigener Server, temporäre DB, kein Schlüssel; `fetch` abgefangen):

- Ein in den Editor getippter, nicht gespeicherter Name wird markiert, ohne dass eine Anfrage entsteht.
- Beim Senden gehen Markierung und Editortext mit.
- Wird der Name danach ersetzt, ist die Markierung veraltet und das Senden blockiert, mit verständlichem Hinweis.
- Keine JS-Fehler.

**Vorher/Nachher**, getrennt nach Art:

- **Lecks:** keine neuen. `pseudonymization_v1` kein Feldfall geändert, Legal-E2E dev/test 0, abweichend: keine.
- **Fehlalarme:** unverändert.
- **Blockaden:** Die bisher unnötige Abweisung (Name nur im ungespeicherten Text) entfällt; bei veralteter Markierung
  wird weiterhin bewusst blockiert.
- **Inhalte:** unverändert (Rückführung exakt).
- **Gesamtsuite** (6 Teile, Vordergrund): **5115 bestanden**, 10 xfail, 1 xpass (vorbestehend), 1 übersprungen,
  4 fehlgeschlagen – die fremden Logo-Tests.

**Offene Grenzen:**

- `editor_text` stammt vom Browser. Ein manipulierter Wert kann nur **zusätzliche** Namen als „Als Person behandeln“
  speichern (mehr Schutz, ggf. Übermaskierung eines selbst gewählten Worts), nie Schutz entfernen. Die Namensform-
  und Rollenprüfung gilt unverändert.
- „Richter“ am Satzanfang wird teils als Person maskiert (bekannte Grenze §46/§50).
- Die übrigen Grenzen aus §52–§54 bleiben bestehen (Kleinschreibung, hochgeladene Dokumente, Dokument- und
  Mandanten-Aktionen). *Nachtrag §56:* hochgeladene Dokumente und Dokument-Aktionen sind jetzt angebunden.

## §56 „Namen schützen“ in hochgeladenen Dokumenten (16.10.2026, Branch `feature/feedback-diagnose`, Basis `27cc723`)

Daten nur synthetisch, alles lokal, keine Cloud-Aufrufe. Status bleibt **teilweise nachgewiesen**.

### 56.1 Bestandsaufnahme der Dokumentpfade (vor den Änderungen, am Code)

- **Text:** Einziges Textfeld ist `Document.extracted_text`. Erstmals gespeichert wird es in
  `DocumentProcessingService.process_document` (`app/documents/service.py`, direkt bzw. nach OCR).
  - **Synchron** beim Upload: Chat-Anhang, Schriftsatz-Upload, Upload in der Akte, Entwurfsexport.
  - **Im Hintergrund:** Mail-Anhang, zunächst ohne Akte.
  - Intake-Ordner: Dokumente zunächst ohne Akte.
  - Es gibt **keine Bearbeitung** des extrahierten Texts; er ändert sich nur durch eine neue Verarbeitung (OCR-Wiederholung).
- **Anzeige mit auswählbarem Text:** Dokumentansicht der Akte (`matter_document.html`, Bereich „Extrahierter Text“)
  und Dokumentansicht im Chat (`chat.html`). Beide rendern `build_document_preview`: escapter Text mit `<mark>`,
  `innerText` entspricht dem extrahierten Text. PDF/DOCX werden zusätzlich als Seitenbilder gezeigt (nicht auswählbar).
- **Cloud-Wege:** Alle laufen über `DraftingService._prepare_and_gate`. Aktenweite Entscheidungen „Als Person
  behandeln“ gelten dort auf **allen** Feldern, auch auf Dokumenttext und Fristen-Ausschnitten:
  - Chat mit Anhang (`context_document_ids`) bzw. die ganze Akte;
  - KI-Aktionen auf Dokumenten (`from-document`);
  - Schriftsatz, Änderungsauftrag, E-Mail-Aktionen (ganze Akte);
  - die Entwurfsprüfung sendet nur den Entwurf, ebenfalls mit Entscheidungen.
- **Lokal:** Klassifikation, Fristen-Erkennung, Suche/Embeddings (fastembed), Vorschau-Erkennung, Qualitätsdiagnose,
  lokale KI (sieht nur die pseudonymisierte Payload).
- **Protokolle:** Kein Logger, Audit-Eintrag oder Diagnosebericht schreibt Dokumenttext (API-Log: Hash und Kategorien).
- **Lücke vorher:** Namen im Dokumenttext waren nirgends markierbar; die Chat-Markierung prüft nur gegen den
  getippten Text.

### 56.2 Änderungen (dieselbe Prüf- und Speicherlogik, keine parallele Schutzarchitektur)

- **Server** (`app/web/name_protection.py`):
  - `protect_document_terms`: Prüfung mit `parse_manual_terms` gegen genau `extracted_text`, Speicherung mit
    `record_manual_terms` als aktenweite Entscheidung „Als Person behandeln“, vor jeder Anfrage.
  - **Bindung an den Textstand:** `document_text_version` (SHA-256-Präfix des extrahierten Texts) wird mit dem
    Formular gesendet. Hat sich der Text seit dem Öffnen geändert, wird abgewiesen, statt die Markierung auf einen
    anderen Text anzuwenden.
  - Ohne Akte, ohne Text, ungültige/manipulierte Markierung oder Speicherfehler: nichts gespeichert, nichts
    gesendet; Meldungen ohne Begriff.
- **Endpunkt** `POST /dashboard/matters/{matter}/document/{doc}/protect-names` (`document_actions_router.py`):
  - Aktenisolation: Dokument der Akte, Akte nicht gelöscht.
  - Rückkehr nur in die eigene Dokumentansicht bzw. einen eigenen Chat **derselben** Akte (kein offener Redirect).
  - Jede Rolle mit Aktenzugang, weil es den Schutz nur erweitert; Aufheben bleibt `PERM_PRIVACY_DECISION_MANAGE`.
  - Eine leere Markierung wird nicht als „gespeichert“ gemeldet.
- **KI-Aktionen auf Dokumenten** (`from-document`): Sie tragen die noch ungespeicherte Markierung und den
  Textstand mit und speichern sie vor dem ersten Cloud-Aufruf. Fehler: HTTP 400, nichts gestartet.
- **UI** (`partials/name_protection.html`: `document_controls`, `document_action_fields`, `document_script`;
  gemeinsamer Baustein `app_name_protection.js`), eingebunden in Akten- und Chat-Dokumentansicht:
  - Auswahl im Text, auch über automatische Hervorhebungen hinweg; Anrede, Titel und Rolle werden abgetrennt.
  - Bereits geschützte Namen werden im Text hervorgehoben (`build_document_preview(protected_terms=…)`, gleiche
    Fundstellenlogik). Eine Markierung verschwindet so nicht unbemerkt.
- **Aus dem unabhängigen Review behoben (mittel):** In der Chat-Dokumentansicht hätte eine gesetzte, aber nicht
  gespeicherte Markierung beim Senden aus dem Eingabefeld still gefehlt; der Anhang ging mit. Jetzt verweigert das
  Eingabefeld das Senden, bis gespeichert ist.
- **Ebenfalls aus dem Review (niedrig):** Die Meldung sagt im Dokumentkontext „im Dokumenttext“ statt „in der
  Nachricht“.

### 56.3 Tests und QA

`tests/test_web_document_name_protection.py` (22 Tests, echte Endpunkte und Gateway, Antwort-Attrappe gibt den
pseudonymisierten Sachverhalt zurück):

- **Gegenprobe:** Ohne Markierung steht „Zweigelt“ in der Payload.
- **Speichern und Wirkung:** Nach dem Speichern aktenweit geschützt, sichtbar hervorgehoben, in jeder späteren
  KI-Aktion maskiert; Datum, Betrag, Paragraf und Aktenzeichen erhalten.
- **Markierung über die KI-Aktion:** vor der Anfrage gespeichert.
- **Namensformen:** Mehrwort-, Bindestrich-, Apostroph- und Zusatznamen; zwei Personen „von Berg“ getrennt;
  „O'Brien“/„O’Brien“ mit einem Platzhalter.
- **Rückführung:** Die Antwort enthält die Dokumentsätze exakt im Original, ohne „von Anna von Berg“ oder „Dr. Dr.“.
- **Textstand geändert:** abgewiesen, keine Unterhaltung, keine Anfrage.
- **Ungültige oder manipulierte Markierungen:** 7 Fälle abgewiesen.
- **Speicherfehler:** keine Anfrage.
- **Isolation:** fremde Akte → 404; Rückkehrziel nur in eigene Ansichten.
- **Rollen:** Mitarbeiter darf schützen, aber nicht aufheben.
- **Chat-Upload:** echte PDF-Extraktion → Chat-Dokumentansicht → Markierung → Folgeanfrage maskiert; die Grenze ist
  ausdrücklich geprüft, siehe 56.4.
- **Sende-Sperre im Chat** verdrahtet; Meldungstext im Dokumentkontext geprüft.
- **HTTP-Nachweis** über den echten Anthropic-Provider und den lokalen Aufzeichnungsserver: keine markierten Namen
  in den gesendeten Bytes, juristische Werte enthalten. Mitgeschnittene Protokollausgaben enthalten keinen Namen.

**Geprüft ist die HTTP-Anfrage des SDK an einen lokalen Loopback-Server (unverschlüsselt). Nicht geprüft sind die
TLS-Übertragung zum echten Endpunkt und der Relay-/Gateway-Pfad gegen einen realen Server (§47).**

**Browser-QA** (Edge headless, eigener Server, temporäre DB, kein Schlüssel, Anthropic- und Gateway-Endpunkt auf
einen toten Port gelegt; KI-Aktionen abgefangen):

- Speichern ohne Markierung wird mit Hinweis abgewiesen.
- „Richter Zweigelt“ → „Zweigelt“; „Zeugin Anna von Berg“ wird über die Hervorhebung hinweg zu „Anna von Berg“.
- Die KI-Aktion trägt Markierung und Textstand; nach echtem Speichern (temporäre DB) ist der Name hervorgehoben und
  der Hinweis sichtbar.
- Chat-Dokumentansicht: Senden aus dem Eingabefeld mit ungespeicherter Markierung wird blockiert, ohne Anfrage.
- Keine JS-Fehler.
- **In der QA aufgefallen (Testaufbau, kein Produktfehler):** Die erste Abfangung im QA-Skript griff nicht. Das
  Formularfeld `name="action"` überdeckt `form.action`, und die Ladeanzeige stoppt die Ereignisweitergabe. Zwei
  KI-Aktionen erreichten deshalb den temporären Server. **Kein Cloud-Aufruf:** Der Server hatte keinen Schlüssel
  (Antwort „Cloud-KI ist nicht konfiguriert“, `api_call_logs` leer). Danach wurden die Endpunkte zusätzlich auf einen
  toten Port gelegt und die Abfangung korrigiert.

**Vorher/Nachher**, getrennt nach Art:

- **Lecks:** in den Benchmarks keine Änderung (`pseudonymization_v1` kein Feldfall geändert, Legal-E2E dev/test 0,
  ohne Abweichung). In den Dokumentwegen geht ein markierter Name nicht mehr im Klartext hinaus (Gegenprobe vorher).
- **Fehlalarme:** unverändert.
- **Blockaden:** neu nur bewusst bei veraltetem Textstand und ungespeicherter Dokument-Markierung im Chat.
- **Inhalte:** unverändert (Rückführung exakt).
- **Gesamtsuite** (6 Teile, Vordergrund): **5137 bestanden**, 10 xfail, 1 xpass (vorbestehend), 1 übersprungen,
  4 fehlgeschlagen – die fremden Logo-Tests.

### 56.4 Grenzen und offene Risiken (nicht durch Scheinlösung verdeckt)

- **Chat- und Schriftsatz-Upload senden im selben Request** (**geschlossen in §57**: Upload und KI-Verarbeitung sind
  jetzt getrennte Vorgänge): Der Text wird beim Hochladen extrahiert und gleich
  mitgesendet; vor dem ersten Versand gibt es keinen Moment zum Markieren. Ein Name, den die Erkennung übersieht,
  geht in dieser ersten Anfrage im Klartext hinaus; der Test hält das ausdrücklich fest.
  - **Sicherer Ablauf:** in der Akte hochladen („Dokumente“), in der Dokumentansicht markieren, dann die KI-Aktion
    bzw. den Chat starten.
  - Eine Upload-ohne-Senden-Option im Chat wäre eine eigene Produktentscheidung.
- **Mail- und Intake-Dokumente ohne Akte:** Sie sind erst nach der Zuordnung markierbar. Vorher erreichen sie keine
  Cloud, weil die KI-Aktion eine Akte verlangt.
- **Aus dem Review (niedrig):**
  - Ein Name mit geschütztem Leerzeichen oder Leerzeile im Dokument wird abgewiesen (fail-closed, kein Leck).
  - Fehler der KI-Aktion erscheinen als schlichte 400-Seite.
  - Der Hinweistext kommt aus einem URL-Parameter (escaped; Textfälschung über einen präparierten Link möglich, wie
    beim bestehenden `?error=`).
  - Ein teilweise automatisch erkannter Name wird nicht zusätzlich als „geschützt“ hervorgehoben; maskiert wird er
    trotzdem.
  - Jede Rolle kann gewöhnliche großgeschriebene Wörter schützen (Übermaskierung, nie weniger Schutz).
- PDF/DOCX-Seitenbilder sind nicht auswählbar; markiert wird im extrahierten Text darunter.
- Die Markierung ist ein zusätzlicher Schutz, kein Nachweis vollständiger Erkennung. Synthetische Tests belegen
  **keine** vollständige Leckfreiheit.

## §57 Upload und KI-Verarbeitung getrennt – Chat und Schriftsatz-Generator (16.10.2026, Branch `feature/feedback-diagnose`, Basis `df2fa3f`)

Schließt die in §56.4 offen gelassene Lücke: Bisher wurde ein Dokument im Chat bzw. im Schriftsatz-Generator im
**selben Request** hochgeladen, ausgelesen und mitgesendet. Ein Name, den die automatische Erkennung übersah, ging in
dieser ersten Anfrage vor jeder Markiermöglichkeit im Klartext hinaus. Status: **teilweise nachgewiesen** (siehe 57.4).

### 57.1 Bestandsaufnahme (am Code, vor den Änderungen)

- **Chat** (`chat_router.send_message_stream` und `/send`): Dateien prüfen → Namensmarkierung prüfen →
  `ChatService.attach_document` (speichern, `DocumentProcessingService.process_document`: Extraktion/OCR, Klassifikation
  und Fristen **lokal**) → Nutzernachricht mit Anhang → sofort `send_message[_stream]` → `DraftingService._prepare_and_gate`
  (zentrale Prüfung) → Cloud.
- **Schriftsatz** (`schriftsatz_router.generate_schriftsatz`): Akte wählen/anlegen → Markierung speichern → Dateien
  speichern und verarbeiten → sofort `create_draft` → Cloud.
- **Keine weiteren automatischen Cloud-Auslöser beim Upload:**
  - Dokumentverarbeitung, OCR-Wiederholung, Mail-Abruf und Titelbildung (`generate_conversation_title`) laufen lokal.
  - Die Wiederaufnahme einer Datenschutz-Rückfrage (`pending-review`, `privacy-review`) verlangt eine offene
    **Nutzer**nachricht (`role == "user"`, letzte Nachricht, Bearbeitungssperre).
  - Dokument- und E-Mail-Aktionen (`from-document`, `from-message`) sind eigene, bewusste Aktionen und verlangen eine
    Akte.

### 57.2 Änderungen (bestehende Logik, keine parallele Architektur)

- **Serverseitig erzwungen:** Ein Request **mit Dateien** ist im Chat (beide Endpunkte) und im Schriftsatz-Generator
  ausschließlich ein Upload. Er speichert, liest lokal aus und ordnet der Akte zu, ruft aber weder
  `send_message[_stream]` noch `create_draft` auf. Das gilt auch für einen mitgetippten Text: Er wird **nicht**
  gesendet, nur der Titel einer neuen Unterhaltung entsteht wie bisher lokal daraus.
- **Chat:**
  - Neue Nachrichtenrolle `upload` (`ChatService.record_upload`) mit dem lokalen Hinweis „noch keine KI-Verarbeitung“
    und den Anhängen. Ist ein Dokument nicht lesbar, nennt der Hinweis das sofort (`readiness.blocked_state_sentences`).
  - Die Rolle ist nie Teil des Verlaufs (`_build_history` nimmt nur user/assistant) und nie eine offene
    Rückfrage-Nachricht.
  - Antwort auf den Upload: Weiterleitung bzw. Stream-Ereignis `uploaded` zur Chat-Dokumentansicht
    (`?uploaded=1#name-protection`) mit Hinweis und der §56-Markierung.
  - Ein mitgetippter Text bleibt nur im Browser (`sessionStorage` dieser Unterhaltung). Er steht nach dem Prüfen,
    auch über „Markierung speichern“ hinweg, wieder im Eingabefeld und wird erst beim Absenden entfernt.
- **Bewusste Aktion im Chat:** Die nächste Nachricht ohne Dateien verarbeitet die seit der letzten Antwort
  hochgeladenen Dokumente (`_message_scope`, `_referenced_document_ids`). Mehrere Uploads hintereinander werden
  vereinigt; eine unbeantwortete Zwischenfrage (offene Rückfrage) beendet den Bezug nicht; jede Antwort, auch ein
  lokaler Sperrhinweis, beendet ihn. Die wiederhergestellte Rückfrage (`pending-review`) prüft denselben Kontext. Datenminimierung §29 bleibt erhalten:
  nur diese Dokumente begrenzen den Kontext. Lesbarkeitssperre, gespeicherte Markierungen (`load_decisions`), Presidio,
  Pseudonymisierung, Leak-Check, Siegel und Rückführung laufen unverändert in `_prepare_and_gate`.
- **Schriftsatz-Generator:**
  - Mit Dateien wird die Seite neu angezeigt, mit Hinweis (`#upload-notice`), Links zur Prüfung je Dokument
    (Aktenansicht mit §56-Markierung), vorausgewählter Akte und den bisherigen Eingaben.
  - Erst „Schriftsatz generieren“ **ohne** Dateien ruft `create_draft` auf.
  - Die Schaltfläche heißt bei gewählten Dateien „Dokumente hochladen (noch keine KI)“.
- **Fehlerfälle (fail-closed, unverändert streng):**
  - Ungültige oder veraltete Markierung beim Upload: nichts gespeichert, nichts gesendet.
  - Veraltete Dokument-Markierung (`text_version`): abgewiesen.
  - Ungültige Markierung bei der Frage: nichts gesendet.
  - Scheitert die zentrale Prüfung: nichts gesendet.
  - Speicherfehler beim Upload: Abbruch vor jeder Verarbeitung.
- **Manipulation und Wiederholung:**
  - Eine Upload-Nachricht kann weder über `privacy-review` beantwortet noch über `pending-review` wiederaufgenommen
    werden.
  - Ein wiederholter Upload (Doppelklick, Retry) ist wieder nur ein Upload.
  - Der Bezug auf die Upload-Dokumente reicht nicht in andere Unterhaltungen oder Akten.

### 57.3 Tests und QA (nur synthetische Daten, kein Anthropic-Aufruf, kein Deployment)

- **Neu: `tests/test_upload_without_ai.py`** (19 Tests). Echter `AnthropicClaudeWritingProvider` (echte Factory und
  echtes SDK) gegen den lokalen Aufzeichnungsserver, toter Proxy, gefälschter Schlüssel.
  - Upload im Chat: **0 HTTP-Requests**, `api_call_logs` leer.
  - Schriftsatz-Upload: **0 HTTP-Requests**, kein Entwurf. `create_draft` beim Upload ist zur Probe verboten
    (würde den Test sprengen).
  - Erst die bewusste Frage bzw. Generierung erzeugt genau **einen** Request. Dessen **verifizierter Payload** enthält
    den nachträglich markierten Namen nicht, wohl aber Daten, Betrag und Norm des Dokuments, und nicht den lokalen
    Upload-Hinweis.
  - Außerdem abgedeckt: Rückführung des Namens in der Antwort, Lesbarkeitshinweis und -sperre, veraltete und
    ungültige Markierungen, ein Ausfall der zentralen Prüfung (nachweislich aufgerufen, fail-closed),
    Rückfrage-Manipulation, Wiederholung und Aktenisolation; nach dem Review zusätzlich: gleicher Kontext für
    `pending-review`, mehrere Uploads, Ende des Upload-Bezugs.
- **Angepasst:** 31 bestehende Tests, die Upload und Senden in einem Schritt erwarteten. Sie laufen jetzt zweistufig
  über gemeinsame Helfer und prüfen zusätzlich, dass der Upload-Schritt nichts sendet.
  - `test_web_chat`: 4
  - `test_web_schriftsatz`: 5
  - `test_chat_document_readiness`: 15
  - `test_web_privacy_review`: 2
  - `test_privacy_cloud_boundary_e2e`: 4
  - `test_web_document_name_protection`: 1. Die dort festgehaltene §56-Grenze („Zweigelt im ersten Versand“) ist
    jetzt umgekehrt belegt: Der erste Versand ist die bewusste Frage, ohne den Namen.
- **Browser-QA** (Edge headless, eigener Server, temporäre DB, kein Schlüssel, tote Cloud-Endpunkte):
  - Chat: Datei und Text abgesendet → Dokumentansicht mit Hinweis, Text im Eingabefeld, Upload-Nachricht sichtbar,
    0 API-Logs.
  - Markierung gespeichert: Entscheidung vorhanden, Text weiterhin im Eingabefeld.
  - Die bewusste Frage wurde im Browser abgefangen.
  - Schriftsatz: Hinweis, Prüf-Link, Eingaben und Akte erhalten, 0 Entwürfe, 0 API-Logs.
  - Keine JS-Fehler. Während des Uploads zeigt der Chat „Dokument wird lokal ausgelesen … (noch keine KI)“, ohne
    Nutzerblase mit dem nicht gesendeten Text.
- **Unabhängiges Review (QA-Agent):** kein Pfad gefunden, auf dem ein Request mit Dateien `send_message*`,
  `create_draft` oder einen Writer erreicht; Upload-Rolle nie im Verlauf, nicht beantwortbar; keine XSS-Stelle.
  Befunde und Umgang:
  - mittel – `pending-review` prüfte den Kontext der ganzen Akte statt des Upload-Bezugs: **behoben**, getestet;
  - mittel – Chat zeigte beim Upload „Lexono antwortet …“ und den Text als gesendete Blase: **behoben**,
    Browser-QA;
  - niedrig/mittel – Hinweis versprach den Text im Eingabefeld auch ohne JavaScript bzw. Browser-Speicher:
    **umformuliert** (bedingt);
  - niedrig – mehrere Uploads, offene Zwischenfrage: **behoben** (vereinigt). Das Weiterführen des Bezugs über einen
    Sperrhinweis hinweg wurde verworfen, weil es das getestete Verhalten „allgemeine Fragen nach einer Sperre“ brach;
  - niedrig – Hinweistext blieb nach dem Senden scheinbar aktuell: **umformuliert** („beim Hochladen …“);
  - niedrig – Schriftsatz: Upload-Antwort ohne Redirect (F5 sendet erneut: weitere Schnellakte, doppelte Dokumente,
    keine KI) und Teilpersistenz bei einem Größenfehler im zweiten Dokument: **offen**, siehe 57.5;
  - niedrig – Testlücken: ergänzt (siehe oben).
- **Benchmarks:** Pseudonymisierung (alle Kennzahlen) identisch zur §56-Baseline.
- **Gesamtsuite** (6 Teile, Vordergrund, nach den Review-Korrekturen): **5156 bestanden**, 10 xfail, 1 xpass
  (vorbestehend), 1 übersprungen, 4 fehlgeschlagen – die fremden Logo-Tests.
- **Belegt bzw. nicht belegt:**
  - Belegt sind die unverschlüsselten Payloads auf diesem Rechner (Loopback).
  - TLS zum echten Anthropic-Endpunkt und der Relay-Pfad gegen einen realen Server sind **nicht** belegt.

### 57.4 Mail- und Intake-Dokumente ohne Akte (untersucht, keine Nebenlösung umgesetzt)

- **Zuordnung:** Ein Dokument aus dem Mail-Abruf oder Intake bekommt erst mit der Zuordnung eine Akte:
  - automatisch: `app/matching/service.py:48-51`;
  - manuell: `app/web/router.py:570-573`, Akte samt Anhängen.
- **Vorher keine Cloud:** `from-document` und `from-message` (`app/web/chat_router.py:899`, `:982`) verlangen eine
  Akte. Ohne Akte erreicht kein Inhalt die Cloud.
- **Sicherer Ablauf:**
  1. zuordnen;
  2. in der Aktenansicht des Dokuments prüfen und markieren (§56; die Entscheidung gilt aktenweit);
  3. erst danach die KI-Aktion starten.
- **Bewusst nicht umgesetzt:** eine Markierung ohne Akte. Sie bräuchte einen eigenen, aktenlosen
  Entscheidungsspeicher mit offener Zuordnungs- und Isolationsfrage, also eine fachliche Entscheidung.

### 57.5 Grenzen und offene Risiken

- **Upload-Bezug endet mit der nächsten Antwort:** Danach werden Uploads derselben Unterhaltung wie bisher nur bei
  ausdrücklichem Dokumentbezug herangezogen.
- **Ungeprüftes Dokument gehört sofort zur Akte (fachlich zu bestätigen):** Ein hochgeladenes, noch nicht geprüftes
  Dokument ist mit dem Upload Teil der Akte. Spätere KI-Aktionen mit Aktenkontext (andere Unterhaltung derselben Akte,
  Schriftsatz-Generierung, Folgefragen mit Dokumentbezug) beziehen es ein, auch wenn niemand es markiert hat. Das
  entspricht dem bisherigen Akten-Upload; immer laufen die zentrale Prüfung und gespeicherte Markierungen. Ob ein
  Dokument erst nach ausdrücklicher Prüfung KI-fähig sein soll, ist eine offene fachliche Entscheidung (nicht
  eigenmächtig umgesetzt).
- **Schriftsatz ohne Redirect nach dem Upload** (**geschlossen in §58** durch eine Vorgangs-ID): Ein erneutes
  Absenden per F5 legt eine weitere Schnellakte und doppelte Dokumente an (keine KI). Ein Redirect müsste die Formularwerte in die URL legen (Personenbezug im Verlauf) und wurde
  deshalb nicht gewählt. Ein Größenfehler im zweiten Dokument hinterlässt wie bisher die Akte mit dem ersten.
- **Der eingegebene Text liegt im Browser:** Der mitgetippte Text bleibt bis zum Absenden im `sessionStorage` des
  Browsers, also nur lokal, im Tab und auf dem Gerät. Ein neuer Chat übernimmt ihn als lokalen Titel (wie bisher),
  gesendet wird er nicht.
- **Keine vollständige Erkennung:** Die Markierung bleibt ein zusätzlicher Schutz. Was weder erkannt noch markiert
  wird, geht bei der bewussten Aktion im Klartext hinaus, nun aber erst nach einer Prüfmöglichkeit.
  Synthetische Tests belegen **keine** vollständige Leckfreiheit.
- **Weitere Wege:** Die Akten-Uploads („Dokumente“ der Akte) waren schon vorher reine Uploads und sind unverändert.

## §58 Idempotenz im Schriftsatz-Generator – F5, Doppelklick, Wiederholung (17.10.2026, Branch `feature/feedback-diagnose`, Basis `4c0d808`)

Status: **teilweise nachgewiesen**. Upload-Trennung (§57), Namensmarkierung und zentrale Prüfung sind unverändert.

### 58.1 Befund (reproduziert)

- Derselbe POST zweimal (F5 auf der Ergebnisseite) ergab **2 Schnellakten und 2 Dokumente**, beide Antworten 200, ohne
  KI-Aufruf.
- Für „Schriftsatz generieren“ mit neuer Akte hätte ein Doppelklick zwei Akten und zwei Entwürfe erzeugt, also
  **zwei Cloud-Anfragen**.
- Einen Idempotenzschutz gab es nicht. Das nächste vorhandene Muster ist die Bearbeitungssperre im Chat
  (`chat_message_claims`).

### 58.2 Lösung (bestehende Muster, kein neuer Mechanismus daneben)

- **Vorgangs-ID je Seite** (`app/drafting/schriftsatz_operations.py`):
  - Jede ausgelieferte Generator-Seite bekommt eine neue, serverseitig ausgestellte ID (uuid4).
  - Sie ist signiert (itsdangerous, prozessweiter Session-Schlüssel, eigenes Salt), an den Nutzer gebunden und
    24 h gültig.
  - Fehlt sie oder ist sie manipuliert, fremd oder abgelaufen, wird **nichts** angelegt. Das ist serverseitig
    erzwungen, keine UI-Sperre.
- **Atomarer Vorgang** (`schriftsatz_operations`, Migration `schritt3_032`, rein additiv):
  - Die ID ist Primärschlüssel. Genau ein Request legt die Zeile an, auch prozessübergreifend, geschrieben in
    eigener kurzer Sitzung wie `processing_claims`.
  - Gespeichert werden nur IDs und Zustände: Akte, fortlaufend die Dokument-IDs, Entwurf, Status, außerdem ein
    Fingerabdruck der Anfrage. Der Fingerabdruck ist ein HMAC mit dem Session-Schlüssel über Art, Formularfelder und
    Datei-Hashes. Keine Formularinhalte, keine Dateinamen im Klartext.
  - **Geänderte Anfrage mit alter ID** (z. B. über „Zurück“ bearbeitet): Sie wird weder ausgeführt noch still auf das
    alte Ergebnis umgeleitet. Es erscheint eine neue Seite mit Hinweis und erhaltenen Eingaben. Die Generator-Seite
    wird nicht im Browser-Cache gehalten (`Cache-Control: no-store`).
- **Wiederholung desselben Vorgangs** (F5, erneutes Absenden, Doppelklick, paralleler Request, Wiederholung nach
  verlorener Antwort):
  - Ergebnis „Upload erledigt“: dieselbe Seite mit „bereits ausgeführt“ und denselben Dokumenten.
  - Ergebnis „Generierung erledigt“: Weiterleitung auf denselben Entwurf.
  - Fehlgeschlagen oder unterbrochen: Hinweis, nichts wird erneut ausgeführt, kein zweiter Cloud-Aufruf. Läuft der
    Vorgang noch (jünger als 15 min), lautet der Hinweis „bitte warten“, nicht „neu starten“.
  - Gelöschte Dokumente oder Akten (§26) erscheinen auch in einer Wiederholung nicht.
  - Ein unerwarteter Absturz lässt den Vorgang auf „processing“. Unklar ist dann, ob schon gesendet wurde, also
    gibt es keine automatische Wiederholung (fail-closed).
- **Bewusst neuer Vorgang bleibt möglich:** Jede Ergebnis- oder Fehlerseite trägt eine neue ID, etwa für dieselbe
  Datei noch einmal oder einen weiteren Upload. Nach einem Teilfehler wählt die Fehlerseite die bereits angelegte Akte
  vor (`?matter_id=`, nur die ID), statt eine weitere Schnellakte anzulegen.
- Keine Formularwerte in URL, Redirect oder Verlauf. Die Fehler-URL ist jetzt kodiert, und die Größen-Fehlermeldung
  nennt keinen Dateinamen mehr. Keine Bereinigung vorhandener Akten oder Dokumente.

### 58.3 Tests und QA (synthetisch, isolierte Datenbanken, kein Anthropic-Aufruf)

- **Neu: `tests/test_schriftsatz_idempotency.py`** (20 Tests), deckt ab:
  - F5 bzw. verlorene Antwort (3 Wiederholungen): 1 Akte, 1 Dokument, **0 HTTP-Requests** am
    Aufzeichnungsserver.
  - Wiederholung in eine bestehende Akte.
  - Ein bewusster zweiter Upload derselben Datei ist möglich.
  - Bewusste Generierung nach dem Upload: genau **1 Request**, die Wiederholung leitet auf denselben Entwurf,
    kein zweiter Request.
  - Doppelklick auf „generieren“ mit neuer Akte.
  - Absturz mitten im Vorgang (500, danach Wiederholung): nichts doppelt.
  - Teilfehler: Akte bleibt erhalten und ist vorausgewählt.
  - Fehlende, gefälschte, fremde und abgelaufene IDs.
  - 8 parallele Claims (genau ein Gewinner).
  - 2 echte parallele HTTP-Requests auf einer Datei-DB: 1 Akte, 1 Dokument; die Überlappung ist belegt, der zweite
    Request trifft den laufenden Vorgang (5 von 5 Läufen stabil).
  - Nach dem Review: geänderte Anfrage mit alter ID (Felder bzw. Dateien), lange unterbrochener Vorgang, gelöschtes
    Dokument, `no-store` und kodierte Fehler-URL ohne Dateinamen.
- **Migration:** Auf- und Abbau getestet (`test_migrations`). Bestehende Generator-Tests senden die ID der Seite mit.
- **Browser-QA** (Edge headless, eigener Server, temporäre DB, tote Cloud-Endpunkte): echtes F5 auf der
  Upload-Ergebnisseite. Der Browser sendet den POST erneut, die Seite zeigt „bereits ausgeführt“, es bleibt bei
  1 Akte, 1 Dokument und 0 API-Logs, ohne JS-Fehler.
- **Unabhängiges Review (QA-Agent):** kein Pfad zu Duplikaten oder einem zweiten Cloud-Aufruf über dieselbe ID gefunden;
  Atomarität, Reihenfolge, Token-Prüfung, §57-Trennung und Migration ohne Befund. Behoben wurden: mittel –
  Zurück-Button mit geänderter Anfrage wurde still auf das alte Ergebnis umgeleitet; niedrig –
  „neu starten“-Aufforderung bei noch laufendem Vorgang, gelöschte Dokumente in der Wiederholung, widersprüchliches
  Banner bei Fehlern, unkodierte Fehler-URL mit Dateinamen, fehlender Entwurf, zu schwacher Paralleltest.
- **Gesamtsuite** (6 Teile, nach den Review-Korrekturen): **5177 bestanden**, 10 xfail, 1 xpass, 1 übersprungen, 4 fehlgeschlagen – die fremden
  Logo-Tests.

### 58.4 Grenzen

- **Datenbankmigration:** Die Desktop-App wendet `schritt3_032` beim Start automatisch an (`run.py`, mit vorheriger
  Sicherung). Ein direkt per `uvicorn` gestarteter Entwicklungsserver braucht `alembic upgrade head`. Auf der realen
  Datenbank wurde in dieser Sitzung **nichts** migriert.
- **Hängender Vorgang:** Ein nach einem Absturz auf „processing“ stehender Vorgang bleibt dauerhaft gesperrt. Der
  Nutzer startet bewusst neu, eine automatische Aufräumung gibt es nicht. Eine dabei angelegte Schnellakte bleibt
  bestehen und ist auf der Fehlerseite vorausgewählt.
- **Nur der Generator** (Chat-Upload **nachgezogen in §59**): Chat-Upload und Akten-Upload sind nicht Teil dieses
  Schritts. Der Chat sendet per `fetch`
  bzw. leitet nach `/send` weiter (kein F5-Neusenden); ein Doppelklick ist dort nur clientseitig gesperrt.
- **Keine Inhaltserkennung:** Zwei bewusst getrennte Vorgänge mit derselben Datei legen weiterhin zwei Dokumente an
  (gewollt).

## §59 Idempotenz beim Chat-Upload – Doppelklick, parallele Requests, Wiederholung (17.10.2026, Branch `feature/feedback-diagnose`, Basis `d50e820`)

Status: **teilweise nachgewiesen**. Upload-Trennung (§57), Namensmarkierung und zentrale Prüfung sind unverändert.

### 59.1 Befund (reproduziert)

- **Doppelte Vorgänge:** Derselbe Upload-Request zweimal, einmal neu und einmal in einer bestehenden Unterhaltung,
  ergab **2 Unterhaltungen, 4 Dokumente und 4 Upload-Nachrichten**, ohne KI-Aufruf.
- **Ursache:** Beide Endpunkte (`send-stream` und `/send`) legten Unterhaltung und Dokumente an, ohne die Anfrage
  wiederzuerkennen. Gesperrt war bisher nur clientseitig (`isSending`).

### 59.2 Lösung (Mechanismus aus §58 wiederverwendet, kein zweiter daneben)

- **Gemeinsamer Mechanismus:** `app/drafting/schriftsatz_operations.py` bekommt Modell und Salt als Parameter.
  `app/chat/upload_operations.py` ist eine dünne Schicht darüber mit eigenem Salt, damit eine Generator-ID nicht im
  Chat gilt und umgekehrt.
- **Neue Tabelle:** `chat_upload_operations` (Migration `schritt3_033`, rein additiv). Sie speichert nur IDs, Status
  und einen HMAC-Fingerabdruck über Unterhaltung, Akte, Text, Briefkopf, Markierung und Datei-Hashes. Inhalte und
  Dateinamen werden nicht gespeichert.
- **Upload-Vorgangs-ID im Eingabefeld:** Jede Chat-Seite trägt eine signierte, nutzergebundene ID. Verbraucht wird sie
  **nur von einem Request mit Dateien**. Eine spätere, bewusst gesendete Nachricht ohne Dateien braucht keine ID und
  ist ein eigener Vorgang. Fehlt die ID oder ist sie gefälscht, fremd, abgelaufen oder für einen anderen Zweck
  ausgestellt, wird nichts angelegt.
- **Gemeinsamer Upload-Pfad** (`_chat_upload`) für beide Endpunkte:
  1. Prüfungen ohne Persistenz;
  2. atomarer Claim über den Primärschlüssel in eigener kurzer Sitzung;
  3. erst danach Unterhaltung, Dokumente (fortlaufend vermerkt), Markierung und Upload-Nachricht.
- **Wiederholung desselben Uploads:** dasselbe Ergebnis (Stream `uploaded` mit `repeated`, `/send` mit gleicher
  Weiterleitung), nichts wird doppelt angelegt.
- **Veränderte Anfrage mit benutzter ID:** Sie wird weder ausgeführt noch als identisch behandelt.
- **Gleichzeitige Wiederholung (Doppelklick):** Der zweite Request wartet bis zu 10 s auf den laufenden ersten und
  liefert dann dessen Ergebnis, statt eine Fehlermeldung zu zeigen, während der Upload gelingt.
- **Fehlgeschlagen:** Hinweis ohne erneute Ausführung; im Stream werden bereits gespeicherte Dateien genannt, in der
  `/send`-URL nur ihre Anzahl. Weil der Upload keine KI-Anfrage auslöst, gilt auch ein Absturz als eindeutiger
  Fehlschlag, nicht als „unklar“.
- **Abbruch zwischen `start` und `uploaded`:** Trägt die Wiederholung die Unterhaltung desselben Vorgangs, gilt sie
  als identisch.
- **Neue ID nach jeder Antwort:** Jede Upload-Antwort im Stream liefert eine neue ID für das Eingabefeld. So bleibt ein
  bewusst neuer Upload ohne Neuladen möglich, auch nach einem Fehler.

### 59.3 Tests und QA (synthetisch, isolierte SQLite-Datenbanken, kein Anthropic-Aufruf)

- **Neu: `tests/test_chat_upload_idempotency.py`** (22 Tests), deckt ab:
  - Doppelklick bzw. verlorene Antwort: 1 Unterhaltung, 1 Dokument, 1 Upload-Nachricht, **0 HTTP-Requests** am
    Aufzeichnungsserver.
  - Wiederholung in eine bestehende Unterhaltung; `/send` dreimal ergibt dieselbe Weiterleitung.
  - Bewusst neuer Upload mit der neuen ID.
  - Veränderte Anfrage (Text, Datei, Unterhaltung).
  - Fehlende, gefälschte, fremde und abgelaufene IDs sowie IDs des Generators und umgekehrt.
  - Teilfehler (zweite Datei zu groß), Absturz zwischen Speicherung und Upload-Nachricht (500, danach Wiederholung).
  - Upload ohne Cloud-Aufruf, die bewusste Frage mit genau **einem** Request (Payload enthält den Dokumentinhalt);
    eine spätere Wiederholung des Uploads sendet nichts.
  - Eine Nachricht ohne Dateien verbraucht keine ID.
  - 2 echte parallele Requests auf einer Datei-DB: beide erhalten dasselbe Ergebnis, einer als Wiederholung. Das
    Warten auf den laufenden Vorgang ist belegt, 5 von 5 Läufen stabil.
  - Nach dem Review: keine Dateinamen in der `/send`-Fehler-URL, kodierte Fehlermeldung (`#`, `&`), Wiederholung
    nach dem `start`-Ereignis.
  - Vorgangszeile ohne Inhalte.
- **Bestehende Tests:** Uploads senden die ID der Seite mit, sonst ist nichts geändert. Der Migrationstest
  (`schritt3_033`) ist ergänzt.
- **Browser-QA** (Edge headless, eigener Server, temporäre DB, tote Cloud-Endpunkte):
  - Der normale Upload über die Oberfläche ist unverändert (Weiterleitung zur Dokumentansicht).
  - Zwei gleichzeitige identische Requests derselben Seite, an der UI-Sperre vorbei: ein Upload, beide Antworten
    `uploaded` (eine als Wiederholung), 0 API-Logs, keine JS-Fehler.
- **Unabhängiges Review (QA-Agent):** kein hoher Befund, keine Doppelanlage; Claim atomar, §57 und zentrale Prüfung
  unberührt, Nachrichtenpfad ohne Dateien unverändert. Behoben wurden:
  - mittel – ein Absturz wurde als „läuft noch“ gemeldet;
  - mittel – der Verlierer eines `/send`-Doppelklicks sah eine Fehlerseite;
  - mittel – durch §59 wären Dateinamen in die `/send`-URL gelangt;
  - niedrig – Wiederholung nach dem `start`-Ereignis, toter Code, Typangaben, Leerzeichen.
- **Gesamtsuite** (6 Teile, nach den Review-Korrekturen): **5200 bestanden**, 10 xfail, 1 xpass, 1 übersprungen, 4 fehlgeschlagen – die fremden
  Logo-Tests.
- **PostgreSQL:** Das Verhalten mehrerer Prozesse ist **nicht verifiziert**, getestet ist nur SQLite.

### 59.4 Grenzen

- **Ohne JavaScript (`/send`):** Ergebnis- und Fehlerseiten tragen eine neue ID; ein Doppelklick wird serverseitig als
  ein Vorgang erkannt. Die Dateiauswahl muss nach einem Fehler, wie bisher, neu getroffen werden.
- **Hängende Vorgänge** (**geschlossen in §60**: Wiederaufnahme bei nachweislich beendetem Besitzer): Ein durch
  Absturz auf „processing“ stehender Upload bleibt gesperrt, wie in §58. Dokumente
  ohne Upload-Nachricht bleiben dann in der Unterhaltungsakte; neu hochgeladen wird bewusst.
- **Mehrere Tabs und Zurück-Button** (Zurück-Button **geschlossen in §60**): Jeder Tab hat eine eigene ID. Derselbe Upload in zwei Tabs gilt als zwei
  bewusste Vorgänge. Eine per „Zurück“ wiederhergestellte Seite trägt eine verbrauchte ID: Ein anderer Upload wird
  abgewiesen (mit neuer ID), der zweite Versuch gelingt.
- **Dateiname in Fehler-URL (vorbestehend, geschlossen in §60):** Die Größen-Fehlermeldung des Chats beim ersten Fehlschlag nennt den
  Dateinamen und landet im Pfad ohne JavaScript in der Fehler-URL. §59 fügt keine weiteren Namen hinzu.

## §60 Chat-Upload: harter Abbruch, Dateinamen in Fehler-URLs, Zurück-Button (17.10.2026, Branch `feature/feedback-diagnose`, Basis `83aeaaa`)

Status: **teilweise nachgewiesen**. Upload-Trennung (§57), Idempotenz (§58/§59), Namensmarkierung und zentrale Prüfung
sind unverändert.

### 60.1 Befund (am Code)

- **Absturzpunkte eines Uploads**, jeweils eigener Commit:
  1. Schnellakte;
  2. Unterhaltung;
  3. Datei geschrieben, danach Dokument-Commit;
  4. Verarbeitung (Text/OCR);
  5. Vermerk am Vorgang;
  6. Upload-Nachricht und ihre Dokumentverknüpfungen (bisher **zwei** Commits);
  7. Status.
- **Folge eines harten Abbruchs:** Der Vorgang blieb dauerhaft auf `processing`. Eine Wiederholung führte ihn nie aus,
  und Teildaten blieben zurück: Unterhaltung und Dokumente ohne Upload-Nachricht.
- **Dateinamen in Fehler-URLs** (Pfad ohne JavaScript):
  - Größenfehler im Chat;
  - Dateityp-Fehler im Chat;
  - Größenfehler beim Akten-Upload; dort war die Meldung zusätzlich unkodiert.
- **Zurück-Button:** Eine aus dem Browser-Cache wiederhergestellte Seite trug eine bereits verbrauchte Kennung.

### 60.2 Lösung

- **Wiederaufnahme statt Bereinigung, nichts wird gelöscht:**
  - Beim Anlegen des Vorgangs werden alle IDs vorab vergeben und als Plan gespeichert (Migration `schritt3_034`):
    Schnellakte, Unterhaltung, je Datei ein Dokument, Upload-Nachricht.
  - Jeder Schritt ist „anlegen, falls unter der geplanten ID noch nicht vorhanden“. Die Datei bekommt einen festen
    Pfad je Dokument-ID, ein Dokument mit unterbrochener Verarbeitung wird lokal neu verarbeitet. Upload-Nachricht
    und Verknüpfungen entstehen in einem Commit.
  - Auch ein Commit mit unklarem Ausgang führt so nicht zur Doppelanlage.
- **Besitzer statt Zeit:**
  - Der Vorgang speichert einen Hash des Rechnernamens, Prozess-ID, Prozess-Startzeit und eine Kennung je
    Ausführungsversuch (`app/chat/process_owner.py`, ohne Zusatzpaket).
  - Übernommen wird nur bei **nachweislich beendetem** Besitzer. Im eigenen Prozess entscheidet die Liste der
    laufenden Vorgänge (Referenzzähler, vor dem Anlegen gesetzt). Bei einem fremden Prozess auf demselben Rechner
    gilt er als beendet, wenn die Prozess-ID fehlt oder der Startzeitpunkt nicht mehr passt.
  - Die Übernahme ist atomar (Compare-and-Set auf die Versuchskennung), genau ein Request setzt fort, auch wenn der
    beendete Besitzer der eigene Prozess war.
  - Als beendet gilt ein fremder Prozess nur bei „keine solche Prozess-ID“ oder abweichender Startzeit; jeder andere
    Fehler der Abfrage gilt als „nicht prüfbar“.
  - **Zeitablauf gibt nie frei.** Ein lebender Besitzer wird auch nach Stunden nicht übernommen.
  - Ist der Besitzer nicht prüfbar (anderer Rechner, Altbestand), bleibt der Vorgang unangetastet; ein bewusst neuer
    Upload mit neuer Kennung ist möglich.
- **Kontrollierter Fehlschlag**, wenn der Stand nicht stimmig rekonstruierbar ist: Dokument, Unterhaltung oder
  Schnellakte inzwischen gelöscht (erkannt über Fortschrittsmarker im Plan, damit nichts wiederbelebt wird), Vorgang
  ohne Plan, abweichende Dateizahl oder fremde Unterhaltung. Die Namensmarkierung wird bei der Wiederaufnahme nicht
  erneut gezählt. Dann wird `failed` gesetzt, nichts gelöscht,
  und ein Neustart ist mit neuer Kennung möglich.
- **Keine Dateinamen in URLs:**
  - feste Meldung für den Größenfehler (`UploadTooLargeError`), der Name erscheint nur lokal im Stream;
  - Dateityp-Fehler nur mit einer kurzen technischen Endung, sonst „?“ (auch im Schriftsatz-Generator);
  - Größenfehler beim Akten-Upload ohne Namen und kodiert.
- **Zurück-Button:**
  - Chat-Seiten werden nicht im Browser-Cache gehalten (`no-store`).
  - Nach einer Wiederherstellung (`pageshow`) holt die Seite eine neue Kennung von `GET /dashboard/chat/upload-token`.
    Der Endpunkt stellt nur eine Kennung aus und schreibt nichts.
  - Ein Upload wird nie automatisch wiederholt. Eine verbrauchte Kennung mit anderem Inhalt wird abgewiesen, wie in
    §59.

### 60.3 Tests und QA (synthetisch, isolierte SQLite-Datenbanken, kein Anthropic-Aufruf)

- **Neu: `tests/test_chat_upload_recovery.py`** (30 Tests), deckt ab:
  - harter Abbruch an **6 Speicherpunkten** (nach Akte, nach Unterhaltung, vor Dokument-Commit, während der
    Verarbeitung, vor dem Vermerk, nach der Upload-Nachricht);
  - Wiederaufnahme jeweils mit genau 1 Unterhaltung, 1 Dokument, 1 Datei, 1 Upload-Nachricht samt Verknüpfung und
    1 Akte; das Dokument ist fertig verarbeitet; eine weitere Wiederholung liefert nur das Ergebnis;
  - **0 HTTP-Requests** am Aufzeichnungsserver;
  - zwei Dateien in einer bestehenden Unterhaltung;
  - die bewusste Frage nach der Wiederaufnahme ergibt genau 1 Request;
  - lebender Besitzer, auch Stunden alt: keine Übernahme; nach seinem Ende Übernahme;
  - nicht prüfbarer Besitzer: keine Übernahme, sicherer Neustart;
  - Prozess-Lebenszeichen inklusive wiederverwendeter Prozess-ID;
  - 8 parallele Übernahmen mit genau einem Gewinner; 2 parallele Wiederaufnahme-Requests, von denen einer fortsetzt
    und einer wartet (5 von 5 Läufen stabil);
  - kontrollierter Fehlschlag;
  - keine Dateinamen in Fehler-URLs (Chat und Akte);
  - Zurück-Button mit neuer Kennung ohne Schreibvorgang;
  - Endpunkt nur angemeldet; Vorgangszeile ohne Inhalte und ohne Klartext-Rechnernamen;
  - nach dem Review: doppelte Übernahme eines eigenen beendeten Versuchs ausgeschlossen, gelöschte Unterhaltung nicht
    wiederbelebt, Markierung nicht doppelt gezählt, Dateiendung mit Punkt im Namen.
- **Migration** `schritt3_034` auf und ab.
- **Browser-QA** (Edge headless, eigener Server, temporäre DB, tote Cloud-Endpunkte): Upload, danach `history.back()`.
  Die Seite trägt eine neue Kennung, es wird nichts erneut hochgeladen. Der bewusst zweite Upload gelingt, mit
  0 API-Logs und ohne JS-Fehler.
- **Unabhängiges Review (QA-Agent):** Absturzpunkte, Atomarität der Upload-Nachricht, Neuverarbeitung,
  Pfadsicherheit, Token-Endpunkt, Fehler-URLs, §57 und Migration ohne Befund. Behoben wurden:
  - mittel – im eigenen Prozess konnte die Übernahme mehrfach gelingen (gleiche alte und neue Besitzerkennung);
  - mittel – eine nach dem Abbruch gelöschte Unterhaltung wäre wiederbelebt worden;
  - niedrig – zu großzügige Prozessprüfung, unvollständige ctypes-Signaturen, Hinweistext, Dateiendung,
    Rechnername im Klartext, doppelte Zählung der Markierung, gefährlicher Default.
- **Gesamtsuite** (6 Teile, nach den Review-Korrekturen): **5231 bestanden**, 10 xfail, 1 xpass, 1 übersprungen, 4 fehlgeschlagen – die fremden
  Logo-Tests.
- **PostgreSQL** gehört derzeit nicht zur vorgesehenen Zielarchitektur (SQLite-Prototyp, CLAUDE.md) und wurde nicht
  geprüft. Compare-and-Set und Primärschlüssel-Claim sind dort **nicht verifiziert**.

### 60.4 Grenzen

- **Mehrere Rechner:** Teilen sich Rechner eine Datenbank, ist der Besitzer auf dem fremden Rechner nicht prüfbar. Der
  Vorgang bleibt dann gesperrt, ein neuer Upload ist möglich, die Teildaten bleiben in der Unterhaltung.
- **Ohne Wiederholung kein Abschluss:** Ein abgebrochener Upload, den niemand wiederholt, bleibt mit Teildaten stehen.
  Eine automatische Bereinigung gibt es bewusst nicht (keine Löschung).
- **Anderes Konto:** Gehört der Besitzer zu einem anderen Benutzerkonto auf demselben Rechner (Zugriff verweigert),
  gilt er als nicht prüfbar.
- **Lebender Prozess, abgebrochener Request:** Lebt der Prozess eines fremden Besitzers weiter, aber sein Request
  wurde hart abgebrochen, gilt der Vorgang weiter als laufend. Der Hinweis verweist dann auf einen bewusst neuen
  Upload.
- **Schriftsatz-Generator (§58)** (**geschlossen in §61.2**): Dort bleibt ein hart abgebrochener Vorgang weiterhin
  gesperrt. Der Generator war
  nicht Teil dieses Schritts; die Generierung ist bewusst fail-closed, weil eine Cloud-Anfrage erfolgt sein könnte.

## §61 Arbeitspakete P1–P5 (17.10.2026, Branch `feature/feedback-diagnose`, Basis `666685f`)

Sequenziell abgearbeitet, je Paket mit Bestandsaufnahme und Qualitätsgate. Status: **teilweise nachgewiesen**;
keine Aussage über vollständige Leckfreiheit oder Rechtskonformität.

### 61.1 P1 – Dokumentfreigabe: Inventur der KI-Pfade

**Was „Freigabe“ hier umfasst:** Einen eigenen Freigabe-Mechanismus „Dokumentfreigabe“ gibt es im Code nicht. Vor
jeder Cloud-Verarbeitung greifen:
- die zentrale Prüfung (`ClaudePrivacyGateway.prepare_request`, Siegel);
- Rückfrage und Namensmarkierung (§51–§56, an den angezeigten Textstand gebunden);
- die Lesbarkeitsprüfung;
- die Trennung von Upload und Verarbeitung (§57).

Eine zusätzliche Pflicht-Freigabe je Dokument vor jeder KI-Aktion bleibt eine **offene fachliche Entscheidung**
(§57.5) und wurde nicht eigenmächtig eingeführt.

**Inventur (am Code, Ist-Stand):**
- **Cloud-Provider:** Alle vier (direkt und Gateway, Schreiben und Prüfung) verlangen vor dem Netzaufruf ein gültiges
  Siegel. Versiegelt wird nur in `prepare_request` bzw. `extend_with_local_note` (mit erneuter Prüfung).
- **Auslöser:** Alle Cloud-Auslöser sind POST-Aktionen über `DraftingService._prepare_and_gate` bzw. `ReviewEngine`:
  - Chat (`send`, `send-stream`, `from-document`, `from-message`, `privacy-review`);
  - Entwurf (`regenerate`, `instructions/apply`, `ai-edit`, `review`);
  - Schriftsatz `generate`, nur ohne Dateien.
  - Gespeicherte Markierungen laden alle Pfade aktenweit.
- **GET:** Keine GET-Route ruft einen Provider auf. `pending-review` prüft nur lokal.
- **Lokal:** Hintergrundaufgaben (Mail-Abholung, Updates, Gesundheitsprüfung), Klassifikation, Fristerkennung,
  Einbettungen, OCR, Titelbildung und Spracherkennung laufen lokal. Eine Umgehung wurde nicht gefunden.
- **Dokumenttext:** Nur die Verarbeitung (`process_document`) ändert ihn. Markierungen wirken aktenweit
  (Übermaskierung, kein Leck). `text_version` bindet nur **neue** Markierungen an den angezeigten Text; die zentrale
  Prüfung läuft bei jedem Versand über den aktuellen Text.

**Ergänzt:**
- **`tests/test_no_ai_on_open.py`:** Ruft **alle GET-Routen** (Stand: 99, zur Laufzeit aufgezählt, neue Routen
  automatisch) mit synthetischen Daten auf. Dazu gehören Mail, Dokumente mit Namen, Entwürfe, Frist, Wissensbasis und
  ein Chat mit Upload-Nachricht.
  - Spione liegen auf allen Cloud-Providern und dem Gateway-Client; jede Socket-Verbindung außer Loopback wird
    gesperrt.
  - **Ergebnis:** 0 Provider-Aufrufe, 0 Netzverbindungen, keine Server-Fehler.
  - Dazu kommt der direkte Akten-Upload (dritter Upload-Weg): lokal ausgelesen, geöffnet, 0 Provider-Aufrufe,
    0 Netzverbindungen.
- **Dabei gefunden und behoben (kein Datenschutzpfad):** DOCX- und PDF-Export von Entwürfen und generierten Dokumenten
  setzten einen Dateinamen mit Umlaut roh in den Kopf `Content-Disposition`. Das ist nicht RFC-6266-konform; der
  Test-Client brach mit `UnicodeDecodeError` ab. Jetzt steht ein ASCII-Ersatzname in `filename=` und der volle Name in
  `filename*=UTF-8''…` (`app/export/filenames.attachment_header`).
  - Ob der Export im installierten Build mit WebView2 sichtbar fehlschlug, ist **nicht verifiziert**.
  - Nebenbefund: Die globale `NoStoreMiddleware` setzt `no-store` ohnehin für alle Antworten; der zusätzliche Kopf
    aus §60 ist redundant, aber unschädlich.

**QA P1 (Repository-Tests, SQLite, TestClient; kein Browser, keine installierte App):**

| Prüfung | Status |
|---|---|
| GET-Routendurchlauf ohne KI und Netz | PASS |
| Akten-Upload ohne KI und Netz | PASS |
| Export-Tests (DOCX/PDF, Aktenseite) | PASS |
| Gesamtsuite: 5233 bestanden, 4 fremde Logo-Fehler | PASS |
| Installierter Build | NICHT VERIFIZIERT |

### 61.2 P2 – Idempotenz: harter Abbruch im Schriftsatz-Generator

**Befund:**
- Ein hart abgebrochener Generator-Vorgang blieb dauerhaft `processing`, ohne Besitzer und ohne Plan.
- Die Wiederholung meldete **zeitabhängig**: unter 15 min „bitte warten, nicht neu starten“, auch wenn der Prozess
  längst beendet war, danach „unterbrochen“.
- Ein abgebrochener Upload hinterließ Schnellakte und Dokumente ohne Abschluss.

**Lösung (Mechanismus aus §60, keine Kopie):**
- **Gemeinsamer Prozess-Merker:** `process_owner.LocalRegistry`, je eine Instanz für Chat und Generator; das
  Chat-Verhalten bleibt unverändert.
- **Besitzer und Plan:** Der Generator speichert beides (Migration `schritt3_035`, additiv). Der Plan enthält die
  geplante Schnellakte und je Datei eine Dokument-ID.
- **Wiederholung desselben Vorgangs:**
  - Lebt der Besitzer nachweislich, wartet sie höchstens 10 s; danach kommt „bitte warten“.
  - Ist er nachweislich beendet, folgt die atomare Übernahme.
  - **Upload:** wird fortgesetzt, Schritt für Schritt „anlegen, falls nicht vorhanden“. Dazu gehören die
    Schnellakte unter der geplanten ID, Dokumente mit festem Dateipfad und das Nachholen einer abgebrochenen lokalen
    Verarbeitung. Die Markierung wird einmal gespeichert, gelöschte Objekte führen zum kontrollierten Fehlschlag.
  - **Generierung:** wird **nie** wiederholt, denn ob die KI-Anfrage schon gesendet war, ist unklar. Es folgt ein
    kontrollierter Fehlschlag mit Hinweis; die Akte bleibt vorausgewählt, auch wenn der Abbruch direkt nach dem Anlegen
    der Schnellakte lag. Ein bewusster Neustart ist möglich.
  - Ein nicht prüfbarer Besitzer bleibt unangetastet. **Zeit allein gibt nie frei.**
- **Audit:** Übernahme, Fortsetzung und kontrollierter Fehlschlag werden protokolliert, ohne Inhalte. Für die
  Übernahme im Chat (§60) ist der Eintrag nachgezogen.
- **Defekter Plan:** führt zum kontrollierten Fehlschlag statt zu wiederholten Server-Fehlern.

**QA P2 (Repository-Tests, SQLite, TestClient):**

| Prüfung | Status |
|---|---|
| `tests/test_schriftsatz_recovery.py` (13 Tests): harter Abbruch an 4 Upload-Speicherpunkten mit Wiederaufnahme ohne Doppelanlage (1 Akte, 1 Dokument, 1 Datei, 0 KI-Anfragen, Audit) | PASS |
| Abbruch nach der Cloud-Anfrage: genau 1 aufgezeichneter Request, Wiederholung ohne zweiten; Abbruch davor und direkt nach der Schnellakte (vorausgewählt, Neustart ohne zweite Akte) | PASS |
| Lebender Besitzer nach 5 h nicht übernommen; nicht prüfbarer Besitzer unangetastet; gelöschtes Dokument und defekter Plan führen zum kontrollierten Fehlschlag; Übernahme mit genau einem Gewinner; Migration auf und ab | PASS |
| §58-Tests: 3 Erwartungen auf das neue, nicht mehr zeitabhängige Verhalten umgestellt (Fortsetzung statt „bitte warten“; Doppelklick wartet auf das Ergebnis; „unterbrochen“ nur bei nicht prüfbarem Besitzer) | PASS |
| Unabhängiges Review: kein hoher Befund; mittel (Akte nicht vorausgewählt) und niedrig (defekter Plan, Audit, Testlücken) behoben | PASS |
| Gesamtsuite: 5246 bestanden, 4 fremde Logo-Fehler | PASS |
| Paralleles Übernahme-Rennen über HTTP für den Generator (nur auf Funktionsebene getestet; im Chat auch über HTTP) | NICHT VERIFIZIERT |
| PostgreSQL | NICHT VERIFIZIERT |

**Bewusst offen:**
- **Doppelte Zählung:** Ein Abbruch genau zwischen dem Speichern der Markierung und ihrem Merker zählt sie bei der
  Fortsetzung doppelt (Audit, Lern-Statistik). Die Entscheidung selbst ist ein Upsert. Den Merker vorher zu setzen
  könnte die Markierung verlieren, also weniger Schutz.
- **Hart gelöschte Objekte:** Ein zwischen Commit und Merker hart gelöschtes Objekt würde unter der geplanten ID neu
  angelegt. Akten und Dokumente werden regulär nur weich gelöscht.
- **Gesperrte Wiederholung:** Eine Wiederholung blockiert bis zu 10 s einen Worker-Thread.
- **Doppelklick bei Entwurfsaktionen:** „Neu generieren“, Änderungsauftrag und KI-Bearbeitung im Editor haben keine
  serverseitige Vorgangs-ID; dort schützt nur die UI-Sperre (`app_ai_loading`). Das war nicht Teil von P2 (Generator
  und Chat-Upload).

### 61.3 P3 – Mail und Intake

**Bestandsaufnahme (am Code):**
- **Mail-Abruf:** `main._run_periodic_mail_ingestion`, nur wenn ein Postfach konfiguriert ist. Ablauf: IMAP,
  `MailIngestionService` (Nachricht und Anhänge speichern), `DocumentProcessingService` (lokal), Zuordnung über
  `MatterMatchingService` und `MatterAssignmentService` (regelbasiert, lokal).
- **Ordner-Erfassung:** `IntakeService` und `IntakeWatcher`, Symlinks werden abgewiesen. Der Watcher wird in der
  laufenden App derzeit **nirgends gestartet** (kein `IntakeWatcher(` in `main`/`web`); nur die Fehler-Wiederholung
  (`errors/service.py`) liest erneut ein.
- **Spätere KI-Aktionen auf E-Mails:** `POST /dashboard/chat/from-message/{id}` verlangt eine Akte und läuft über die
  zentrale Prüfung samt Namensmarkierung (§54).
- **Ergebnis:** Kein Eingangsweg löst eine KI- oder Cloud-Anfrage aus. Eine Lücke wurde nicht gefunden, am App-Code
  ist nichts geändert.

**Ergänzt: `tests/test_no_ai_on_intake.py`** (dieselben Spione wie P1: alle Cloud-Provider, Gateway-Client, jede
Socket-Verbindung außer Loopback):
- echter Mail-Abrufzyklus (nur das Postfach ist ein Fake) mit synthetischer Mail und PDF-Anhang: gespeichert, lokal
  ausgelesen, zugeordnet, 0 Provider-Aufrufe, 0 Netz, 0 API-Logs;
- Ordner-Erfassung über den Watcher-Handler samt Verarbeitung: 0 Provider-Aufrufe, 0 Netz;
- KI-Aktion auf eine nicht zugeordnete Mail: 400, 0 Requests am Aufzeichnungsserver;
- bewusste KI-Aktion auf eine zugeordnete Mail mit Markierung: genau 1 Request; der markierte Name fehlt im
  verifizierten HTTP-Payload, Betrag und Datum bleiben erhalten.

**QA P3 (Repository-Tests, SQLite):**

| Prüfung | Status |
|---|---|
| Neue Tests sowie vorhandene Mail-, Watcher- und Mail-Service-Tests (20) | PASS |
| Gesamtsuite | nicht erneut ausgeführt, nur ein Testmodul ergänzt; letztes Gate 5246 PASS (§61.2) |
| Echter IMAP-Server, installierter Build | NICHT VERIFIZIERT |

**Offen bzw. fachlich:**
- Mail- und Intake-Dokumente ohne Akte sind erst nach der Zuordnung markierbar (§57.4).
- Die automatische Zuordnung setzt eine Klassifikation voraus, die keiner der Upload-Pfade aufruft (vorbestehend,
  dokumentiert in `main.py`). Das ist nicht datenschutzrelevant.

### 61.4 P4 – Datenschutzvalidierung (Benchmarks reproduziert)

Methodik wie in den Vorläufen: dieselben Skripte, dieselben eingefrorenen synthetischen Korpora und dieselbe
Produktionskonfiguration. Es gab keine Netz- oder Cloud-Aufrufe. Die eingecheckte
`benchmarks/pseudonymization_v1/results.json` blieb unverändert; sie stammt aus einem Stand vor §49.

| Benchmark | Baseline | Ist (17.10.2026, nach P1–P3) | Status |
|---|---|---|---|
| `pseudonymization_v1` (63 Feldfälle, 88 Gold-Werte) | §57-Lauf | **0 abweichende Kennzahlen.** 85 von 88 geschützt; Klartext-Lecks 3, Teil-Lecks 2, alle als bekannte Grenze markiert; Fehlalarme 6; juristische Werte 67/68 erhalten; Blockaden und Fehler 0 | PASS |
| dasselbe gegen die eingecheckte `results.json` | Stand vor §49 | Lecks 8 → 3: E-Mail, IBAN und zwei Personennamen behoben. Die E-Mail in K04 ist jetzt nur ein Teil-Leck | PASS (Verbesserung) |
| `bench_name_detection` (baseline, 1100 Namen) | §53 | 0 Lecks, 0 Blockaden, alle Gruppen identisch; Fehlalarme 2/132 („Partei“, „Verwalter“), auch schon in früheren Läufen | PASS |
| `bench_role_object_negatives` | §53 | 0/12 Sätze mit Verlust, 0 Personen-Platzhalter im Schriftsatz ohne Namen | PASS |
| `bench_chat_without_file` | §48 | 0/440 Lecks, 0 Blockaden, 0/14 Fachfragen verändert | PASS |
| Legal-E2E (`scripts.legal_bench.e2e`) | §56 | Nur Laufzeiten verändert (Bruchteile von Sekunden). Alle Qualitätskennzahlen identisch, etwa erfundene Fundstellen 6/6 markiert und keine Aufgabenfehler | PASS |

**Verbleibende Lecks (bekannte Grenzen, unverändert dokumentiert):**
- P10: Person, Rollenwort und Nachname ohne Anrede. Abgedeckt über die manuelle Markierung (§51), nicht automatisch.
- A06: Ortsname und Teil einer Kennung im Grundbuchtext.
- K04: Teil einer E-Mail mit OCR-Leerzeichen.
- I07: kurze Nummer ohne Bezeichnung.

**Ausgehende Payloads und Fail-Closed:** Vorhandene, weiter grüne Nachweise:
- `test_privacy_wire_payload`, `test_privacy_cloud_boundary_e2e` sowie die HTTP-Nachweise aus §57–§61 (P1, P3);
- `test_bench_pseudonymization::test_e2e_paths_protect_and_fail_closed`: Bei einem NER-Ausfall gibt es keinen
  Cloud-Aufruf, und ein Klarname aus der lokalen Vorabanalyse wird nicht gesendet.

**Neu erkannter Fehler im Regressionsschutz:** `test_no_plaintext_leak_outside_documented_limits` schloss Fälle mit
bekannter Grenze **vollständig** aus. Folgende Verschlechterungen wären nicht aufgefallen:
- ein zusätzliches Leck in A06, I07, K04 oder P10;
- mehr Teil-Lecks oder Fehlalarme;
- weniger erhaltene juristische Werte.

**Korrektur:** zwei Sperrklinken-Tests in `tests/test_bench_pseudonymization.py`:
- die zulässige Menge von Lecks und Teil-Lecks je Fall und Typ, jedes davon als Grenze dokumentiert;
- Fehlalarme ≤ 6, höchstens 1 nicht erhaltener juristischer Wert, 0 Blockaden und 0 Fehler.

Eine Mutationsprobe belegt die Wirkung: Ohne P10 in der Erlaubnisliste schlägt der Test fehl. Der Korpus selbst bleibt
eingefroren; neue synthetische Fälle wurden bewusst nicht ergänzt, weil kein neuer Fehler gefunden wurde.

**Status:** Die Ergebnisse gelten nur für endliche synthetische Korpora. Sie belegen **keine** vollständige
Leckfreiheit und keine Rechtskonformität. Die Validierung auf realitätsnäheren, nicht modellgenerierten Texten ist
**NICHT VERIFIZIERT**.

### 61.5 P5 – Gateway und Transport (lokal geprüft, keine Bereitstellung)

**Bestandsaufnahme (`gateway/`, ~800 Zeilen, 10 Testdateien, `deploy/` mit Caddy und systemd):**

| Bereich | Ist-Stand | Bewertung |
|---|---|---|
| Authentifizierung | `Bearer <client_id>:<secret>`, Argon2id, generische 401, Dummy-Verify gegen Timing-Enumeration, keine OpenAPI/Swagger | in Ordnung |
| Mandantentrennung | Relay ohne Zustand; Kanzlei-ID nur für Rate-Limit und Log; deaktivierte Kanzlei wird abgewiesen; keine kanzleiübergreifenden Daten | in Ordnung |
| Berechtigungen | Kanzlei-Credentials nur per Server-CLI (`tenant_admin`, Rotation, Widerruf) | in Ordnung |
| Größenlimits | `max_request_bytes` (200 KB) in der Route, aber **erst nach** vollständigem Einlesen und JSON-Parsen; Caddy `max_size 250KB` davor | **Lücke** (ohne Proxy Speicherrisiko) |
| Modellfreigaben | Allowlist, tatsächlich verwendetes Modell zentral (`default_model`), `max_tokens`-Obergrenze, `thinking` nur „disabled“, keine SDK-Wiederholungen, Ziel-URL fest | in Ordnung |
| Inhalte | `role` beliebig, `content` mit beliebigen Block-Typen (Bild, Dokument, `tool_use`) | **Lücke** (Missbrauch des zentralen Schlüssels bei kompromittierter Credential) |
| Protokollierung | typisierte Metadaten ohne Inhalt; Caddy-Access-Log ohne Body | in Ordnung |
| TLS | Caddy (Let's Encrypt), Gateway nur an `127.0.0.1`; Client erzwingt `https://` bei `APP_ENV != development`, die installierte App setzt `APP_ENV=production` | Konfiguration in Ordnung; **realer TLS-Betrieb NICHT VERIFIZIERT** |

**Behoben (nur Code, lokal):**
- **Nachrichtenschema** (`gateway/schemas.py`): nur Rollen `user` und `assistant`, nur Textblöcke ohne Zusatzschlüssel,
  `cache_control` nur „ephemeral“, System-Blöcke nur Text. Das entspricht genau dem, was der Lexono-Client sendet.
  Abweichungen werden mit 422 abgewiesen, ohne Anthropic zu erreichen.
- **Speicherschutz vor dem Parsen** (`gateway/main.py`, ASGI-Schranke `_BodySizeLimit`):
  - Ist `Content-Length` zu groß, folgt sofort 413.
  - Gestückelte Bodys werden höchstens bis zur Schranke gelesen (2 × `max_request_bytes`).
  - Die Schranke greift vor Authentifizierung und Parsen; die präzise, protokollierte 413 der Route bleibt.

**QA P5 (Repository-Tests mit Anthropic-Fake; kein Server, kein echter Schlüssel):**

| Prüfung | Status |
|---|---|
| `tests/test_gateway_hardening.py` (17): Bild, Dokument, `tool_use`, Zusatzschlüssel, falsches `cache_control`, fremde Rollen und System-Blöcke werden mit 422 abgewiesen, ohne Anthropic-Aufruf | PASS |
| Der echte Client-Payload (versiegelt, pseudonymisiert) läuft weiter durch; einfacher Text bleibt erlaubt | PASS |
| Größenschranke: Content-Length und gestückelt ergeben 413 ohne Auth und ohne Aufruf; die Routen-Prüfung bleibt; Mutationsprobe (Schranke entfernt, Tests schlagen fehl) | PASS |
| Vorhandene Gateway-Tests (93, inkl. E2E-Pilotszenario Client → Relay → Anthropic-Fake) | PASS |
| Gesamtsuite: 5269 bestanden, 4 fremde Logo-Fehler | PASS |
| Realer Server, TLS, Rate-Limit und Logging im Betrieb, echte Anthropic-Antwort | NICHT VERIFIZIERT |

**Offen (Betrieb, nicht ohne Freigabe umsetzbar):**
- **Rechenlast ohne gültige Kennung:** Jeder Request ohne gültige Credential kostet einen Argon2-Verify (absichtlich,
  gegen Enumeration). Ein vorgelagertes IP-Rate-Limit fehlt, die Abwehr läge bei Caddy oder fail2ban
  (Betriebsentscheidung).
- **Mehrere Instanzen:** Das Rate-Limit liegt im Prozess; mehrere Instanzen bräuchten einen geteilten Speicher.
- **Inhaltsprüfung im Relay:** Das Relay prüft weiterhin nicht, ob Inhalte pseudonymisiert sind; die Garantie liegt
  clientseitig (Siegel). Siehe Roadmap P1.
- **Kein Produktivnachweis:** Server, Domain, Zertifikat und echte Transportstrecke existieren nicht. Hetzner bzw. P0-08
  bleiben ein eigenes Arbeitspaket.

### 61.6 Gesamtstatus

**Ergebnisse je Paket:**

| Paket | Ergebnis | Commit |
|---|---|---|
| P1 Dokumentfreigabe | keine Umgehung; Routendurchlauf ohne KI und Netz als Dauertest; Export-Kopf korrigiert | `0382518` |
| P2 Idempotenz | Generator: Wiederaufnahme nach hartem Abbruch, Generierung nie wiederholt, keine Zeitfreigabe | `b5cab05` |
| P3 Mail/Intake | keine Lücke; Nachweis-Tests (Mail-Zyklus, Ordner, KI-Aktion nur mit Akte) | `aa487e3` |
| P4 Datenschutzvalidierung | Benchmarks ohne Abweichung reproduziert; Sperrklinke gegen Verschlechterung | `bfb7ff9` |
| P5 Gateway/Transport | Schema- und Größenhärtung lokal; Betrieb nicht verifiziert | dieser Commit |

**Art der Nachweise:** Alle Prüfungen sind Repository-Tests (SQLite, TestClient, lokale Aufzeichnungsserver, Fakes).
Browser-, installierte-App- und Server-Tests wurden in §61 **nicht** ausgeführt.

**Datenschutzstatus:** teilweise nachgewiesen. Keine Aussage über vollständige Leckfreiheit oder Rechtskonformität.

**Nächste Schritte (Vorschlag):**
1. Owner-Entscheidung: Pflicht-Freigabe je Dokument vor KI-Aktionen (§57.5).
2. Serverseitige Vorgangs-ID für Entwurfsaktionen (Neu generieren, KI-Bearbeitung).
3. Gateway-Bereitstellung als eigenes Paket mit Freigabe: IP-Rate-Limit, TLS- und Transportnachweis.
4. Validierung auf realitätsnäheren, nicht modellgenerierten Texten.

## §62 Konfigurierbare Dokumentfreigabe vor KI-Verarbeitung (17.10.2026, Branch `feature/feedback-diagnose`, Basis `022e636`)

Status: **teilweise nachgewiesen**. Zentrale Prüfung, Pseudonymisierung, Validierung und Fail-Closed sind in allen
Modi unverändert verpflichtend.

### 62.1 Modi, Geltungsbereich, Standard

- **A – Manuelle Freigabe:** Jedes Dokument braucht eine ausdrückliche Freigabe.
- **B – Automatische Freigabe:** Nach erfolgreicher zentraler Prüfung ohne gesonderte Freigabe. Unsichere Begriffe
  werden bei der Anfrage maskiert bzw. erfragt, wie bisher.
- **C – Automatische Freigabe mit Entscheidung bei Auffälligkeiten:** Unauffällige Dokumente gehen automatisch durch.
  Bei unsicheren Begriffen ist eine Entscheidung nötig: Freigabe oder Namensmarkierung.
- **Geltungsbereich:**
  - Der Modus gilt **kanzleiweit** (`firm_profiles.document_release_mode`).
  - Ändern dürfen ihn Admin und Anwalt (`PERM_PRIVACY_DECISION_MANAGE`), mit Audit-Eintrag.
  - Die Anzeigepräferenz „Automatisch freigegeben“ gilt **pro Benutzer** (`users.show_auto_release_notice`).
- **Standard ohne gesetzten Wert: B.** Das ist exakt das bisherige, geprüfte Verhalten. Bestehende Installationen
  ändern sich so nicht stillschweigend, und B ist nicht unsicher, weil die zentrale Prüfung bei jeder Anfrage
  fail-closed bleibt. Es ist aber die am wenigsten restriktive Variante. **Owner-Entscheidung offen:** Soll für
  Neuinstallationen C der Standard sein?

### 62.2 Durchsetzung (serverseitig)

- **Prüfung je Dokumentstand:** Die zentrale Prüfung (`ClaudePrivacyGateway.prepare_request`, Modus „ask“) läuft
  lokal auf dem Dokumenttext, mit Beteiligten und Entscheidungen wie beim Versand. Ergebnisse: unauffällig, auffällig
  oder fehlgeschlagen.
- **Zwischenspeicher:** Ergebnisse werden gespeichert nach Textstand, Kontext (Entscheidungen, Beteiligte) und
  Code-Version. Nur echte Ergebnisse werden gespeichert; „fehlgeschlagen“ wird jedes Mal neu geprüft. Geschrieben wird
  in einer eigenen kurzen Sitzung.
- **Manuelle Freigabe:** Sie ist an den angezeigten Textstand gebunden. Ein veralteter Stand wird abgewiesen, und eine
  Textänderung macht die Freigabe wirkungslos.
- **Durchsetzung an der einzigen Engstelle** (`_build_sachverhalt`): Dokumenttext nicht freigegebener Dokumente geht
  in keinem Pfad hinaus, ebenso wenig Frist-Ausschnitte daraus. Der Anwalt bekommt einen lokalen Hinweis.
- **Ausdrücklich angefragte Dokumente** (Chat-Upload, „Mit Dokument chatten“): Es gibt eine lokale, verständliche
  Meldung, keinen Cloud-Aufruf.
- **Fehlgeschlagene Prüfung:** Sie blockiert in jedem Modus, auch eine bestehende manuelle Freigabe. Die Freigabe ist
  dann serverseitig gesperrt.
- **Laufzeit:** Im Modus B wird nicht zusätzlich geprüft, denn eine fehlgeschlagene Prüfung blockiert die ganze
  Anfrage ohnehin. Die Anzeige prüft immer vollständig.

### 62.3 Oberfläche (dezent)

- **Seite „Datenschutz & Dokumentfreigabe“** (`/dashboard/privacy/document-release`): aktiver Modus, Moduswahl und
  Anzeigepräferenz. Eine Karte im Einstellungs-Tab „KI & Datenschutz“ zeigt den Modus dauerhaft.
- **Am Dokument** (Akten- und Chat-Dokumentansicht): Statuszeile („Automatisch freigegeben“, „Manuell freigegeben“,
  „Freigabe erforderlich“, „Entscheidung erforderlich“, „Verarbeitung blockiert – Datenschutzprüfung fehlgeschlagen“)
  samt Modus.
- **Erfolgsmeldung bei automatischer Freigabe:** mit „Pseudonymisierung überprüfen“, „Freigabeeinstellungen öffnen“ und
  „Diese Meldung nicht mehr anzeigen“. Ausblendbar ist nur diese Meldung; Blockade, erforderliche Freigabe und
  Entscheidung erscheinen immer.
- **Human in the Loop:** Die Seite „Pseudonymisierung überprüfen“ zeigt lokal Platzhalter, Kategorie und Originalwert
  sowie den pseudonymisierten Text. Feedback läuft über die bestehenden Wege: Namen schützen (§56),
  Entscheidungsverwaltung (§52), Qualitätsmeldung (§75). Keine Rückmeldung lockert Regeln automatisch.

### 62.4 Tests und QA

| Prüfung | Art | Status |
|---|---|---|
| `tests/test_document_release.py` (34) | Repository, SQLite, echte Endpunkte, lokaler Gateway | PASS |
| … darin: alle drei Modi; Bindung an den Textstand (Textänderung macht die Freigabe veraltet, veraltetes Formular wird abgewiesen) | Repository | PASS |
| … darin: Akte und Fristen ohne nicht freigegebenen Text; Markierung löst Auffälligkeit; fehlgeschlagene Prüfung in jedem Modus, nicht übersteuerbar, Fehler fail-closed und nicht gespeichert, vorübergehender Fehler wird neu geprüft | Repository | PASS |
| … darin: Upload und Öffnen ohne KI in allen Modi (0 Requests am Aufzeichnungsserver); Chat-Frage zu nicht freigegebenem Upload lokal beantwortet, nach Freigabe genau 1 verifizierter Request | Repository, HTTP-Recorder | PASS |
| … darin: Anzeigepräferenz dauerhaft gespeichert und änderbar, ändert nichts sonst; Warnungen nie ausblendbar; interner Rücksprung; Rechte (Mitarbeiter sieht, ändert und gibt nicht frei); Aktenisolation; Audit; Prüfseite; Migration `schritt3_036` | Repository | PASS |
| GET-Routendurchlauf (P1) inkl. neuer Seiten: 0 Provider-Aufrufe, 0 Netz, keine Server-Fehler | Repository | PASS |
| Browser-QA (Edge headless, isolierter Server, temporäre DB, tote Cloud-Endpunkte) | Browser | PASS |
| … darin: Einstellungsseite; Status und Meldung am Dokument; Ausblenden per Checkbox (DB gespeichert, Status bleibt); Moduswechsel per Formular; Freigabe per Klick; Prüfseite; Einstellungskarte; 0 API-Logs, keine JS-Fehler | Browser | PASS |
| Unabhängiges Review: kein Bypass (eigene Probe: weder Text noch Frist noch Dateiname eines nicht freigegebenen Dokuments im Payload); mittlere Befunde behoben (vorübergehender Fehler, Commit im Kontextaufbau, Laufzeit, Code-Version im Schlüssel), niedrige behoben (Anzeige/Durchsetzung konsistent, Audit ohne Änderung, Fristen ohne Text, Chat ohne Akte) | Review | PASS |
| Gesamtsuite: 5298 bestanden, 4 fremde Logo-Fehler | Repository | PASS |
| Installierte App (WebView2, echte Datenbank) | App | NICHT VERIFIZIERT |

### 62.5 Offene Risiken

- **„Kein personenbezogenes Datum“ in Modus C:** Diese Entscheidung ist nur unterhaltungsbezogen und löst die
  Auffälligkeit nicht. Mitarbeiter brauchen dann eine Freigabe durch einen Anwalt (fachliche Entscheidung).
- **Laufzeit in Modus C:** Nach jeder Änderung von Entscheidungen oder Beteiligten wird die Akte beim nächsten Versand
  synchron neu geprüft (bis zu 30 Dokumente, lokal). Eine Vorab-Prüfung im Hintergrund fehlt noch.
- **Erstes Öffnen:** Das erste Öffnen eines Dokuments führt in jedem Modus einmal die lokale Prüfung aus (Anzeige).
  Die Prüfseite rechnet ohne Zwischenspeicher.
- **Obergrenze vor dem Filter:** Die Obergrenze von 30 Dokumenten im Kontext greift vor dem Freigabefilter. Viele
  neue, nicht freigegebene Dokumente können ältere freigegebene verdrängen; ein Hinweis erscheint. **Behoben in §63.3.**
- **E-Mail-Texte:** Die Freigabe betrifft Dokumente (`documents`). E-Mail-Texte selbst laufen weiter über die
  zentrale Prüfung der jeweiligen Aktion, Anhänge sind Dokumente.
- **Kein Beweis:** Keine Aussage über vollständige Leckfreiheit oder Rechtskonformität.


## §63 Dokumentfreigabe: Modus C als Standard, Vorab-Prüfung, mehr als 30 Dokumente (17.10.2026, Branch `feature/feedback-diagnose`, Basis `cb38afb`)

Status: **teilweise nachgewiesen**. Zentrale Prüfung, Pseudonymisierung, Validierung und Fail-Closed sind unverändert
verpflichtend; nichts davon wurde gelockert. Die offene Owner-Frage aus §62.1 ist durch den Auftrag §63 entschieden:
**C ist Standard für Neuinstallationen.**

### 63.1 P1 – Modus C für Neuinstallationen, Bestand unverändert

- **Erkennung einer Neuinstallation:** Migration `schritt3_037`. Bei der Ersteinrichtung laufen die Migrationen
  **vor** dem Anlegen des ersten Benutzers (`app/setup/wizard.py`: `run_migrations` → `create_admin`; die App migriert
  bei jedem Start). Als Neuinstallation gilt nur eine **wirklich leere** Datenbank: keine Benutzer, kein
  Kanzleiprofil, keine Mandanten, Akten oder Dokumente. Dann wird ein Kanzleiprofil mit `auto_review` (C) angelegt
  (übrige Werte = Modell-Standards, wie beim bedarfsweisen Anlegen).
- **Bestehende Installationen:** Ein Profil **ohne** Wert wird ausdrücklich auf `auto` (B, bisheriges Verhalten)
  gesetzt. Ein gesetzter Wert (A, B oder C) wird **nie** überschrieben. Ohne Profil wird nichts angelegt; das Profil
  entsteht wie bisher bedarfsweise, ohne Wert = B.
- **Code-Standard bleibt B** (`DEFAULT_MODE`): Ein leerer Wert wird nie stillschweigend strenger oder lockerer.
  `NEW_INSTALLATION_MODE = auto_review` dokumentiert den Standard; die Einstellungsseite zeigt „Standard für
  Neuinstallationen“ bzw. „Standard bestehender Installationen“. Der Modus bleibt änderbar (Admin/Anwalt, Audit).
- **Wiederholte Starts, Restore, Downgrade/Re-Upgrade:** Keine Änderung, kein zweites Profil. Downgrade ändert keine
  Daten (leer und `auto` bedeuten dasselbe).
- **Grenze:** Mit `create_all` angelegte Entwicklungs-/Testdatenbanken haben keinen Wert und laufen damit in B. In
  `app/` gibt es keinen solchen Pfad.

### 63.2 P2 – Vorab-Prüfung in Modus C

- `release.precheck_document` läuft am Ende von `DocumentProcessingService.process_document`. Das ist der gemeinsame
  Punkt aller Eingangswege (Upload, Import, Chat-/Schriftsatz-Upload).
- Nur in Modus C, nur mit Akte und verwertbarem Text. **Lokal**: dieselbe zentrale Prüfung (`prepare_request`, Modus
  „ask“), kein KI-, Cloud- oder Netzaufruf.
- **Bindung:** Ergebnis gespeichert je Textstand, Prüfkontext (Beteiligte, aktenweite Entscheidungen) und Version des
  Datenschutz-Codes. Ändert sich eines davon, prüft der Versand neu.
- **Fehler und Abbruch:** werden nicht gespeichert und unterbrechen die Verarbeitung nie. Bis zu einer erfolgreichen
  Prüfung ist das Dokument **blockiert** (fail-closed). „Fehlgeschlagen“ wird nie zwischengespeichert.
- **Keine Schreibzugriffe auf die Sitzung des Aufrufers** (Review §63): Modus ohne bedarfsweises Anlegen des Profils
  gelesen, kein Autoflush; das Ergebnis schreibt eine eigene kurze Sitzung.
- **Grenzen:**
  - Mail-Anhänge haben bei der Verarbeitung noch keine Akte. Sie werden bei Anzeige oder Versand geprüft.
  - Die Vorab-Prüfung läuft synchron im Upload. Der erste Upload nach dem Start (Kaltstart der lokalen Erkennung)
    und große Texte dauern Sekunden.
  - Verbindlich ist immer die Prüfung beim Versand; die Vorab-Prüfung ist nur ein Vorwärmen.

### 63.3 P3 – mehr als 30 Dokumente

- Die Höchstzahl (30) gilt jetzt für **freigegebene** Dokumente. Vorher wurden die 30 neuesten geladen und erst danach
  gefiltert, sodass nicht freigegebene neue Dokumente ältere freigegebene verdrängten.
- `_select_released_documents`: neueste zuerst (`created_at desc, id`), blockweise je 30, nur Dokumente mit
  verwertbarem Text. Die Suche ist auf **120 Dokumente** begrenzt, damit viele offene Dokumente keine unbegrenzte
  lokale Prüfung auslösen. Der Prüfkontext wird je Akte einmal für alle Blöcke berechnet.
- **Nie verwendet:** nicht freigegebene und nicht durchsuchte Dokumente. **Präzisiert in §66.2:** gilt für Volltexte; Fristauszüge freigegebener Dokumente laufen über einen eigenen, ebenfalls freigabegeprüften Pfad.
- **Hinweise:**
  - zurückgehaltene Dokumente (bis zu 10 Namen, „und N weitere“);
  - „Höchstzahl von 30 Dokumenten erreicht: N weitere …“;
  - „Suchgrenze erreicht: N weitere … nicht auf Freigabe geprüft“, wenn die Suche vor 30 Freigaben endet.
  - Die Hinweise bleiben lokal und gehen nicht in die Cloud-Anfrage.
- **Grenzen:**
  - Blättern ohne Snapshot: Ein gleichzeitig neu angelegtes Dokument kann doppelt erscheinen; Inhalte werden
    dedupliziert, schlimmstenfalls ein doppelter Name im Hinweis. Ein gleichzeitig gelöschtes Dokument kann
    übersprungen werden; das ist fail-closed.
  - Modus B: bis zu 30 Dokumente mit Text; vorher nur die mit Text unter den 30 neuesten.
  - Eine neue Entscheidung oder ein neuer Beteiligter macht die gespeicherten Prüfungen der Akte ungültig. Der
    nächste Versand prüft dann bis zu 120 Texte lokal neu.

### 63.4 P4 – fachliche Grenzen

- **„Kein personenbezogenes Datum“ gilt nur für sein Gespräch bzw. seinen Kontext.** Die Dokumentprüfung lädt nur
  aktenweite Entscheidungen (`conversation_id=None`). Eine Entscheidung in einem Gespräch gibt kein Dokument frei und
  gilt nicht kanzleiweit. Es gibt keine globale Ausnahme.
- Die Namensmarkierung („personenbezogen“) verschärft den Schutz nur, sie lockert ihn nie.
- **Mitarbeitende** haben weder `PERM_CLAUDE_CALL` noch `PERM_PRIVACY_DECISION_MANAGE`. Sie können also nicht
  freigeben, nicht über Chat, „Aus Dokument“ oder die Prüfseite versenden und keine Entscheidung treffen (jeweils 403,
  keine Freigabe, kein Payload). Eine erforderliche Anwaltsfreigabe ist damit nicht zu umgehen.

### 63.5 Tests

| Ebene | Inhalt | Ergebnis |
|---|---|---|
| Repository (pytest) | `tests/test_document_release_defaults.py` (23): Neuinstallation C, Bestand unverändert (leer/A/B/C), ohne Profil, Altdaten ohne Benutzer, Anzeige; Vorab-Prüfung lokal und versionsgebunden, nicht in B, kein Profil angelegt, Fehler nicht vertraut, Textänderung; Upload ohne KI-Aufruf; >30 Dokumente gemischt, Hinweise, Suchgrenze; Gesprächsentscheidung; Mitarbeitende; alle Schriftsatz-, Chat- und Mailpfade | PASS |
| Repository | verwandte Suiten (Freigabe, Migrationen, keine KI beim Öffnen/Upload, Drafting) | PASS |
| Repository | vollständige Suite (6 Blöcke; Block 6 nach Speicherabbruch in drei Teilen im Vordergrund) | PASS (5321 Tests), außer den 4 bekannten fremden Logo-Tests (`test_design_refresh`) |
| Sandbox | Migration auf temporärer DB: upgrade → downgrade → upgrade, genau 1 Profil `auto_review` (Review-Agent) | PASS |
| Browser | Einstellungsseite: Hinweis „Standard für Neuinstallationen“ (TestClient-HTML) | PASS (nur gerendertes HTML, keine Browser-Sitzung) |
| Installierte App / WebView2 / Installer | Neuinstallation mit Modus C, Upgrade einer echten Installation | **NICHT VERIFIZIERT** |
| PostgreSQL | Migration und Auswahl | **NICHT VERIFIZIERT** |

**Kein Beweis:** Keine Aussage über vollständige Leckfreiheit oder Rechtskonformität.


## §64 Vorab-Prüfung im Hintergrund, Dokumentauswahl, Datenschutzvalidierung (17.10.2026, Branch `feature/feedback-diagnose`, Basis `4b7387d`)

Status: **teilweise nachgewiesen**. Zentrale Prüfung, Pseudonymisierung, Validierung und Fail-Closed sind unverändert
verpflichtend; nichts davon wurde gelockert.

### 64.1 P1 – Vorab-Prüfung in Modus C

**Nachgewiesene Lücken (§63-Stand):**
- Mail-Anhänge wurden nie vorab geprüft. Bei der Verarbeitung haben sie noch keine Akte; die Zuordnung (automatisch
  oder „Übernehmen“) folgt danach.
- Die Prüfung lief synchron im Upload (Kaltstart der lokalen Erkennung, große Texte: Sekunden).
- Eine aktenweite Entscheidung, ein Beteiligter oder ein geänderter Mandantenname macht alle gespeicherten Prüfungen
  der Akte ungültig. Der nächste Versand prüfte dann bis zu 120 Texte synchron neu.

**Änderung:** `app/documents/precheck_queue.py`.
- Ein Session-Listener merkt sich nach jedem Flush die betroffenen Dokumente und Akten:
  - Dokumente mit geändertem Text oder geänderter Akte;
  - Akten mit neuen, geänderten oder gelöschten Beteiligten;
  - Akten mit aktenweiten Entscheidungen (gesprächsbezogene Entscheidungen ändern den Prüfkontext nicht);
  - Akten eines Mandanten mit geändertem Namen bzw. Akten mit geändertem Mandanten.
- Erst **nach dem Commit** prüft ein einzelner lokaler Hintergrund-Thread diese Dokumente mit derselben zentralen
  Prüfung (`check_document`), in eigener Sitzung. Bei einer Akte sind es höchstens die 120 neuesten Dokumente mit Text,
  wie die Suchgrenze der Auswahl.
- Ein Rollback verwirft die gesammelten Aufträge. Die Warteschlange ist begrenzt (1000) und dedupliziert.
- Der Modus wird bei jedem Auftrag frisch gelesen, ohne das Kanzleiprofil anzulegen. Vorab geprüft wird nur in Modus C.
- Der synchrone Aufruf in `process_document` entfällt. Der Upload wartet nicht mehr.
- **Sicherheit:**
  - Die Vorab-Prüfung ist nur ein Vorwärmen. Verbindlich bleibt die Prüfung beim Versand
    (`released_document_ids` → `processing_state`). Sie prüft selbst, wenn kein passendes Ergebnis vorliegt.
  - Ein laufender, abgebrochener oder fehlgeschlagener Auftrag bewirkt daher **nie** eine Freigabe.
  - Das Ergebnis ist an Textstand, Prüfkontext und Datenschutz-Codeversion gebunden: Der Schlüssel entsteht aus den
    Daten, die der Worker selbst gelesen hat. Ein veralteter Stand erzeugt nur einen Eintrag, den der Versand nie trifft.
  - „Fehlgeschlagen“ wird nie gespeichert.
  - Kein KI-, Cloud- oder Netzaufruf. Im Log steht nur der Fehlertyp, kein Inhalt.
- **Nach dem Review behoben:**
  - Ein Fehler in den Hooks erreicht den Aufrufer nicht mehr. Ein erfolgreicher Commit sieht also nie wie ein Fehler
    aus, und ein Flush wird nie abgebrochen.
  - Das spaCy-Modell wird unter einer Sperre geladen (`presidio_ner._get_analyzer_engine`): Worker und Request laden
    es nicht gleichzeitig doppelt.
- **Grenzen:**
  - Ohne eigene Verbindung (SQLite im Arbeitsspeicher, nur in Tests) läuft die Prüfung direkt im Commit-Hook.
  - Nicht erfasst; es fehlt dann nur die Vorwärmung, der Versand prüft trotzdem:
    - neu angelegte Dokumente, die schon Text und Akte haben (z. B. Generator synthetischer Daten);
    - der alte Wert beim Aktenwechsel eines Beteiligten;
    - Bulk-Änderungen ohne ORM.
  - Ein Savepoint-Rollback verwirft alle gesammelten Aufträge der Sitzung, auch hier fehlt nur die Vorwärmung.
  - Worker und Versand können denselben Text parallel prüfen: doppelte Rechenarbeit, kein Sicherheitsproblem.
  - PostgreSQL: Der Worker hielte während der Erkennung eine Lesetransaktion offen. **NICHT VERIFIZIERT.**
  - Globale Datenschutz-Einstellungen außerhalb des Prüfkontexts machen gespeicherte Ergebnisse nicht ungültig
    (bestehende Grenze seit §62).

### 64.2 P2 – Auswahl bei mehr als 30 bzw. 120 Dokumenten

Geprüft: Die Auswahl aus §63.3 erfüllt die Vorgaben, **keine Code-Lücke gefunden**. Neue Regressionstests
(`tests/test_document_selection_limits.py`):
- 160 Dokumente (10 älteste freigegeben): nichts verwendet, „Suchgrenze erreicht: 40 weitere“;
- 135 Dokumente (100 neuere offen): genau die 20 durchsuchten freigegebenen, „15 weitere“;
- genau 30 freigegebene: kein Höchstzahl-Hinweis;
- Modus C gemischt (unauffällig, auffällig, auffällig und manuell freigegeben, fehlgeschlagen trotz Freigabe): nur
  unauffällige und manuell freigegebene Dokumente;
- veraltete manuelle Freigabe, veraltete gespeicherte Prüfung, älterer Prüfkontext: nicht verwendet;
- Hinweise erscheinen **nicht** in der tatsächlich ausgehenden Anfrage (Loopback-Mitschnitt) und nicht im
  Schriftsatz-Payload.

### 64.3 P3 – Datenschutzvalidierung

**Inventar:**

| Benchmark | Korpus | Regressionsschutz vor §64 |
|---|---|---|
| `pseudonymization_v1` (`scripts/bench_pseudonymization.py`) | 63 synthetische Feldfälle | Sperrklinke (§61.4) |
| `legal_e2e_v1` (`scripts/legal_bench`) | 7 synthetische Akten (dev 4, test 3) | nur Lecks, Fristen und Chronologie; Fehlalarme, Werte- und Normenerhalt **nur gemessen** |
| `bench_name_detection` | 1100 Namen | `test_privacy_name_second_signal` |
| `bench_role_object_negatives` | 12 Sätze und 1 Schriftsatz | **keiner** |
| `bench_chat_without_file` | 440 Namen-Kontexte, 14 Fachfragen, 100 Fachbegriffe | **keiner** |
| `bench_realistic_documents` | 1 Protokoll als DOCX, PDF und Scan | `test_realistic_documents` |
| `presidio_audit_corpus` | eingefroren | `test_privacy_presidio_audit` |

**Reproduktion (gleiche Methodik, keine eingecheckte Ergebnisdatei überschrieben):**
- `pseudonymization_v1`: identisch mit §61.4 (85/88 geschützt, Lecks 3, Teil-Lecks 2, Fehlalarme 6, juristische
  Werte 67/68, Blockaden 0, Fehler 0).
- `bench_role_object_negatives`: 0/12. `bench_chat_without_file`: 0/440 Lecks, 0 Blockaden, 0/14 Fachfragen; zusätzlich
  gemessen: 20/100 Fachbegriffe in mindestens einem Kontext maskiert.
- `legal_e2e_v1` (alle 7 Fälle) gegen die eingecheckte `results.json` (`e13bffe`, 14.10.2026, gleicher Korpus-Hash):
  - Lecks 0, Werte, Normen und Chronologie vollständig erhalten.
  - Fristen ohne Referenz: dev 28 → 1, test 18 → 0 (§40).
  - Klassifikation: dev 11 → 8 von 20, test 2 → 5 von 13.
  - **Fehlalarme: dev 37 → 38, test 26 → 28** („Erbschein“ als Person, „-0533“ als Kennung, „7“ als Adresse). Seitdem
    gab es mehrere bewusste Verschärfungen in `app/privacy` (§45–§54); die Einzelursache ist nicht nachgewiesen. Es
    handelt sich um Übermaskierung, kein Leck. Der Anstieg blieb unbemerkt, weil er nicht begrenzt war.

**Korrektur des Regressionsschutzes:**
- `tests/test_legal_benchmark_e2e.py`: Erlaubt sind genau die gemessenen Fehlalarme. Werte, Normen und Chronologie
  müssen vollständig erhalten bleiben, Widersprüche mindestens 1.
- `tests/test_privacy_benchmark_ratchets.py`: beide Skripte vollständig, ohne Teilmenge (Lecks 0, Blockaden 0,
  Fachfragen unverändert, keine neu maskierten Fachbegriffe).
- Bekannte Grenzfälle bleiben enthalten.
- Mutationsprobe: Fehlt ein bekannter Eintrag in der Erlaubnisliste, schlägt der jeweilige Test fehl.

**Ausgehende Payloads und Fail-Closed:** `test_privacy_wire_payload`, `test_privacy_cloud_boundary_e2e`,
`test_bench_pseudonymization` (NER-Ausfall: kein Cloud-Aufruf) und der neue Loopback-Nachweis aus 64.2: PASS.

**Realitätsnähere, nicht modellgenerierte Texte: BLOCKIERT (Owner-Entscheidung).**
- Das Quellenregister (`benchmarks/legal_e2e_v1/source_register.json`) verlangt vor jeder Nutzung einer öffentlichen
  Quelle die Einstufung „nutzbar“ mit Prüfdatum und Begründung, ausdrücklich durch eine verantwortliche Person, „nicht
  der Agent“. Der Validator erzwingt das.
- Alle 7 Einträge stehen auf `nicht_geprueft`: Gesetze im Internet, Rechtsprechung im Internet, BVerfG, openJur, ZJS,
  Vis Moot, Soldan Moot.
- Es wurden deshalb keine fremden Texte übernommen. Alle Ergebnisse gelten weiter nur für synthetische,
  modellgeschriebene Korpora.

### 64.4 Tests und Prüfebenen

| Ebene | Inhalt | Ergebnis |
|---|---|---|
| Repository | `test_document_precheck_queue` (14), `test_document_selection_limits` (9), `test_privacy_benchmark_ratchets` (2), Sperrklinke in `test_legal_benchmark_e2e` | PASS |
| Repository | volle Suite (6 Blöcke, Block 6 in 3 Teilen, im Vordergrund) | 5347 PASS; 4 FAIL = bekannte fremde Logo-Tests (`test_design_refresh`) |
| Sandbox | Benchmarks reproduziert (s. 64.3), Ausgaben nur in temporären Dateien | PASS |
| Browser | keine Browser-Sitzung | **NICHT VERIFIZIERT** |
| Installierte App / WebView2 | Installierter Build vom 15.10.2026 (Migrationen bis `schritt3_029`; Repository: `schritt3_037`). §62–§64 sind dort **nicht enthalten**, die App lief nicht. Kein Test, keine Neuinstallation. | **NICHT VERIFIZIERT** |
| PostgreSQL, echte Transportstrecke | – | **NICHT VERIFIZIERT** |

**Testprozesse:** Vor den großen Läufen liefen keine Python-Prozesse. Danach sind keine verwaisten Prozesse übrig.
7,4 GB frei.

**Kein Beweis:** Keine Aussage über vollständige Leckfreiheit oder Rechtskonformität.


## §65 Installer aus 07d5fe0, Update, WebView2-Prüfung, Datenschutzkorpus mit Quellenbezug (17.10.2026, Branch `feature/feedback-diagnose`, Basis `07d5fe0`)

Status: **teilweise nachgewiesen**. Zentrale Prüfung, Pseudonymisierung, Validierung und Fail-Closed sind unverändert;
in §65 wurde kein App-Code geändert.

### 65.1 Build

- **Quelle:** separater `git worktree` auf genau `07d5fe0` (`C:\Users\Bonit\lexono_build_07d5fe0`). Der Working Tree mit
  den fremden Logo-/UX-Änderungen wurde nicht gebaut und nicht verändert. Einzige nicht versionierte Eingabe:
  `windows/vendor/` (Tesseract, WebView2-Bootstrapper), unverändert kopiert.
- **Befehle:** `pyinstaller windows\lexono.spec --distpath dist --workpath build --noconfirm` (Exit 0), dann
  `ISCC.exe windows\installer.iss` (Exit 0, 298 s). Ergebnis: `dist\installer\Lexono_Setup.exe` (556,6 MB).
- **Inhalt geprüft:**
  - Migrationen `schritt3_030` bis `schritt3_037` im Bundle;
  - Templates `document_release.html` und `document_pseudonymization.html`;
  - Module `app.documents.precheck_queue`, `app.documents.release`, `app.web.document_release_router` und
    `app.models.document_release` im PYZ-Archiv;
  - Marker aus §63/§64 im ausgelieferten Bytecode („lexono-document-precheck“, `_build_analyzer_engine`, „Suchgrenze
    erreicht“).
- **Ergebnis:** PASS.

### 65.2 Installation (Update der bestehenden Installation)

**Vorab geklärt:**
- **Programmordner:** `%LOCALAPPDATA%\Lexono` (Build vom 15.10.2026, Migrationen bis `schritt3_029`).
- **Datenverzeichnis:** `%PROGRAMDATA%\Lexono`. Der Installer verändert es nicht: kein `[UninstallDelete]`, und
  `[InstallDelete]` betrifft nur den Programmordner.
- **Echte Datenbank** (`schritt3_029`; 86 Mandanten, 136 Akten, 142 Dokumente; nur Zählungen gelesen): Sie wird erst
  beim nächsten normalen Start migriert. Dabei legt Lexono vorher automatisch ein geprüftes ZIP-Backup an
  (`app/backup/pre_migration.py`).
- **Kein Autostart:** keine Run-Keys, kein Autostart-Ordner, keine geplante Aufgabe. Lexono lief nicht.
- **Probe der Migration 029 → 037 auf einer Kopie** (SQLite-Backup-API, Original nur lesend, Kopie danach gelöscht):
  - alle 42 Tabellen erhalten, keine Zeilenzahl verändert, 6 neue Tabellen;
  - Freigabemodus danach `auto` (B, bisheriges Verhalten);
  - `integrity_check` ok;
  - 9 Fremdschlüssel-Verstöße, identisch schon im Original (keine Folge der Migration).
- **Sicherung des bisherigen Programmordners:** `C:\Users\Bonit\lexono_install_backup_vor_07d5fe0` (989 Dateien,
  1218 MB).

**Update:**
- `Lexono_Setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG` → Exit 0. Der stille Modus startet die App nicht.
- WebView2-Bootstrapper: Exit `0x80040C28`. Die Runtime war bereits installiert (154.0.4258.53).

**Danach:**
- installierte `Lexono.exe` byte-gleich mit dem Build;
- Migrationen bis `schritt3_037` vorhanden;
- echte Datenbank unverändert (gleicher Hash, `schritt3_029`).

**Ergebnis:** PASS. Die Migration der echten Datenbank ist **NICHT VERIFIZIERT**: Sie erfolgt erst beim nächsten
normalen Start, mit automatischem Backup.

### 65.3 WebView2-Prüfung der installierten Anwendung

**Aufbau:**
- Die installierte `Lexono.exe` (Build `07d5fe0`) läuft gegen ein **isoliertes** Datenverzeichnis
  (`LEXONO_DATA_DIR=C:\Users\Bonit\lexono_qa_s65`), nicht gegen die echte Datenbank.
- Die Einrichtung läuft über die Befehle der App selbst (`migrate`, `create-admin`), wie bei einer Neuinstallation.
- Kein Schlüssel; tote Cloud-Endpunkte (`https://127.0.0.1:9`); lokale KI und Mail-Abruf aus.
- Gesteuert wird das echte WebView2-Fenster (Edg/155.0.4283.60) über CDP
  (`WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port`).
- Nachweise (Screenshots, `ergebnis*.json`) liegen unter `C:\Users\Bonit\lexono_qa_s65\qa_nachweise`.
- Danach wurde das Fenster regulär geschlossen; Prozess und Ports sind beendet. `llama-server` lief nicht.
- **Echte Schutzprüfung bestätigt:** Ein `http://`-Gateway-Endpunkt wird in Produktion abgelehnt.

| Prüfung im echten Fenster | Ergebnis |
|---|---|
| Neuinstallation: Migration bis `schritt3_037`, Modus `auto_review` (C); Einstellungsseite „(Standard für Neuinstallationen)“ (§63 P1) | PASS |
| Erstanmeldung erzwingt Passwortänderung, danach Neuanmeldung | PASS |
| Mandant und Akte über die echten Formulare angelegt | PASS |
| Upload zweier Dokumente (0,7 s): Vorab-Prüfung im Hintergrund, unauffällig `passed`, auffällig `uncertain` (§64 P1) | PASS |
| Anzeige: „Automatisch freigegeben“ bzw. „Entscheidung erforderlich – Auffälligkeit bei der Datenschutzprüfung“ (§62) | PASS |
| Neuer Beteiligter über das Formular: neue Vorab-Prüfung im neuen Prüfkontext (2 → 4 Ergebnisse, 2 Kontexte) (§64 P1) | PASS |
| KI-Aktion „Dokument analysieren“ auf das auffällige Dokument: lokaler Hinweis „… nicht an die Cloud-KI gesendet …“, `api_call_logs` = 0 (§62) | PASS |
| Mehrfach-Upload von 33 Dokumenten (1,5 s): alle 35 Dokumente vorab geprüft (§64 P1) | PASS |
| Moduswechsel A über das Formular: „Freigabe erforderlich“, Rückwechsel C, 2 Audit-Einträge (§62) | PASS |
| Auswahl bei mehr als 30 Dokumenten im Chat-/Schriftsatz-Payload (§63/§64 P2): braucht eine Cloud-Anfrage, ohne Cloud nicht sichtbar | **NICHT VERIFIZIERT** (nur Repository) |
| Migration und Verhalten mit der echten Datenbank, Installer-Erstinstallation auf frischem Rechner, PostgreSQL, echte Transportstrecke | **NICHT VERIFIZIERT** |

### 65.4 Quellen für die Datenschutzvalidierung

- **Freigabe:** Die verantwortliche Person hat zwei Quellengruppen freigegeben, „Gesetze im Internet“ und
  „Rechtsprechung im Internet“ (Owner-Auftrag §65). Andere Quellen bleiben gesperrt (`nicht_geprueft`, unbenutzt).
- **Register** (`benchmarks/legal_e2e_v1/source_register.json`): Status `nutzbar` mit Prüfdatum und Begründung.
  Neu dokumentiert sind Abruf (Datum, Seiten, Einzelabruf statt Massenabruf), Verwendungszweck, rechtliche Grundlage
  (§ 5 Abs. 1 UrhG) und redaktionelle Bestandteile.
- **Nutzungsbedingungen** (im Original gelesen, 17.10.2026):
  - Keine einschränkende Bedingung. Bei „Gesetze im Internet“ sind Deep Links ausdrücklich erlaubt; „Rechtsprechung
    im Internet“ stellt ein XML-Format „für die Nutzer …, die die … Entscheidungen über automatisierte Verfahren
    weiterverarbeiten möchten“ bereit.
  - Haftungsausschluss und „keine Rechtsberatung“ sind vermerkt.
- **Redaktionelle Bestandteile, gesondert geprüft und nicht als amtliches Werk behandelt:**
  - `<titelzeile>` und `<norm>` (Dokumentation);
  - `<sonstosatz>` („Hinweis der Dokumentationsstelle“);
  - juris-Zusätze wie „(juris: SGB 3)“;
  - Datenbankstruktur (möglicher Datenbankschutz nach §§ 87a ff. UrhG): genutzt wurden nur Einzeldokumente.
- **Verwendet:**
  - 5 Entscheidungen: BGH VIII ZR 274/23, VIII ZR 18/24, VI ZR 431/24 sowie BAG 6 AZR 32/24, 2 AZR 72/24;
    übernommen werden amtliche Leitsätze, die Rechtsfrage und das Ergebnis;
  - 6 Normtexte im Wortlaut: §§ 469, 543, 573, 573c, 577 BGB und § 174 InsO.

### 65.5 Korpus `privacy_sources_v1` und Regressionsschutz

- **Korpus** (`benchmarks/privacy_sources_v1/cases.json`):
  - 5 Problemstellungen, jeweils mit ECLI, Abrufdatum und **dokumentiertem erwartetem Ergebnis** (aus dem amtlichen
    Leitsatz bzw. Tenor);
  - 11 vollständig synthetische Dokumente: Kündigung, Aktenvermerk, Mandanten-Mail, Ausübung des Vorkaufsrechts,
    Prüfvermerk und Tabelle zur Sammelanmeldung, Gesprächsnotiz, Chat-Auftrag, Datenschutzauskunft, Vermerk zur
    Erfolgsaussicht, Fristenvermerk.
  - Alle Personen, Anschriften, Konten, Rufnummern, E-Mail-Adressen, Geburtsdaten und Kennungen sind erfunden.
  - Aus den Entscheidungen werden keine Angaben realer Verfahrensbeteiligter übernommen, auch keine Daten und Orte
    des Ausgangsfalls.
  - Die Erzähltexte sind synthetisch (modellgeschrieben); wortgetreu nicht modellgeneriert sind nur Normtexte und
    Leitsätze.
- **Runner** `scripts/bench_privacy_sources.py`:
  - dieselben Prüf- und Bewertungsfunktionen wie `pseudonymization_v1`;
  - zusätzlich eine Korpusvalidierung: jeder Wert steht im Text, nur freigegebene Quellen;
  - Chronologie: Daten in derselben Reihenfolge wie im Dokument;
  - Fail-Closed bei NER-Ausfall (Pseudonymisierung und Restrisiko-Scan).
  - Die Zahlen sind **nicht** mit `pseudonymization_v1` vergleichbar (anderer Korpus).
- **Ergebnis 17.10.2026** (`results.json`, deterministisch in 2 Läufen):

| Kennzahl | Wert |
|---|---|
| geschützte Werte | 52/52, **0 Lecks, 0 Teil-Lecks** |
| Blockaden, Fehler | 0, 0 |
| Chronologie erhalten | 11/11 |
| Fail-Closed bei NER-Ausfall | 22/22 ohne Payload |
| juristische Werte erhalten | **101/116** |
| Maskierungen ohne Referenz | 34 |
| tatsächlich ausgehende Anfrage (Loopback, Q09) | kein geschützter Wert, Daten und Fachbegriffe erhalten |

- **Befund (neu, Grenze):** Die verlorenen Werte sind vor allem
  - Aktenzeichen **realer** BGH-/BAG-Entscheidungen in Zitaten, als Kennung maskiert (z. B. „VIII ZR 274/23“): Die
    zitierte Rechtsprechung ist im Payload nicht mehr identifizierbar;
  - Gerichtsnamen wie „Amtsgericht Kiel“ oder „Landgericht Osnabrück“, als Ort maskiert;
  - „Buchst.“ in „Art. 6 Abs. 1 Unterabs. 1 Buchst. f DSGVO“, als Person maskiert.
  - Ein Teil der Maskierungen ohne Referenz ist dagegen bewusster Schutz (bekannte Mandantin, Anschrift der
    Hausverwaltung).
- **Owner-Entscheidung offen:** Sollen Aktenzeichen oberster Bundesgerichte im Zitatkontext unmaskiert bleiben? Das
  wäre eine Lockerung der Erkennung und braucht eine eigene Abgrenzung zu den Aktenzeichen der eigenen Verfahren
  (z. B. „14 C 233/26“). Sie wurde **nicht** vorgenommen.
- **Regressionsschutz** (`tests/test_bench_privacy_sources.py`, 8 Tests):
  - Korpus und Quellen gültig; der Validator weist erfundene Werte und gesperrte Quellen ab;
  - 0 Lecks;
  - Chronologie vollständig, Fail-Closed;
  - Sperrklinke auf die bekannten Verluste und Maskierungen: jede neue schlägt fehl;
  - Normtexte bleiben erhalten;
  - Loopback-Nachweis auf der Leitung.
- `test_legal_benchmark_corpus`: Der Test „nutzbar ohne Prüfdatum wird abgelehnt“ wählt jetzt ausdrücklich einen
  gesperrten Eintrag. Die Prüfung bleibt unverändert.

### 65.6 Tests und Prüfebenen

| Ebene | Inhalt | Ergebnis |
|---|---|---|
| Repository | `test_bench_privacy_sources` (8), `test_legal_benchmark_corpus`, `test_legal_benchmark_e2e`, `test_bench_pseudonymization`, `test_privacy_wire_payload`, `test_privacy_benchmark_ratchets` | PASS (56) |
| Repository | volle Suite: in §65 kein App-Code geändert, letzter voller Lauf §64 (5347 PASS, 4 fremde Logo-Tests FAIL) | nicht erneut ausgeführt |
| Sandbox | Migrationsprobe auf einer Kopie der echten Datenbank, Benchmarks | PASS |
| Build | Worktree `07d5fe0`, PyInstaller und Inno Setup | PASS |
| Installation | Update der bestehenden Installation, echte Datenbank unverändert | PASS |
| WebView2 (installierte App, isolierte Daten) | s. 65.3 | PASS (Ausnahmen NICHT VERIFIZIERT) |

**Kein Beweis:** Keine Aussage über vollständige Leckfreiheit oder Rechtskonformität.


## §66 Dokumentauswahl in der installierten App, Übermaskierung juristischer Werte, Migration/Update (17.10.2026, Branch `feature/feedback-diagnose`, Basis `dd3b24f`)

Status: **teilweise nachgewiesen**. Zentrale Prüfung, Pseudonymisierung, Validierung und Fail-Closed sind unverändert; es
gab keine Lockerung außerhalb der bestehenden, geprüften §33-Regel.

### 66.1 Ausgangslage Build

- Der installierte Build stammte aus `07d5fe0`.
- Zwischen `07d5fe0` und `dd3b24f` gibt es **keinen** Unterschied in `app/`, `run.py`, `migrations/`, `windows/`,
  `alembic.ini` oder `Start.vbs`: Der Build entsprach `dd3b24f` im App-Code.
- Nach den §66-Änderungen wurde aus Commit **`44f3df7`** neu gebaut (Worktree, PyInstaller und Inno Setup jeweils
  Exit 0). Im ausgelieferten Code stehen die §66-Marker (`_with_context_notices`, `context_notices`, ausgeschriebene
  Monatsnamen, „nicht als Volltext“).
- Stilles Update: Exit 0, `Lexono.exe` byte-gleich mit dem Build, echte Datenbank unverändert (Hash vorher = nachher).
- Der vorige Installer ist archiviert: `C:\Users\Bonit\lexono_installer_archiv\Lexono_Setup_07d5fe0.exe`.

### 66.2 P1 – Dokumentauswahl in der installierten App (WebView2)

**Aufbau:**
- Die installierte `Lexono.exe` läuft gegen die isolierte QA-Instanz `C:\Users\Bonit\lexono_qa_s65`; das echte
  WebView2-Fenster wird über CDP gesteuert.
- Akten werden über die echten Formulare angelegt, Dokumente über den echten Mehrfach-Upload hochgeladen, Fragen über
  den echten Chat-Composer gestellt.
- Die **tatsächlich ausgehende** Anfrage geht an einen lokalen Aufzeichnungsserver (`ANTHROPIC_BASE_URL` auf
  127.0.0.1 mit `LEXONO_QA_LOOPBACK_CLOUD_ENDPOINT=1`, gefälschter Schlüssel, Gateway-Zeile der QA-`.env`
  deaktiviert; das Original liegt als `qa_nachweise\s66\env_vor_s66.txt`).
- Kein Anthropic-Aufruf, kein Gateway, keine Serverkosten. Alle 4 Anfragen gingen an `/v1/messages` des
  Aufzeichnungsservers, nur mit dem gefälschten Schlüssel.
- Nachweise (Screenshots, alle Anfragen als JSON, `ergebnis.json`): `qa_nachweise\s66\lauf2` und
  `qa_nachweise\s66\lauf3_build_44f3df7`.

**Lauf 1 – ungültig, kein Befund gegen Lexono:**
- `?matter=` öffnete das zuletzt aktive Gespräch, die Fragen liefen deshalb in einer anderen Akte. Richtig ist
  `?new=1&matter=`.
- Die Testtexte enthielten kennungsartige Marker („S1-F001“). Die Prüfung fragt bei solchen Zeichenfolgen nach, was
  im Repository reproduziert und vorsichtiges Verhalten ist.

**Lauf 2 (Build `07d5fe0`) – Befunde:**
- **Lücke:** Der Chat zeigte die Auswahlhinweise (Höchstzahl, Suchgrenze, nicht freigegebene Dokumente) **nicht** an
  – weder im Streaming- noch im normalen Pfad. `DraftingResult.open_review_points` wurde vom Chat nie übernommen.
- **Ungenau dokumentiert:** Fristauszüge („Argumentationspunkte“) freigegebener Dokumente gehen auch für Dokumente
  jenseits der Höchstzahl bzw. Suchgrenze hinaus. Das ist ein eigener Pfad mit eigener Freigabeprüfung
  (`_build_argumentationspunkte` → `released_document_ids`), also zulässig. Die Aussage in §63.3 „nicht durchsuchte
  Dokumente werden nie verwendet“ gilt aber nur für Volltexte.

**Korrektur (Commit `44f3df7`):**
- Die Hinweise der Kontextauswahl werden als eigenes Feld `DraftingResult.context_notices` (über
  `_PreparedRequest.context_notices`) bis in den Chat gereicht. Dort stehen sie im lokalen Block „Offene Prüfpunkte /
  Hinweise“, außerhalb des kopierbaren Texts.
- Der Gesprächsverlauf entfernt diesen Block vor jeder weiteren Cloud-Anfrage (bestehend, `_build_history`).
- Die Hinweistexte sind präzisiert: „nicht als Volltext berücksichtigt … erkannte Fristhinweise freigegebener
  Dokumente bleiben enthalten“.

**Lauf 3 (installierter Build `44f3df7`):**

| Fall | Erwartung | Tatsächliche ausgehende Anfrage | Anzeige in der App | Ergebnis |
|---|---|---|---|---|
| S1: 34 freigegebene ältere + 8 auffällige neuere (Modus C, alle 42 vorab geprüft: 34 passed, 8 uncertain) | 30 neueste freigegebene als Volltext; keine auffälligen | Sachverhalt: genau 30 Dokumente; 0 Sperrbeträge; Fristauszüge aus allen 34 freigegebenen; kein Hinweistext | Hinweisblock: 8 Dateinamen „nicht freigegeben“ + „Höchstzahl von 30 … 4 weitere … nicht als Volltext“ | **PASS** |
| S2: 3 freigegebene ältere + 125 auffällige neuere (>120 durchsucht) | kein Volltext; Suchgrenze benannt | Sachverhalt: 0 Dokumente; 0 Sperrbeträge; 3 Fristauszüge aus den freigegebenen (eigener, freigabegeprüfter Pfad); kein Hinweistext | „… und 110 weitere“ + „Suchgrenze erreicht: 8 weitere …“ | **PASS** |
| S3: auffälliges Dokument („Richter“), danach Beteiligter „Richter“ über das Formular (Kontextänderung) | vorher zurückgehalten; danach neue Prüfung im neuen Kontext, Name maskiert | vorher: Dokument nicht enthalten; danach Prüfungen `uncertain` → `passed` (2 Kontexte), Dokument enthalten, „Richter“ nicht im Klartext | vorher Hinweis „nicht freigegeben“ | **PASS** |
| Hinweise/Statusmeldungen im Payload (alle 4 Anfragen) | nie | keine der Phrasen „Nicht berücksichtigt“, „Höchstzahl“, „Suchgrenze“, „Freigabemodus“, „Entscheidung erforderlich“, „Automatisch freigegeben“, „.txt“ | – | **PASS** |
| Textänderung eines Dokuments | neue Prüfung, keine veraltete Freigabe | über die Oberfläche nicht auslösbar (kein Textbearbeiten) | – | **NICHT VERIFIZIERT** in der App (Repository-Tests: PASS) |

**Beobachtungen ohne Änderung:**
- Die Seitenleiste zeigt „Cloud-KI (Gateway) Bereit“, obwohl die direkte Strecke zum lokalen Aufzeichnungsserver
  aktiv war. Die Statusanzeige unterscheidet Gateway und Direktstrecke nicht.
- Im Payload wurden „Freigabevermerk“ (Dokumenttext) und „Suchgrenze“ (Aktentitel) als Person maskiert: weitere
  Übermaskierung, kein Leck.

### 66.3 P2 – Übermaskierung der 15 nicht erhaltenen Werte (`privacy_sources_v1`)

| Fall | Wert | Erwartet | Tatsächliche Maskierung | Ursache | Ergebnis |
|---|---|---|---|---|---|
| Q05 | BAG, Urteil vom 20. Februar 2025 – 6 AZR 32/24 | lesbar (veröffentlichte Entscheidung) | `6 AZR 32/24` → Aktenzeichen | §33-Zitierform kannte nur numerische Daten | **behoben** |
| Q07 | BAG, Urteil vom 3. April 2025 – 2 AZR 72/24 | lesbar | Aktenzeichen | wie Q05 | **behoben** |
| Q10 | BGH, Urteil vom 14. Oktober 2025 – VI ZR 431/24 | lesbar | Aktenzeichen | wie Q05 | **behoben** |
| Q02 | BGH, Beschluss vom 2. Dezember 2025 – VIII ZR 274/23 | lesbar | Aktenzeichen | Datumsform; dazu „Mandantin“ im 80-Zeichen-Fenster → Schutz eigener Verfahren (§33) | **bleibt maskiert** (bewusst) |
| Q04 | BGH, Urteil vom 8. Oktober 2025 – VIII ZR 18/24 | lesbar | Aktenzeichen | Datumsform; „unseren Mandanten“ im Fenster danach (§33) | **bleibt maskiert** (bewusst) |
| Q08 | 2 AZR 72/24 (ohne Gericht/Datum) | lesbar | Aktenzeichen | arabische Kurzform ohne Datum – nach Review §33 bewusst maskiert | **bleibt maskiert** |
| Q09, Q10 | Art. 6 Abs. 1 Unterabs. 1 Buchst. f DSGVO | lesbar | „Buchst“ → Person (NER) | Abkürzung fehlte in `_LEGAL_ABBREVIATIONS` | **behoben** („Unterabs.“/„Buchst.“ wie „Abs.“, nur mit Punkt) |
| Q02 | Amtsgericht Köpenick | lesbar | → Ort | Gerichtsname als Ortsangabe | **unverändert** (Owner-Entscheidung) |
| Q05 | Amtsgericht Kiel | lesbar | → Ort | wie oben | **unverändert** |
| Q10 | Landgericht Osnabrück | lesbar | → Ort | wie oben | **unverändert** |
| Q07 | Arbeitsgericht Frankfurt am Main | lesbar | „Frankfurt am Main“, „am Main“ → Ort | wie oben | **unverändert** |
| Q02 | Räumungsklage | lesbar | „Räumungsklage Wohnbau Lindenhof GmbH ./.“ → Organisation | NER-Spanne zu weit | **unverändert** |
| Q04 | personenidentisch | lesbar | „veräußernden Gesellschaft personenidentisch“ → Organisation | NER-Spanne zu weit | **unverändert** |
| Q03 | 2019 | lesbar | „Haus im Jahr 2019“ → Ort | NER-Spanne zu weit | **unverändert** |

**Lösung, eng begrenzt (`app/privacy/detectors.py`):**
- Die bestehende §33-Ausnahme (nur Bundesgerichte, nur Bundes-Aktenzeichen, nur die kanonische Zitierform „<Gericht>,
  Urt./Beschl. v. <Datum> – <Az>“, nie bei Hinweis auf ein eigenes Verfahren) erkennt das Datum jetzt auch mit
  **ausgeschriebenem Monat**.
- „Unterabs.“ und „Buchst.“ gelten wie „Abs.“ nur mit Punkt als Abkürzung.
- Nicht erweitert: Kurzformen, untere Instanzen, eigene Aktenzeichen (z. B. „14 C 233/26“ bleibt maskiert), Fenster
  und Liste der Hinweise auf eigene Verfahren.
- **Nicht umgesetzt:**
  - Gerichtsnamen: eine neue Lockerung der Ortserkennung; der Gerichtssitz kann den Wohnort eingrenzen. Bleibt
    **Owner-Entscheidung**.
  - NER-Spannen: keine sichere enge Regel.

**Absicherung:**
- `test_privacy_identity_genitive_citations`: Zitate mit ausgeschriebenem Monat bleiben lesbar.
- Eigenes Verfahren mit ausgeschriebenem Monat, untere Instanz, eigenes Aktenzeichen und Kurzform bleiben maskiert.
- Ein Name neben „Buchst.“ wird weiter maskiert.

**Messung `privacy_sources_v1`** (gleiche Methodik):

| Kennzahl | vorher → nachher |
|---|---|
| geschützte Werte / Lecks | 52/52, 0 → 52/52, **0** |
| erhaltene juristische Werte | 101/116 → **106/116** |
| neue Maskierungen | **0** |
| Chronologie / Fail-Closed / Blockaden | 11/11, 22/22, 0 → unverändert |

- Die Sperrklinke ist nachgezogen: Die behobenen Werte sind als „bleibt erhalten“ festgeschrieben, die verbleibenden
  10 bleiben im Regressionsschutz.
- Die übrigen Datenschutz-Benchmarks liegen in der vollen Suite und sind grün: `pseudonymization_v1`-Sperrklinke,
  Legal-E2E-Sperrklinke, Chat ohne Akte, Rollenwort-Sätze.

### 66.4 P3 – Datenbank, Backup, Migration, Wiederherstellung

**QA-App:**
- Datenbank: `C:\Users\Bonit\lexono_qa_s65\data\kanzlei_ai.db`, gewählt über `LEXONO_DATA_DIR` →
  `.env` `DATABASE_URL`.
- Stand `schritt3_037`, neu angelegt durch die installierte App (`migrate`, `create-admin`).
- Es gibt keinen Backup-Ordner: Bei einer neuen, leeren Datenbank und ohne ausstehende Migration legt Lexono
  bestimmungsgemäß kein Backup an.
- Der Build `44f3df7` bringt keine neue Migration mit.

**Echte Datenbank:**
- `C:\ProgramData\Lexono\data\kanzlei_ai.db`, Stand `schritt3_029`, in §65 und §66 **unverändert** (gleicher Hash
  vor und nach beiden Updates).
- Daneben liegen ältere Handsicherungen `kanzlei_ai.db.bak_pre_schritt3_019` bis `…_022` vom 03.10.2026.

**Ablauf beim nächsten normalen Start** (`run.py` `cmd_serve` → `cmd_migrate` bei jedem Start):
1. `pending_revisions` liest den Stand, nur lesend.
2. Eine Datenbank aus einer neueren Programmversion wird abgewiesen (Downgrade-Schutz: keine Sicherung, keine
   Migration, kein Start).
3. Steht etwas aus (hier 029 → 037), legt `create_pre_migration_backup` über die SQLite-Backup-API eine konsistente
   Kopie als ZIP an: `data\backups\vor-update_schritt3_029_auf_schritt3_037_<Zeitstempel>.zip`. Das ZIP wird geprüft
   (`testzip`). Schlägt das fehl, gibt es **keine Migration** (fail-closed).
4. Dann folgt `alembic upgrade head`. Die Probe auf einer Kopie der echten Datenbank (§65.2) ergab: alle 42
   Tabellen und Zeilenzahlen erhalten, 6 neue Tabellen, Modus explizit B, `integrity_check` ok.

**Wiederherstellung:**
- Nur offline: `Lexono.exe restore --archive <ZIP>` (`app/backup/restore_service.py`), bei gestoppter App.
- Vorher sichert Lexono die aktuelle Datei als `*.pre-restore-<Zeitstempel>.bak`.
- Abgedeckt durch die Repository-Tests `test_update_hardening`, `test_restore_service` und `test_restore_backup_cli`
  (PASS, volle Suite).
- **Fremdschlüssel:** Die 9 Verstöße (`chat_message_documents`, `drafts`, `draft_feedback`, `outbox_entries`,
  `documents`) bestehen **bereits im Original**; die Probe-Migration hat sie weder verursacht noch verändert.
- **NICHT VERIFIZIERT** (keine Freigabe erteilt): echte Migration, echtes Backup und Wiederherstellung der echten
  Datenbank; Wiederherstellung in der installierten App.

### 66.5 Tests und Prüfebenen

| Ebene | Inhalt | Ergebnis |
|---|---|---|
| Installierte App (WebView2, isolierte QA-Daten, Loopback-Payload) | Lauf 3, s. 66.2 | PASS (Textänderung NICHT VERIFIZIERT) |
| Build/Installation | Build `44f3df7`, Update, echte Datenbank unverändert | PASS |
| Repository | `test_document_selection_limits` (+1: Hinweise im Chat lokal, Folgefrage ohne Hinweise), `test_privacy_identity_genitive_citations` (+10), `test_bench_privacy_sources` (+1) | PASS |
| Repository | volle Suite (6 Blöcke im Vordergrund, Block 6 in 3 Teilen) | 5367 PASS; 4 FAIL = bekannte fremde Logo-Tests |
| Sandbox | `privacy_sources_v1` neu gemessen, Ursachendiagnose | PASS |
| Browser (ohne App) | – | nicht durchgeführt |
| PostgreSQL, echte Transportstrecke | – | **NICHT VERIFIZIERT** |

**Testprozesse:**
- Vor den Läufen gab es keine alten Prozesse.
- Jede QA-Instanz wurde nach Identitätsprüfung (PID-Datei, Pfad, `serve`) über `CloseMainWindow` regulär beendet.
- Danach lief kein Python-, Lexono- oder llama-Prozess mehr; frei sind 7,4 GB.
- QA-Daten, Programmsicherung und Installer-Archiv sind erhalten.

**Kein Beweis:** Keine Aussage über vollständige Leckfreiheit oder Rechtskonformität.


## §67 Öffentliche Gerichts- und Justizbezeichnungen als Institution (17.10.2026, Branch `feature/feedback-diagnose`, Basis `da33cf4`)

Status: **teilweise nachgewiesen**. Owner-Entscheidung: Öffentliche Gerichtsbezeichnungen bleiben lesbar. Gericht,
Gerichtsbarkeit und Instanz sind für die rechtliche Einordnung nötig. Fail-Closed, Restrisiko-Scan und Leak-Prüfungen
sind unverändert.

### 67.1 Ursache (Erkennungskette)

1. **Erkennung:** Die Namenserkennung (spaCy/Presidio, `presidio_ner.detect_presidio_entities`) liefert Gerichte als
   Ort-Treffer – gemessen für:
   - `ort: 'Amtsgericht Kiel'`, `ort: 'Landgericht Osnabrück'`, `ort: 'Amtsgerichts Köpenick'`;
   - `ort: 'Arbeitsgericht Berlin'`, `ort: 'Oberlandesgericht Schleswig'`;
   - `ort: 'Bundesgerichtshof'`, `ort: 'Bundesarbeitsgericht'`;
   - bei „Arbeitsgericht Frankfurt am Main“ und „Staatsanwaltschaft Kiel“ nur den Sitz (`ort: 'Frankfurt am Main'`,
     `ort: 'Kiel'`).
2. **Klassifikation:** In `detectors.detect_all` gab es keine Institutionsklasse für Justizbehörden. Ein Ort-Treffer
   wurde wie jede Ortsangabe behandelt.
3. **Pseudonymisierung:** Daraus entstand `[ORT_xx]`, etwa „Klage beim [ORT_01]“.
4. **Validierung:** Der Restrisiko-Scan (`security_check`) nutzt dieselbe Funktion `detect_all` und hätte eine
   unmaskierte Gerichtsbezeichnung als Rest-Ort gemeldet. Eine Regel muss deshalb in `detect_all` greifen, nicht nur in
   der Pseudonymisierung.
5. **Nicht verursacht von §67:**
   - Der Testvorspann „Akte: Beispielakte“ wird vom Restrisiko-Scan als Ort gemeldet und blockiert manche Sätze
     fail-closed, mit und ohne Gerichtsregel gemessen.
   - „Richter“ wird im Kontext mitunter als Person maskiert.

### 67.2 Geänderte Klassifikation (`app/privacy/detectors.py`, `_keep_public_court_names`)

Die Regel sitzt in `detect_all` direkt nach `_handle_citations` und wirkt damit gleichermaßen in Pseudonymisierung und
Restrisiko-Scan.

**Klassifikationsregel** (kein Katalog einzelner Orte):

| Bestandteil | Ausprägung |
|---|---|
| Gerichtsart | Amts-, Land-, Oberlandes-, Arbeits-, Landesarbeits-, Sozial-, Landessozial-, Verwaltungs-, Oberverwaltungs-, Finanz-, Familien-, Insolvenzgericht, Verwaltungsgerichtshof, Kammergericht |
| Bundesgerichte | Bundesgerichtshof, Bundesarbeits-, Bundessozial-, Bundesverwaltungs-, Bundesverfassungs-, Bundespatentgericht, Bundesfinanzhof |
| Justizbehörden mit amtlicher Namensbildung | Staatsanwaltschaft, Generalstaatsanwaltschaft, Amtsanwaltschaft |
| Grammatik | jeweils auch im Genitiv |
| Sitz (optional) | großgeschriebene Wörter, Bindestrich, „am/an der/in der/im/ob der/vor der/bei“, Klammerzusatz, I–IV (z. B. „München I“) |

**Wirkung:**
- Lesbar bleibt nur ein **Ort- oder Organisationstreffer**, der **genau** „Institution [+ Sitz]“ ist, oder **genau**
  der Sitz unmittelbar nach einer Institutionsbezeichnung.

**Schutzgrenzen:**
- Personen-, Anschrifts-, Kontakt-, IBAN-, Kennungs- und Aktenzeichentreffer bleiben unberührt. Sie werden auch direkt
  neben dem Gericht maskiert.
- **Mehrdeutigkeit:** Steht ein Wort des Sitzes in einem anderen geschützten Treffer desselben Texts (Personenname,
  Anschrift wie „24103 Kiel“), bleibt die Bezeichnung maskiert.
- Ist der Ort an anderer Stelle als Ort erkannt („wohnt in Kiel“), maskiert die Wiederholungsregel ihn auch im
  Gerichtsnamen („Amtsgericht [ORT_01]“).
- **Nicht erfasst:**
  - andere Institutionen (Finanzamt, Stadtwerke);
  - Sitze, die die Namenserkennung als Person meldet (z. B. „Landgericht [PERSON_01]“, „des Amtsgerichts Köpenick“ in
    einem Fall) – das bleibt Übermaskierung, kein Leck;
  - als **Beteiligter der Akte** erfasste Gerichte (bekannte Entität `gericht` → `[GERICHT_01]`). Das ist eine
    ausdrückliche Akteneinstellung und bleibt unverändert.
- **Unverändert:**
  - die §33/§66-Zitatregel, also nicht erweitert;
  - eigene Aktenzeichen, Aktenzeichen unterer Instanzen und Kurzformen bleiben maskiert;
  - Fail-Closed bei NER-Ausfall.

### 67.3 Tests

`tests/test_privacy_court_names.py` (32 Tests, in zwei Läufen identisch):

| Prüfung | Ergebnis |
|---|---|
| Gerichtsbezeichnungen lesbar: Amtsgericht Kiel, Landgericht Berlin, Bundesgerichtshof, Bundesarbeitsgericht, Arbeitsgericht Berlin, Arbeitsgericht Frankfurt am Main, Landgericht München I, Landgericht Osnabrück, Amtsgerichts Kiel, OLG Schleswig, LAG Berlin-Brandenburg, Staatsanwaltschaft Kiel, Generalstaatsanwaltschaft Schleswig | PASS |
| Dieselben Orte als gewöhnliche Ortsangabe bleiben maskiert (Kiel, Osnabrück, Köpenick) | PASS |
| Wohnort im selben Text → Sitz auch im Gerichts- bzw. Behördennamen maskiert | PASS |
| Sitz in der Anschrift („24103 Kiel“) bzw. als Personenname („Amtsgericht Weber“) → Gericht maskiert | PASS |
| Gericht + eigenes Aktenzeichen („14 C 233/26“, „27 O 118/26“), eigenes BAG-/BGH-Verfahren („2 AZR 72/24“, „VIII ZR 274/23“) → Aktenzeichen maskiert | PASS |
| Gericht + Name, Telefon, E-Mail, IBAN, Geburtsdatum → nur das Gericht lesbar | PASS |
| Mandanten- und Verfahrenskontext (Mandant, Gegner-GmbH, Vertreter, Aktenzeichen) → nichts weiter freigegeben | PASS |
| Stadtwerke/Finanzamt nicht ausgenommen; als Person erkannter Sitz bleibt maskiert | PASS |
| NER-Ausfall mit Gerichtsnamen → kein Payload (Fail-Closed) | PASS |
| Bestehend: Entscheidungszitate, untere Instanzen, Q02/Q04, `test_privacy_identity_genitive_citations` | PASS |
| Volle Suite (6 Blöcke im Vordergrund, Block 6 in 3 Teilen) | 5399 PASS; 4 FAIL = bekannte fremde Logo-Tests |

### 67.4 Benchmark `privacy_sources_v1` (gleiche Methodik wie §66)

| Kennzahl | §66 | §67 |
|---|---|---|
| **Lecks** (geschützte Werte im Payload) | 0 (52/52 geschützt) | **0** (52/52), Teil-Lecks 0 |
| erhaltene juristische Werte | 106/116 | **109/116** |
| **Übermaskierung**: verlorene juristische Werte | 10 | 7 |
| **Übermaskierung**: Maskierungen ohne Referenz | 29 | 26 |
| neue Maskierungen / neu verlorene Werte | – | 0 / 0 |
| Chronologie, Fail-Closed, Blockaden | 11/11, 22/22, 0 | unverändert |

- **Wieder erhalten:** „Amtsgericht Köpenick“ (Q02), „Amtsgericht Kiel“ (Q05), „Landgericht Osnabrück“ (Q10).
- **Bewusst weiter maskiert:**
  - „Arbeitsgericht Frankfurt am Main“ (Q07): Der Sitz ist dort auch die Wohnanschrift der Mandantin.
  - Q02/Q04: Entscheidungszitate mit Hinweis auf eigene Mandanten im Umfeld.
  - Q08: arabische Kurzform.
  - 3 zu weite NER-Spannen.
- Die Sperrklinke ist nachgezogen (`FIXED_IN_S66` um die drei Gerichte ergänzt, 109/116 festgeschrieben).
- Ein besserer Wert ist **kein Sicherheitsnachweis**. Er gilt nur für diesen endlichen synthetischen Korpus.

### 67.5 Nicht verifiziert

- Installierte App: Der Build `44f3df7` enthält §67 nicht, die App wurde nicht neu gebaut. **NICHT VERIFIZIERT** in
  der App, nur Repository.
- Kleinschreibung, OCR-Varianten und Abkürzungen von Gerichtsnamen („AG Kiel“, „LG Berlin“) sind nicht erfasst und
  bleiben wie bisher.
- Reale Texte, PostgreSQL, echte Transportstrecke: **NICHT VERIFIZIERT**.

**Kein Beweis:** Keine Aussage über vollständige Leckfreiheit oder Rechtskonformität.


## §69 Fristauszüge und System-Typbezeichnungen (Befunde F4/F6 aus dem App-Test §68) (17.10.2026, Basis `e8d0a0f`)

Status: **teilweise nachgewiesen**. Die §67-Gerichtsregel ist nicht erweitert. Pseudonymisierung, Leak-Checks und
Fail-Closed sind unverändert.

### 69.1 F4 – Gerichtsname im Fristauszug maskiert

**Weg des Textes:**
1. Dokumenttext.
2. `PlaceholderDeadlineExtractor._context_window`: 60 Zeichen um das Datum, eingerastet auf die nächste Leerstelle.
3. `Deadline.source_text` (gespeichert).
4. `RuleBasedLocalAIProvider._build_argumentationspunkte` („Möglicher Termin … :: <Auszug>“).
5. Gateway: `detect_all` mit der Wiederholungsregel `_extend_with_repeated_occurrences`.

**Ursache:**
- Die Fenstergrenze fiel zwischen „Amtsgericht“ und „Kiel“, der Auszug begann deshalb mit „Kiel, Az. …“.
- Ohne Gerichtsart ist „Kiel“ ein gewöhnlicher Ort-Treffer; die §67-Regel greift richtigerweise nicht.
- Die Wiederholungsregel maskierte den Wert daraufhin an jeder Stelle, auch im Sachverhalt („Amtsgericht [ORT_01]“).
- Übermaskierung, kein Leck.

**Änderung (Herkunft belegt):**
- `app/deadlines/extractor.py`:
  - `institution_prefix`: Steht im **Originaltext** unmittelbar vor dem Fenster genau eine Gerichts- oder
    Justizbezeichnung (dieselbe Klassifikation wie §67, `detectors.COURT_TYPE_BEFORE_SPACE`), gehört sie zum Auszug.
  - `with_institution_prefix`: ergänzt **bereits gespeicherte** Auszüge nur, wenn der Auszug im Volltext des
    Quelldokuments **eindeutig** auffindbar ist und dort unmittelbar eine Gerichtsart davor steht.
  - In allen anderen Fällen bleibt der Auszug unverändert: kein Volltext, nicht auffindbar, mehrdeutig, kein Gericht
    davor.
- `app/ai_providers/local_ai_provider.py`: Die Argumentationspunkte nutzen `with_institution_prefix`.
- Ein isoliertes „Kiel“ löst nie eine Freigabe aus. Es wird nur das Wort aus dem Originaltext übernommen.

### 69.2 F6 – Blockade bei Gericht + Anschrift

**Reproduktion:**
- Synthetisch, lokal, über den echten Gateway.
- Mit und ohne §67-Regel identisch blockiert: „Nach Pseudonymisierung weiterhin erkennbare Muster: ['person']“.

**Verantwortlicher Prüfschritt:** Restrisiko-Scan (`security_check`), Restbefund `person: 'Gerichtliches'`.

**Ursache:**
- Der Kontextaufbau setzt vor jeden Dokumenttext die von Lexono vergebene Typbezeichnung, etwa
  `[Gerichtliches Schreiben] …`.
- Die Namenserkennung las „Gerichtliches“ uneinheitlich als Person: in der Pseudonymisierung mal maskiert
  („[[PERSON_01] Schreiben]“), mal nicht. Im zweiten Fall meldete der Restrisiko-Scan das Wort als Personen-Rest und
  blockierte fail-closed.
- Mit Gerichten oder Anschriften hat das nichts zu tun.

**Änderung (`app/privacy/detectors.py`, `_drop_spans_in_document_type_labels`):**
- Person-, Ort- und Organisationstreffer, die **vollständig** innerhalb einer solchen Bezeichnung **am Zeilenanfang in
  eckigen Klammern** liegen, entfallen.
- Das gilt nur für das **geschlossene Vokabular** des Klassifikators (`ALLOWED_DOCUMENT_TYPES` plus
  „unklassifiziert“), in Pseudonymisierung und Restrisiko-Scan gleich.
- Im Fließtext und hinter der Bezeichnung gilt unverändert die normale Erkennung.

### 69.3 Tests

`tests/test_privacy_deadline_excerpts_and_labels.py`, 18 Tests, alle PASS. Eine Mutationsprobe mit abgeschalteter
Korrektur lässt die F4- und F6-Tests scheitern.

| Prüfung | Ergebnis |
|---|---|
| Neuer Auszug enthält die Gerichtsart aus dem Originaltext | PASS |
| Gespeicherter Auszug: ergänzt nur bei eindeutigem Nachweis im Volltext; Wohnort, fehlender Volltext, nicht auffindbar und mehrdeutig bleiben unverändert | PASS |
| Ende-zu-Ende über `DraftingService` (Payload an den Schreib-Provider): „Amtsgericht Kiel“ in Sachverhalt und Auszug lesbar, kein `[ORT_`, „14 C 233/26“ maskiert | PASS |
| Wohnort im Auszug: Ort und Name maskiert | PASS |
| Sitz in der Anschrift: Gericht, Name, Straße, PLZ maskiert | PASS |
| F6: keine Blockade mehr; Kiel, Name, Straße, PLZ maskiert; Typbezeichnung lesbar | PASS |
| Typbezeichnungen lesbar, Namen dahinter maskiert (6 Bezeichnungen) | PASS |
| Wort außerhalb der Bezeichnungsposition nicht ausgenommen | PASS |
| NER-Ausfall mit Typbezeichnung und Fristauszug: kein Payload (Fail-Closed) | PASS |
| Volle Suite (6 Blöcke im Vordergrund, Block 6 in 3 Teilen) | 5417 PASS; 4 FAIL = bekannte fremde Logo-Tests |

**Benchmark `privacy_sources_v1` gegenüber §67:**
- Lecks 0/0, erhalten 109/116, Maskierungen ohne Referenz 26 – unverändert.
- Der Korpus nutzt den Feldfall-Pfad ohne Fristauszüge und Typbezeichnungen, misst F4/F6 also nicht. Abgesichert sind
  sie durch die Tests oben.

### 69.4 Nicht verifiziert

- Installierte App: Der installierte Build `e8d0a0f` enthält §69 nicht. **NICHT VERIFIZIERT** in der App und im
  App-Payload; geprüft sind Repository und Payload an den Schreib-Provider (Ende-zu-Ende im Repository).
- **F7 (NER-Ausfall):** In der installierten App nicht auslösbar, ohne die Installation zu verändern.
  **NICHT VERIFIZIERT** in der App. Repository-Tests `test_ner_failure_still_blocks_with_a_court_name` und
  `test_ner_failure_still_blocks_with_labels_and_excerpts`: PASS.
- Reale Texte, PostgreSQL, echte Transportstrecke: **NICHT VERIFIZIERT**.

**Kein Beweis:** Keine Aussage über vollständige Leckfreiheit oder Rechtskonformität.

## §71 Quellenbasierter Legal-Benchmark mit Referenzlösungen, Lauf in der installierten App (18.10.2026, Branch `feature/feedback-diagnose`, Code-Stand `fc9d3f4`)

Ziel: Der Korpus `benchmarks/legal_e2e_v1` soll realistische, quellenbasierte Akten mit Referenzlösung enthalten. Die
Bewertung soll nach Kriterien getrennt sein. Der Benchmark läuft über die echte Oberfläche der installierten App
(WebView2), nicht nur im Repository.

### 71.1 Bestandsaudit (vor §71)

- Der Korpus hatte 7 synthetische Fälle (LE-D001–D004, LE-T001–T003). Referenzdaten waren Fakten, Chronologie,
  Fristen, Zitate, Widersprüche und Datenschutz. Es gab **keine Referenzlösung** und keine Bewertung juristischer
  Ergebnisse.
- Der Runner `scripts/legal_bench/e2e.py` arbeitete nur im Repository (In-Memory-DB, Payload-Erfassung vor der
  Cloud-Grenze). Die Dokumentfreigabe (Modus C) war im Runner nicht geprüft.
- Das Quellenregister enthielt nur „Gesetze im Internet“ und „Rechtsprechung im Internet“ als `nutzbar` (§65). Alle
  anderen Einträge waren `nicht_geprueft`.

### 71.2 Quellen und Rechteprüfung

| Quelle | Prüfung (18.10.2026, durch den Agenten, keine Freigabe) | Verwendung |
|---|---|---|
| Rechtsprechung im Internet (BGH VIII ZR 18/24, BAG 6 AZR 32/24, BAG 2 AZR 72/24) | bereits `nutzbar` (§65; § 5 Abs. 1 UrhG, amtliche Leitsätze) | Rechtsfrage und Ergebnis aus dem Leitsatz, Aktenzeichen, ECLI |
| Gesetze im Internet | bereits `nutzbar` (§65) | Normen |
| OpenRewi „Verwaltungsrecht in der Klausur“ (16 Fälle), „Grundrechte-Fallbuch“ (10 Fälle) | CC BY-SA 4.0. ShareAlike: Eine Bearbeitung müsste unter derselben Lizenz weitergegeben werden. Das ist eine Owner-Entscheidung. | **nicht verwendet**, `nicht_geprueft` |
| ZJS Übungsfälle | nur „© 2007 - 2026 ZJS“, keine Lizenz | **nicht verwendet** |
| Vis Moot, Soldan Moot | Urheberrecht der Veranstalter, nicht geklärt | **nicht verwendet** |

Erfunden wurde keine Quelle. Die drei Entscheidungen stehen mit Aktenzeichen und ECLI im Fall. Der Validator verlangt
die Referenzentscheidung als belegtes Zitat mit passender ECLI. Die Einstufung als `nutzbar` bleibt Aufgabe der
verantwortlichen Person.

### 71.3 Fälle und Aktenstruktur

Es gibt drei neue **abgeleitete** Fälle (`synthetic: false`, `derivation: "abgeleitet"`). Aus der Entscheidung stammen
nur die Rechtsfrage und das Ergebnis des Leitsatzes. Alle Personen, Firmen, Daten, Beträge und Schreiben sind erfunden.

| Fall | Split | Rechtsfrage (Quelle) | Dokumente | Fakten / Fristen / Widersprüche |
|---|---|---|---|---|
| LE-D005 | dev | Mietervorkaufsrecht bei Verkauf an eine Gesellschaft mit denselben Gesellschaftern (BGH VIII ZR 18/24) | Mietvertrag (docx), Mitteilung der Notarin (pdf), Mail des Mandanten (txt), Schreiben der Verkäuferin (Scan), Aktenvermerk (txt) | 7 / 2 / 2 |
| LE-D006 | dev | Sammelanmeldung zur Insolvenztabelle (BAG 6 AZR 32/24) | Forderungsanmeldung (pdf), Aufstellung (docx), Prüfvermerk, Mail (txt), Protokoll (Scan) | 7 / 2 / 1 |
| LE-T004 | test | Kündigungsschutzklage gegen ein Konsulat, Staatenimmunität (BAG 2 AZR 72/24) | Arbeitsvertrag (docx), Kündigung (Scan), Mail, Vermerk (txt), Schreiben des Konsulats (pdf) | 8 / 2 / 2 |

Der Korpus umfasst jetzt 10 Fälle (6 dev, 4 test) mit 48 Dokumenten.

Es gibt fehlende und widersprüchliche Angaben:

- **LE-D005:** Der Zugang der Mitteilung ist streitig, laut Verkäuferin am 28.02.2026, laut Mandant am 03.03.2026.
  Daraus ergeben sich zwei mögliche Fristenden (28.04. und 04.05.2026).
- **LE-D006:** Die Beträge widersprechen sich (32.112,00 und 31.497,75 EUR).
- **LE-T004:** Der Zugang ist streitig (15.01. und 16.01.2026).

### 71.4 Referenzlösung und Bewertung

Der Block `solution` je abgeleitetem Fall enthält:

- erwartetes Ergebnis mit `result_terms` und `wrong_results`;
- `required_norms`;
- `decisive_facts`;
- `deadlines`;
- `open_points`;
- Herkunft.

Der Validator (`corpus._validate_solution`) lehnt jedes Kriterium ab, das nicht aus den Akten oder der Quelle
ableitbar ist.

`scripts/legal_bench/solution_score.py` bewertet getrennt:

- Ergebnis, einschließlich der Erkennung eines falschen Ergebnisses;
- Normen;
- Fakten;
- Fristen;
- offene Punkte.

Textähnlichkeit wird nicht genutzt. `payload_basis` prüft, ob der **gesendete Payload** die Grundlage der Lösung
enthält.

Grenze der Bewertung: Die Prüfung arbeitet mit exakten Werten, abweichende Beugungen zählen als fehlend. Eine richtige
Antwort eines echten Modells ist damit **nicht gemessen**, siehe 71.7.

### 71.5 Ergebnisse im Repository (offline, Payload vor der Cloud-Grenze)

Lauf `benchmarks/legal_e2e_v1/results.json`: Code-Stand `978448a` mit den Änderungen, die als `fc9d3f4` committet
sind; Fake-Embedding nur in den Tests.

| Messung | dev (6 Fälle) | test (4 Fälle) |
|---|---|---|
| kritische Befunde | 0 – **PASS** | 0 – **PASS** (nach Fix, siehe unten) |
| Lecks / Werte fremder Akten im Payload | 0 / 0 | 0 / 0 |
| Extraktion der Referenzwerte | 192/192 | 110/110 |
| Chronologie im Schriftsatz-Payload | 35/35 | 22/22 |
| Fristdaten-Recall | 13/13 | 4/5 – **FAIL** für LE-T004 05.02.2026 |
| Lösungsgrundlage im Payload | 4/4 | 0/2 – **FAIL** (F8, Übermaskierung) |
| Freigabe-Probe Modus C | auffälliges Dokument im Zustand „decision“ nicht im Payload, freigegebenes darin – **PASS** | |

**Gefundener und behobener Befund:** In LE-T004 stand „Generalkonsulat“ aus der Kurzform „Generalkonsulat Valdoria“ des
Aktentitels im Klartext im Payload. Ursache: Die Gegnerbezeichnung „Generalkonsulat der Republik Valdoria“ war nur in
voller Länge und als letztes Wort geschützt. Behoben in `_build_known_entities`: Die Kurzform aus erstem und letztem Wort
dreiteiliger Namen ist jetzt zusätzlich geschützt. Das verstärkt den Schutz und lockert nichts.

Tests:

- `tests/test_legal_benchmark_solutions.py`: 21 Tests, PASS;
- `tests/test_legal_benchmark_corpus.py`: PASS;
- volle Suite: 5438 PASS, 4 FAIL in fremden Logo-Tests (`test_design_refresh`, nicht §71).

### 71.6 Lauf in der installierten App (WebView2)

**Build und Methode:**

- Build `fc9d3f4` aus einem sauberen Worktree mit PyInstaller und Inno Setup. Das Update ist still installiert.
  SHA-256 der installierten `Lexono.exe`: `c2742683…e1e8ff`, byte-identisch mit dem Build.
- Vorherige Installation gesichert, vorheriger Installer archiviert.
- Daten: isolierte Kopie `LEXONO_DATA_DIR=C:\Users\Bonit\lexono_qa_s65`.
- Cloud-Anfragen gehen an einen lokalen Aufzeichnungsserver mit ungültigem Testschlüssel (QA-Loopback-Freigabe).
  Gateway-Zeile deaktiviert, Proxy auf einen toten Port, keine Kosten.
- Die echte Kanzleidatenbank wurde weder gestartet noch verändert: MD5-Präfix vor und nach jedem Lauf `677CAFD8109F`.

**Ablauf über die Oberfläche:**

1. Mandant über `/dashboard/clients/create`, Akte über `/dashboard/matters/create`.
2. Beteiligte über das Formular der Akte.
3. Upload der gerenderten docx/pdf/txt-Dateien und Bild-PDFs über `#matter-document-upload-input`.
4. Freigabe-Entscheidung per Klick („Für KI-Verarbeitung freigeben“).
5. Chat-Analyse über `/dashboard/chat`.
6. Schriftsatz über den Generator.

Messgrundlage:

- Fristen lesen nur aus der QA-DB.
- Datenschutz und Lösungsgrundlage werden am **tatsächlich gesendeten HTTP-Payload** geprüft.
- Nachweise: `C:\Users\Bonit\lexono_qa_s65\qa_nachweise\s71_legal_benchmark\` (Lauf 1) und `…\lauf2\` (Lauf 2).

**Lauf 1 (Standardkonfiguration der QA-Daten, OCR aus):**

- Die drei Scans blieben `pending` („Noch kein verwertbarer Text“).
- Der Chat in LE-T004 wies das sichtbar aus: „Nicht ausgewertet, da ohne verarbeiteten Text: Kuendigung_Scan.pdf“.
  F3 und F7 fehlten deshalb im Payload.
- LE-D005 Q1: Die Datenschutz-Rückfrage („Kurze Rückfrage … Bis zu Ihrer Auswahl wird nichts an die Cloud gesendet“)
  blieb unbeantwortet. Es gab keine Anfrage, das System war fail-closed – **PASS**.

**Lauf 2 (OCR in der isolierten Umgebung eingeschaltet, Rückfragen per Klick mit der strengsten Wahl
„Unsicher – vorsichtshalber pseudonymisieren“ beantwortet):**

| Messung | LE-D005 | LE-D006 | LE-T004 |
|---|---|---|---|
| Beteiligte angelegt | 4 | 5 | 2 |
| Extraktion (OCR-Scan) | 34/34 (Scan 5/5) | 37/37 (Scan 4/4) | 28/28 (Scan 4/4) |
| Chronologie im App-Text | 7/7 | 5/5 | 6/6 |
| Freigabe | 2 × „Entscheidung erforderlich“, manuell freigegeben, Rest automatisch | alle automatisch | alle automatisch |
| Fristvorschläge (alle `unreviewed`) | 2/2, zusätzlich 10.03.2026 (Vermerkdatum) | 2/2 | 1/2, fehlt 05.02.2026; zusätzlich 21.01.2026 (Vermerkdatum), 30.04.2026 (Kündigungstermin) |
| Chat: Anfrage / Lecks / fremde Akte | 1 (nach 2 Rückfragen) / 0 / 0 | 1 / 0 / 0 | 1 / 0 / 0 |
| Schriftsatz: Anfrage / Lecks / fremde Akte | 1 / 0 / 0 | 1 / 0 / 0 | 1 / 0 / 0 |
| Lösungsgrundlage im Payload | vollständig – **PASS** | vollständig – **PASS** | F8 fehlt – **FAIL** (wie Repository) |

Alle Anfragen gingen nur an `/v1/messages` des Aufzeichnungsservers, mit Testschlüssel. Die App wurde jeweils regulär
geschlossen.

Die App-Ergebnisse stimmen mit dem Repository überein.

### 71.7 Offene Lücken und Blockaden

- **LE-T004, Frist 05.02.2026:** Bei „vorsorglich Fristende 05.02.2026, spätestens 06.02.2026“ wird nur das spätere
  Datum vorgeschlagen. Für die Praxis ist das riskant, weil die sichere, frühere Frist fehlt. Ursache:
  `_EXPLICIT_DEADLINE_BEFORE` kennt „Fristende“ nur mit Doppelpunkt. **Bewusst nicht behoben**, weil LE-T004 zum
  Test-Split gehört und nicht zur Regelabstimmung dienen darf. Vorschlag: einen Dev-Fall mit „Fristende <Datum>“
  ergänzen und dort beheben.
- **F8 Übermaskierung (LE-T004):** „Visa-Anträge“ wird als Organisation maskiert, der entscheidende Fakt ist im Payload
  nicht lesbar. Das ist kein Leck. Es bleibt im Regressionsschutz (`KNOWN_BASIS_GAPS`).
- **Fehlvorschläge bei Fristen:** Vermerkdaten („Vermerk vom …“ vor „Frist:“) und Kündigungstermine erscheinen als
  Fristvorschlag. Alle sind `unreviewed`; eine Übernahme braucht eine Bestätigung.
- **Juristische Korrektheit echter Modellantworten: NICHT VERIFIZIERT.** Der Aufzeichnungsserver liefert eine feste
  Antwort. Ohne Owner-Freigabe gab es keinen echten Modellaufruf und keine API-Kosten. `score_answer` ist dafür bereit.
- **Automatische Aktenzuordnung von E-Mails in der App: blockiert.** Es gibt keinen Mail-Import über die Oberfläche
  ohne IMAP-Postfach, und die Synchronisierung ist in der QA aus. Die Zuordnung ist nur im Repository geprüft
  (Zuordnungsprobe des Runners).
- **NER-Ausfall** in der installierten App nicht auslösbar, ohne die Installation zu verändern: **NICHT VERIFIZIERT**.
- **OCR in der App** wurde nur in der isolierten Umgebung mit `OCR_ENABLED=true` geprüft. Ob der Standard für die
  Kanzlei OCR einschalten soll, entscheidet der Owner.
- Exakte Kriterien: Andere Formulierungen werden als fehlend gewertet. Das kann zu falschen FAILs führen, aber nicht
  zu falschen PASS.
- Die Statusleiste zeigt „Cloud-KI (Gateway) Bereit“, obwohl die Gateway-Zeile deaktiviert ist und an den lokalen
  Aufzeichnungsserver gesendet wird. Nur beobachtet, nicht untersucht.

**Kein Beweis:** Keine Aussage über vollständige juristische Zuverlässigkeit oder vollständige Leckfreiheit. Der
Datenschutzstatus bleibt „teilweise nachgewiesen“.

## §72 Fristenden ohne Doppelpunkt, Verwaltungs-Komposita, KI-Status mit tatsächlichem Weg (18.10.2026, Branch `feature/feedback-diagnose`, Code `0303e0e`)

Ziel: die drei Befunde aus §71 beheben und in der installierten App prüfen. Bestehende Datenschutzgarantien und
Benchmark-Ergebnisse sollen erhalten bleiben. Es gilt die Benchmark-Regel, dass ein Testfall nicht zur Regelabstimmung
dient. Beide Erkennungsbefunde wurden deshalb zuerst an einem neuen Entwicklungsfall nachgestellt: **LE-D007**,
synthetisch, mit eigenen Formulierungen.

### 72.1 P1 – Fristende ohne Doppelpunkt

**Ursache:** In `app/deadlines/extractor.py` zählte „Fristende“ nur mit Doppelpunkt unmittelbar vor dem Datum als
ausdrückliches Fristende (`\w*frist\w*\s*:`). Ohne Doppelpunkt griff nur die Regel „Fristwort + am/bis/zum“. Bei
„vorsorglich Fristende 05.02.2026, spätestens 06.02.2026“ trug deshalb nur „spätestens“. Das frühere, vorsorgliche
Fristende ging verloren, also gerade das sichere.

**Änderung:**

- „Fristende“, auch „Klagefristende“ oder „Fristende vorsorglich“, unmittelbar vor dem Datum zählt jetzt als
  ausdrückliches Fristende.
- Nennt ein Satz mehrere Fristenden, bleiben sie **getrennte** Vorschläge. Jeder Vorschlag nennt:
  - seine Stellung (das früheste / das späteste);
  - seine Bedeutung laut Text (vorsorglich / spätestens);
  - die anderen Daten;
  - den Hinweis, dass nicht entschieden ist, welches maßgeblich ist („NICHT entschieden – anwaltliche Prüfung
    erforderlich“).
- Nichts wird zusammengeführt, und kein Datum ersetzt ein anderes.
- Ein Fristbeginn im selben Satz (Zugang) zählt nicht als Alternative.
- Konfidenz (0,5) und Prüfstatus „ungeprüft“ bleiben unverändert.

**Tests:** `tests/test_s72_deadline_alternatives.py` mit 15 Tests:

- mit und ohne Doppelpunkt;
- umgekehrte Reihenfolge;
- einzelnes Fristende ohne Alternativvermerk;
- negative Fälle: Fristende in anderem Satz, Ereignisdatum, „Fristendet“, kein Fristwort direkt vor dem Datum;
- zwei Sätze;
- Fristbeginn.

Die 261 bestehenden Fristentests: PASS.

**Grenze, bestand schon vorher:** Ein Datum hinter „vom“, gefolgt von einem Fristwort („Vermerk vom 17.02.2026 …
Klagefrist:“, „Das Fristende ist offen, … am 12.02.2026“), wird weiterhin als Frist vorgeschlagen. Daher stammen die
Vermerkdaten aus §71 (10.03.2026, 21.01.2026). §72 ändert daran nichts. Die Vorschläge sind „ungeprüft“.

### 72.2 P2 – „Visa-Anträge“ als Organisation

**Ursache:** Das spaCy-Modell (`de_core_news_lg`) stuft Bindestrich-Komposita aus Verwaltungsgegenstand und
Verfahrensnomen je nach Satz als Organisation ein („Visa-Anträge“, „Steuer-Bescheide“). Die zusammengeschriebene Form
(„Visaanträge“) gilt sogar als Person. „Visa“ ist zugleich ein Firmenname. Die bestehende Kompositum-Regel
(`is_common_compound`) griff nicht: Sie prüft nur rein alphabetische Wörter, scheitert also am Bindestrich, und ihr
Kopfnomen ist nur „antrag“, nicht „anträge“. Die Namensprobe trennt hier nicht: „Visa-Anträge“ ergibt 0,
„Asyl-Anträge“ ergibt 4.

**Änderung:** `is_admin_procedure_compound` in `app/privacy/presidio_ner.py` verlangt **beide** Teile aus geschlossenen
Klassen:

- Gegenstände behördlicher Verfahren (Visa, Pass, Asyl, Bau, Steuer, Renten …);
- Verfahrens- und Dokumentnomen (Antrag/Anträge, Bescheid(e), Verfahren, Akte …).

Die Regel gilt nur für Einwort-Treffer der Kategorien Person und Organisation. Sie wirkt auch in der Zweitsignal- und
Rückfrageerkennung (`is_technical_compound`). Nichts wird pauschal gelockert. Weiterhin maskiert bleiben:

- „Siemens-Anträge“ und „Müller-Anträge“, weil der Vorderteil nicht in der Klasse steht;
- „Visa-Anträge GmbH“ und „Visa Europe“ als Mehrworttreffer;
- „Herr Visa“ wegen der Anrede;
- „Ostermann-Holtkamp (Personalservice GmbH)“;
- bekannte Beteiligte über die Abgleichsliste, z. B. „Visa-Anträge Service GmbH“.

**Tests:** `tests/test_s72_admin_procedure_compounds.py` mit 33 Tests:

- die Klassen und ihre Gegenbeispiele;
- F8 und ähnliche Begriffe im Gateway-Payload lesbar;
- echte Organisations- und Personennamen im Payload maskiert.

### 72.3 P3 – „Cloud-KI (Gateway) Bereit“

**Ursache:** In `app/web/templates/base.html` war das Label „Cloud-KI (Gateway)“ fest verdrahtet. Der Zustand selbst
war schon korrekt getrennt: Konfiguration, TCP/TLS-Erreichbarkeit ohne API-Aufruf und letzte Anfrage
(`cloud_health.py`). In der QA ging die Verbindung direkt an einen lokalen Prüfendpunkt. Zusätzlich galt eine
Gateway-URL **ohne Zugangsdaten** als prüfbar und damit als „Bereit“, obwohl die Factory dann keinen Provider baut und
nicht auf den direkten Weg ausweicht.

**Änderung:**

- `cloud_health.cloud_route` bestimmt den Weg mit derselben Regel wie `factory.build_writing_provider`: Gateway (URL
  und Zugangsdaten), direkt zur offiziellen Adresse oder lokaler QA-Prüfendpunkt.
- `CloudHealth.route` trägt den Weg. Die Statusleiste zeigt „Cloud-KI (Gateway)“, „Cloud-KI (direkt)“ oder
  „Cloud-KI (lokaler Prüfendpunkt)“, der Tooltip nennt den Weg. Ohne Statusprüfung erscheint neutral „Cloud-KI“.
- Die Erreichbarkeit steht weiterhin getrennt daneben; der Weg ist Konfiguration, kein Erreichbarkeitsnachweis.
- Eine Gateway-URL ohne Zugangsdaten meldet „nicht konfiguriert“.
- Die lokale KI bleibt eine eigene Zeile.
- Keine Schlüssel-, Gateway- oder Architekturänderung, keine API-Anfrage.

**Tests:**

- `tests/test_cloud_health.py`: 6 neue Tests (Gateway mit/ohne Zugangsdaten, direkt, Prüfendpunkt, Weg bei
  Nichterreichbarkeit, unzulässige Adresse).
- `tests/test_web_chat.py`: Weg-Label, 3 Fälle.
- 3 bestehende Tests prüften das feste Label, also den Befund selbst. Sie wurden angepasst; ihr Zweck bleibt erhalten.

### 72.4 Repository-QA und Benchmark-Vergleich mit §71

Die volle Suite lief in sechs Teilen im Vordergrund; der erste Gesamtlauf wurde vom System wegen Speichermangels
beendet. Ergebnis: **5494 PASS**, 4 FAIL. Die Fehlschläge sind die bekannten fremden Logo-Tests (`test_design_refresh`:
Logo-Dateien fehlen in der Arbeitskopie), kein neuer Fehler. Der eine XPASS (`Richter`) trat schon vorher auf.

Benchmark `python -m scripts.legal_bench.e2e`, `results.json`:

| Messung | §71 dev (6) | §72 dev (7, + LE-D007) | §71 test (4) | §72 test (4) |
|---|---|---|---|---|
| kritische Befunde | 0 | 0 | 0 | 0 |
| Lecks / Werte fremder Akten | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| Extraktion | 192/192 | 221/221 | 110/110 | 110/110 |
| keep-Werte erhalten | 38/38 | 44/44 | 25/25 | 25/25 |
| Chronologie | 35/35 | 40/40 | 22/22 | 22/22 |
| Fristdatum-Recall | 13/13 | 16/16 | 4/5 | **5/5** |
| Fristen nicht in der Referenz | 2 | 3 (LE-D007: Kündigungstermin 31.05.2026) | 2 | 2 |
| Lösungsgrundlage im Payload | 4/4 | 4/4 | 0/2 | **2/2** |
| erfundene Fundstellen markiert | 12/12 | 14/14 | 8/8 | 8/8 |

Weitere Proben unverändert:

- Freigabe-Probe: auffälliges Dokument nicht im Payload;
- Aktenzuordnung: keine Fremdzuordnung;
- fremder Chat-Anhang: blockiert.

LE-D007 vorher und nachher:

- Fristen 2/3 → 3/3;
- keep 5/6 → 6/6 („Steuer-Bescheide versandt“ war vorher maskiert).

### 72.5 Installierte App (WebView2)

**Build und Installation:**

- Build aus `0303e0e` im sauberen Build-Worktree. Die drei geänderten Funktionen sind im Build enthalten und
  bytecode-gleich mit `0303e0e`.
- Vorher gesichert: `C:\Users\Bonit\lexono_install_backup_vor_0303e0e` (956/956 Dateien). Vorheriger Installer
  archiviert als `Lexono_Setup_fc9d3f4.exe`.
- Stilles Update, Installer-Exit 0. Die installierte `Lexono.exe` ist byte-identisch mit dem Build (SHA-256
  `07ee1a649bb602b24d9fc2f962479cf2b55ee48d673d93ca578d9b078ad67bcd`).
- Die echte Kanzleidatenbank wurde nicht gestartet oder verändert: MD5-Präfix `677CAFD8109F` vor und nach
  Installation und Tests.

**Methode wie §71:**

- isolierte QA-Daten, OCR an;
- alles über die Oberfläche: Mandant, Akte, Beteiligte, Upload, Freigabe, Chat, Schriftsatz;
- Anfragen nur an einen lokalen Aufzeichnungsserver mit Testschlüssel;
- Nachweise: `C:\Users\Bonit\lexono_qa_s65\qa_nachweise\s72_legal_benchmark\`.

| | LE-D005 | LE-D006 | LE-D007 | LE-T004 |
|---|---|---|---|---|
| Extraktion / Chronologie | 34/34 / 7/7 | 37/37 / 5/5 | 29/29 / 5/5 | 28/28 / 6/6 |
| Fristen (alle „ungeprüft“) | 2/2 | 2/2 | 3/3 | **2/2** (§71: 1/2) |
| Hinweis „mehrere mögliche Fristenden“ in der Fristdetailansicht | 28.04. früheste, 04.05. späteste | – (verschiedene Sätze) | 05.03. früheste, 06.03. späteste | 05.02. früheste, 06.02. späteste |
| Lecks / fremde Akte (Chat, Schriftsatz) | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| Lösungsgrundlage im Payload | vollständig | vollständig | – (kein Lösungsblock) | **vollständig** (§71: F8 fehlte) |
| Statusleiste | „Cloud-KI (lokaler Prüfendpunkt)“ Bereit | ebenso | ebenso | ebenso |

**P2 im tatsächlichen Payload:**

- LE-T004: „… [BETEILIGTER_01] kann bestätigen, dass ich nur Visa-Anträge erfasst habe. Telefon [TELE…“;
- LE-D007: „Steuer-Bescheide versandt“;
- „Generalkonsulat“, „Valdoria“, „Kostadinova“, „Lindqvist“, „Brakenstedt“ und „Ostermann/Holtkamp“ kommen in keinem
  Payload vor.

**P3, Gateway-Weg:** eigener App-Start ohne jede Anfrage, mit Gateway-URL auf einen toten lokalen Port und
Dummy-Zugangsdaten nur in der Prozessumgebung. Angezeigt wurde „Cloud-KI (Gateway)“, „nicht erreichbar“, Tooltip
„Verbindung abgelehnt – Weg: Lexono-Gateway“. Die lokale KI zeigt „deaktiviert“.

### 72.6 Nicht verifiziert und offen

- **Direkter Weg in der App: NICHT VERIFIZIERT.** Die Erreichbarkeitsprüfung würde eine echte Verbindung zu
  api.anthropic.com aufbauen. Der Weg ist bewusst nicht in der App getestet, nur im Repository
  (`test_direct_route_to_the_official_address`).
- **Echter Gateway-Server: NICHT VERIFIZIERT** (kein Server, §70).
- Juristische Korrektheit echter Modellantworten: NICHT VERIFIZIERT. Es gab keine Modellaufrufe; der
  Aufzeichnungsserver liefert eine feste Antwort.
- Die Klassen in P2 sind geschlossen. Andere Verwaltungsbegriffe außerhalb der Klassen können weiter übermaskiert
  werden. Das ist fail-closed, aber unverständlich.
- Vermerkdaten als Fristvorschlag bestanden schon vorher, siehe 72.1.
- Das längere Label „Cloud-KI (lokaler Prüfendpunkt)“ bricht in der Seitenleiste zweizeilig um. Es tritt nur in der
  QA auf.

**Kein Beweis:** Keine Aussage über vollständige juristische Zuverlässigkeit oder vollständige Leckfreiheit. Der
Datenschutzstatus bleibt „teilweise nachgewiesen“.

## §73 Fristenerkennung: Dokument-/Vermerkdaten, Ereignisdaten und offene Angaben (18.10.2026, Branch `feature/feedback-diagnose`, Code `3019b7b`)

Ziel: die in §72 dokumentierte Restschwäche gezielt beheben. Fristvorschläge zu Vermerkdaten wie
„Vermerk vom 17.02.2026 … Klagefrist:“ sollen verschwinden, ohne dass echte Fristen verloren gehen. Danach wird der
Legal-Benchmark erneut ausgewertet. Entwickelt wurde am neuen Entwicklungsfall **LE-D008**. Der Test-Split blieb
unverändert, ebenso Fälle, Referenzen und Schwellen.

### 73.1 Bestandsaufnahme: alle Fristvorschläge außerhalb der Referenz (Stand §72)

| Fall | Datum | Textstelle | Einordnung |
|---|---|---|---|
| LE-D005 | 10.03.2026 | „Aktenvermerk vom 10.03.2026 - Az. …⏎Ausübungsfrist: …“ | **Fehlvorschlag** (Vermerkdatum) |
| LE-T004 | 21.01.2026 | „Vermerk vom 21.01.2026 - Az. …⏎Klagefrist: …“ | **Fehlvorschlag** (Vermerkdatum) |
| LE-D003 | 02.12.2025 | „Ortstermin am 02.12.2025“ | korrekt als **Termin**, nicht als Frist eingeordnet; nur außerhalb der Referenz |
| LE-D007, LE-T004 | 31.05., 30.04.2026 | „kündigen … ordentlich zum …“ | **bewusst** als Fristende geführt (§40, per Test abgesichert): Beendigungstermin – fachliche Frage, nicht geändert (73.6) |

Baseline LE-D008: Recall 3/3, aber **4 Fehlvorschläge**: 23.01. (Vermerk-Kopf), 26.01. (E-Mail-Kopf
„Datum: …⏎Betreff: Frist …“), 22.01. („am 22.01.2026 zugestellt“) und 21.01. („Zustellung schon am 21.01.2026; das
Fristende ist danach offen“).

### 73.2 Ursachen (`app/deadlines/extractor.py`)

1. **Feldzeile:** Ein Zeilenumbruch galt nur nach Satzzeichen, an Leerzeilen oder in Tabellen als Satzgrenze. Die
   Kopfzeile „Aktenvermerk vom … - Az. …“ endet ohne Satzzeichen und verschmolz so mit der Feldzeile „Klagefrist: …“
   darunter.
2. **„vom“:** Die Regel „Fristwort bis 30 Zeichen nach dem Datum“ griff auch, wenn „vom“ das Datum als Dokument- oder
   Ereignisdatum ausweist.
3. **Reihenfolge:** Ein Ereignis direkt am Datum (Zustellung, Zugang, Bekanntgabe, „ging … zu“) wurde erst nach den
   Fristwort-Regeln geprüft. Ein Fristwort im Satz machte das Zustelldatum so zum Fristende.
4. **Offene Angabe:** „das Fristende ist offen“ diente als Hinweis auf ein anderes Datum im Satz.

### 73.3 Änderung (eng, jeweils mit Gegenproben)

- Eine Folgezeile, die als Feldzeile beginnt („Wort:“), ist eine harte Satzgrenze. Weiche PDF-Umbrüche mitten im Satz
  bleiben unverändert (Regressionstest P2.3).
- Die Regel „Fristwort danach“ greift nicht bei „vom <Datum>“ und nicht, wenn das Fristwort ausdrücklich offen ist
  („ist [danach/noch] offen“, „unklar“, „nicht berechnet“ …). Gleiches gilt für die Regel „Fristwort + am/bis/zum“.
- Zustellung, Zugang oder Bekanntgabe unmittelbar am Datum werden **Fristbeginn**: Vorschlag ohne Fälligkeitsdatum,
  „Fristende wurde nicht berechnet“. Ein ausdrückliches Fristende („Fristende“, „bis“, „spätestens“) geht weiterhin vor.
  „Eingang“ ist bewusst nicht in der Regel, weil „Eingang am … erforderlich“ ein Fristende ist.
- Unverändert:
  - Konfidenz;
  - Prüfstatus „ungeprüft“ (keine automatische Bestätigung);
  - Textstelle und Begründung je Vorschlag;
  - der §72-Hinweis bei mehreren Fristenden.

### 73.4 Repository-Tests

- `tests/test_s73_deadline_document_dates.py`: 25 Tests.
  - Vermerk- und Dokumentdaten mit und ohne „vom“, E-Mail-Kopf, „Schreiben vom …“;
  - Gegenproben mit echtem Fristende in der Feldzeile, „läuft … ab“, „Wiedervorlage:“, weichem Umbruch;
  - Ereignisdaten als Fristbeginn, Gegenproben dazu („endet am … nach Zugang“, „Eingang … Fristablauf“,
    „Zustellung spätestens bis“, vorsorglich/spätestens);
  - offene Angaben;
  - Prüfstatus, Quelle und Unsicherheit.
- Bestehende Fristen- und Extraktor-Tests (15 Dateien): **311 PASS**, 1 xfailed wie zuvor.
- Volle Suite (sechs Teile, Vordergrund): **5519 PASS**, 4 FAIL. Die Fehlschläge sind die bekannten fremden Logo-Tests
  `test_design_refresh`, kein neuer Fehler. Dazu 10 xfailed, 1 xpassed (bekannt, „Richter“), 1 skipped.

### 73.5 Legal-Benchmark (`python -m scripts.legal_bench.e2e`, `results.json`) im Vergleich zu §72

| Messung | §72 dev (7) | §73 dev (8, + LE-D008) | §72 test (4) | §73 test (4) |
|---|---|---|---|---|
| kritische Befunde / Lecks / Werte fremder Akten | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| Extraktion | 221/221 | 246/246 | 110/110 | 110/110 |
| Chronologie | 40/40 | 45/45 | 22/22 | 22/22 |
| geschützte Inhalte: keep-Werte erhalten | 44/44 | 49/49 | 25/25 | 25/25 |
| Fristdatum-Recall | 16/16 | 19/19 | 5/5 | 5/5 |
| **Fristen außerhalb der Referenz** | 3 | **2** | 2 | **1** |
| Lösungsgrundlage im Payload | 4/4 | 4/4 | 2/2 | 2/2 |
| erfundene Fundstellen markiert | 14/14 | 16/16 | 8/8 | 8/8 |

Weitere Proben unverändert:

- Freigabe-Probe: auffälliges Dokument nicht im Payload;
- Aktenzuordnung: keine Fremdzuordnung;
- fremder Chat-Anhang: blockiert.

Außerhalb der Referenz verbleiben nur der Termin (LE-D003) und die Kündigungstermine (73.6).

**Grenzen der Bewertung:**

- Die Lösungsgrundlage wird am **gesendeten Payload** geprüft. Er enthält den Aktentext wörtlich, aber
  pseudonymisiert. Ein exakter Wertvergleich ist dort sachgerecht; er scheitert nur, wenn die Maskierung einen Wert
  verändert (so F8 vor §72).
- Die Bewertung von **Antworten** (`score_answer`) arbeitet ebenfalls mit exakten Werten. Zulässige Paraphrasen
  („denselben Gesellschaftern“) zählen dort als fehlend; das ergibt falsche FAILs, aber keine falschen PASS. Sie wurde
  mangels echter Modellantworten nicht angewendet.
- Juristische Korrektheit wird weder gemessen noch aus Textähnlichkeit abgeleitet.

Quellen und Rechte sind unverändert gegenüber §71. LE-D008 ist vollständig synthetisch, mit realen ZPO-Normen; es
wurde keine fremde Falllösung übernommen. Es gab keine Cloud-Aufrufe und keine Kosten.

### 73.6 Installierte App (WebView2)

**Prüfung vor dem Update:**

- keine laufende Instanz;
- Sicherungsziel frei, frühere Sicherungen und Archiv vollständig;
- 74 GB frei;
- die installierte EXE entsprach dem Build `0303e0e`;
- Build-Worktree sauber.

**Build und Installation:**

- Build aus `3019b7b`; `_role`, `_is_hard_newline` und `_event_at_date` sind bytecode-gleich mit `3019b7b`.
- Sicherung `C:\Users\Bonit\lexono_install_backup_vor_3019b7b` (956/956 Dateien). Installer `0303e0e` archiviert.
- Stilles Update, Exit 0. Die installierte EXE ist byte-identisch mit dem Build (SHA-256
  `bb63d5c29a4fc1de87a20fb9acef9c45e617ebf8d9b36e19724ae159731f2ced`).
- Echte Datenbank vor und nach allem `677CAFD8109F`.

**Lauf:**

- Methode wie §71/§72: isolierte QA-Daten, OCR an, alles über die Oberfläche, Anfragen nur an einen lokalen
  Aufzeichnungsserver mit Testschlüssel.
- Nachweise: `C:\Users\Bonit\lexono_qa_s65\qa_nachweise\s73_legal_benchmark\`.

| | LE-D005 | LE-D007 | LE-D008 | LE-T004 |
|---|---|---|---|---|
| Fristen mit Datum (Recall / außerhalb Referenz) | 2/2 / – (§72: 10.03.) | 3/3 / 31.05. (Beendigungstermin) | 3/3 / – | 2/2 / 30.04. (Beendigungstermin; §72 zusätzlich 21.01.) |
| Vorschläge ohne Datum (Art laut Begründung) | 2 × Fristbeginn | Fristbeginn, Fristdauer | 3 × Fristbeginn (Zustellungen 22.01., 21.01., 21.01.) | Fristbeginn, Fristdauer |
| Prüfstatus | alle ungeprüft | alle ungeprüft | alle ungeprüft | alle ungeprüft |
| §72-Hinweis „mehrere Fristenden“ in der Detailansicht | ja | ja | – (verschiedene Sätze) | ja |
| Extraktion / Chronologie | 34/34 / 7/7 | 29/29 / 5/5 | 25/25 / 5/5 | 28/28 / 6/6 |
| Payload: Lecks / fremde Akte (Chat, Schriftsatz) | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| Lösungsgrundlage im Payload | vollständig | – | – | vollständig |

In der Detailansicht von „Aufgaben & Fristen“ zeigen Fristbeginn-Vorschläge „nicht berechnet“. Einschränkung: Das
Merkmal „Möglicher Fristbeginn“ wurde im Seitentext gesucht, nicht nur in der Detailansicht. Maßgeblich für die Art
ist die gespeicherte Begründung.

LE-D006 wurde in §73 nicht erneut in der App ausgeführt. Er enthält keine der geänderten Formulierungen; sein
App-Ergebnis aus §72 (2/2) ist nicht neu verifiziert. Nach den Läufen bleiben keine Lexono-, pytest- oder
QA-Prozesse zurück.

### 73.7 Offene Risiken und nächste Schritte

- **Beendigungstermin „kündigen … zum <Datum>“ als Fristende (fachliche Entscheidung, Owner):**
  - Option A: so lassen (heute; der Termin ist relevant, wird aber „Frist“ genannt);
  - Option B: als eigene Art „Beendigungstermin“ führen (Termin, kein Fristablauf);
  - Option C: nicht vorschlagen.

  Die bestehenden §40-Tests sichern Option A ab.
- Die Feldzeilen-Grenze erkennt nur „Wort:“ am Zeilenanfang. Mehrwort-Feldbezeichnungen („Frist für Stellungnahme:“)
  sind keine Grenze. Das kann weiter zu Fehlvorschlägen führen, aber nicht zum Verlust echter Fristen.
- Die Ereignisregel ist bewusst eng (Zustellung, Zugang, Bekanntgabe, „ging … zu“). Andere Ereignisse („erhalten am“)
  bleiben unberührt.
- Antwortbewertung mit exakten Werten: Für echte Modellantworten wären vorab geprüfte, zulässige Varianten je
  Kriterium nötig, im Entwicklungs-Split und vor dem Lauf festgelegt. Keine juristische Bewertung über Textähnlichkeit.
- **NICHT VERIFIZIERT:** juristische Korrektheit echter Modellantworten, reale Kanzleitexte, PostgreSQL.

**Kein Beweis:** Keine Aussage über vollständige juristische Zuverlässigkeit oder vollständige Leckfreiheit. Der
Datenschutzstatus bleibt „teilweise nachgewiesen“.

## §74 Beendigungstermin als eigene Art, mehrteilige Fristfelder, Legal-Benchmark erweitert (18.10.2026, Branch `feature/feedback-diagnose`, Code `fc7d3d3`)

Ziel: Den Beendigungszeitpunkt („Das Mietverhältnis wird ordentlich zum 30.04.2026 gekündigt“) als eigene Art führen,
nicht als Fristende. Das entspricht Option B aus §73.7, vom Owner beauftragt. Dazu die Fristenerkennung für mehrteilige
Feldbezeichnungen härten und den Benchmark um einen Fall erweitern, der beides gemeinsam prüft. Bestehende Fälle,
Test-Split, Referenzen und Schwellen sind unverändert.

### 74.1 Bestandsaufnahme

- Die Art eines Vorschlags steht nur in der Begründung („Art: …“) und wird über `review.kind_from_reasoning`
  abgeleitet. Es gibt kein eigenes Datenbankfeld und keine Migration.
- Beendigungstermine entstanden im Zweig „zum + Beendigungswort“ als „Art: Frist“ mit dem Hinweis
  „zum (Beendigung/Ablauf)“. Derselbe Hinweis traf auch echte Fristenden („Die Widerspruchsfrist endet zum …“).
- **Nachgelagerte Pfade:**
  - Bezeichnung (`kind_label`), Liste und Detail in „Aufgaben & Fristen“ (Spalte/Feld „Typ“ war fest „Frist“);
  - Dokumentansicht der Akte;
  - Bezeichnung im Cloud-Kontext (`local_ai_provider.label_of`);
  - Zweitmeinungs-Abgleich (`second_opinion.comparison`, Cloud-Schema frist/termin/fristbeginn);
  - Akten-Export (ohne Art);
  - Fund-Schlüssel (`finding_key` = Art|Angabe|n, Duplikatschutz bei erneuter Analyse);
  - Sortierung und Kalender nach Datum.
- Erinnerungen sind laut `settings.py` noch nicht an die Fristenlogik angebunden; dort ändert sich nichts.
- **Weitere Lücken (Probe vor der Änderung):**
  - „Frist für Stellungnahme: 20.03.2026“ und „Frist zur Klageerhebung: 05.03.2026“ ergaben **keinen** Vorschlag;
    echte Fristen gingen verloren.
  - „Aktenvermerk 17.02.2026⏎Frist zur Klageerhebung: …“ ergab einen Fehlvorschlag. Die Feldzeilen-Grenze aus §73
    kannte nur Bezeichnungen aus einem Wort.
  - „Das Mietverhältnis wird … zum 30.04.2026 gekündigt“ (Partizip nach dem Datum) ergab keinen Vorschlag.

### 74.2 Änderung

**Beendigungstermin** (`app/deadlines/extractor.py`): Im Zweig „zum <Datum>“ entscheidet `_termination_cue`.

- Beendigungstermin, wenn:
  - eine Beendigungshandlung vor dem Datum steht („kündig…“, „beend…“, „Aufhebung“, „ausscheiden“);
  - oder „<Verhältnis/Vertrag/Mitgliedschaft/Mandat> endet zum“;
  - oder ein Partizip danach folgt („gekündigt“, „beendet“, „aufgehoben“).
- Gegenprobe: Benennt der Satz das Ende einer **Frist** („Die Kündigungsfrist endet zum …“, „Ablauf“), bleibt es ein
  Fristende.
- Begründung: „Art: Beendigungstermin (kein Fristende) – … Das ist keine Frist: ob die Beendigung wirksam ist und ob
  eine Kündigungsfrist eingehalten wurde, wurde NICHT geprüft; daraus folgende Fristen … sind gesondert zu ermitteln.“
- Erhalten bleiben Datum, Originalangabe, Textstelle, Konfidenz 0,5 und Prüfstatus „ungeprüft“. Ein Beendigungstermin
  ist nie Teil des §72-Hinweises „mehrere mögliche Fristenden“.

**Nachgelagerte Pfade:**

- Art `beendigung` mit Bezeichnung „Möglicher Beendigungstermin (kein Fristende)“.
- `type_label` zeigt „Beendigungstermin“ statt „Frist“ in Spalte und Detailfeld „Typ“.
- Cloud-Kontext: „Möglicher Beendigungstermin (kein Fristende)“.
- Zweitmeinung: `termin`, Cloud-Schema unverändert.
- Export: neues Feld `kind` (additiv).

**Fund-Schlüssel:** Beendigungstermine teilen den Schlüsselraum `frist`. Ein bis §73 gespeicherter Vorschlag
(`frist|30.04.2026|1`) wird bei erneuter Analyse wiedererkannt, es entsteht **kein** Duplikat. Altvorschläge werden
**nicht umgedeutet**: Der alte Hinweis traf auch echte Fristenden, deshalb bleiben sie „Frist“, bis ein Mensch sie
prüft.

**Mehrteilige Feldbezeichnungen und Feldzeilen:**

- „<…frist…> für/zur/zum/zu/der/des <1–4 Wörter>:“ unmittelbar vor dem Datum ist ein ausdrückliches Fristende.
- Eine Folgezeile mit mehrteiliger Feldbezeichnung (nur Buchstabenwörter, bis zu 6 Wörter, also keine Uhrzeit) ist
  eine Satzgrenze.
- Eine **abgeschlossene** Feldzeile „Feld: <Datum>“ ist ebenfalls eine Satzgrenze. Sonst galten zwei verschiedene
  Fristen auf zwei Zeilen als Alternativen eines Satzes; das hat LE-D009 aufgedeckt.
- Weiche Umbrüche in einem Feldwert („bis zum⏎31.07.2026“) bleiben verbunden.

**Bestehender Test geändert (bewusst, Owner-Auftrag):** `test_deadlines_kinds.py` führte „kündigen wir … ordentlich zum
30.06.2026“ als Fristende (§40). Der Fall ist jetzt als Beendigungstermin abgesichert
(`test_termination_date_is_a_termination_not_a_deadline_end`). Die übrigen Fälle dieser Liste sind unverändert.

### 74.3 Regressionstests (Repository)

- `tests/test_s74_termination_dates.py` (46 Tests):
  - ordentliche und außerordentliche Kündigungen, „endet zum“, „beendet“, „Kündigung zum“ – mit Prüfung von Begründung,
    Unsicherheit und fehlender Wirksamkeitsaussage;
  - Gegenproben: Kündigungs- und Widerspruchsfrist „endet zum“, „bis zum“, „bis spätestens“, „spätestens am“,
    „Fristende“;
  - Zugang und Zustellung als Fristbeginn;
  - Dokument- und Vermerkdaten, unvollständiges „zum nächstmöglichen Termin“, zwei Beendigungstermine;
  - mehrteilige Feldbezeichnungen positiv und negativ, abgeschlossene Feldzeile, weicher Umbruch, Uhrzeit;
  - vorsorglich/spätestens weiterhin als Alternativen;
  - Bezeichnungen, Altvorschläge, Fund-Schlüssel;
  - erneute Analyse ohne Duplikat;
  - Speicherung „ungeprüft“;
  - E2E LE-D009.
- `tests/test_s74_termination_ui_export.py` (2 Tests): Typ-Spalte und Detail in „Aufgaben & Fristen“, `kind` im
  Export-Manifest.
- Fristen-, Zweitmeinungs-, Export- und Aufgaben-Suites (24 Dateien): **519 PASS**, 1 xfailed wie zuvor.
- Volle Suite (sechs Teile, Vordergrund): **5567 PASS**.
  - **4 FAIL, bereits bekannt:** `test_design_refresh` mit 4 Logo-Tests; die fremden Logo-Dateien fehlen in der
    Arbeitskopie. Kein neuer Fehler.
  - Dazu 10 xfailed, 1 xpassed (bekannt), 1 skipped.
- Ein Zwischenfehler in `test_legal_benchmark_e2e.py` wurde behoben: `summarise` setzte die neuen Felder voraus. Jetzt
  abwärtskompatibel, der Test ist unverändert.

### 74.4 Legal-Benchmark

**Neuer Entwicklungsfall LE-D009** (Mietrecht, Eigenbedarfskündigung, vollständig synthetisch, reale BGB-Normen). Die
Referenz war **vor dem ersten Lauf** festgelegt: SHA-256 `2f9865cb…f25b`, 18.10.2026 15:16, nach dem Lauf unverändert.

- **Fristen (`deadlines`):**
  - 31.08.2026, Widerspruch, „bis spätestens“ und „Fristende“;
  - 06.03.2026, „Frist für Stellungnahme:“ und „Frist zur Stellungnahme an die Gegenseite:“;
  - 02.11.2026, Räumung „spätestens am“;
  - 02.03.2026, „Wiedervorlage:“.
- **Beendigungstermin (`terminations`, neu):** 31.10.2026 in drei Formulierungen.
- **Negativdaten (`not_deadlines`, neu):**
  - Schreiben vom 27.01. und 16.02.;
  - E-Mail-Kopf 03.02.;
  - Zugang 28.01. (Fristbeginn) und 29.01. (bloßes Datum);
  - Vermerk 18.02.;
  - Mietbeginn 01.04.2009.

**Validator und Runner** (`scripts/legal_bench/corpus.py`, `e2e.py`): Die neuen Felder sind optional und werden gegen
den Aktentext geprüft, ohne Überschneidung mit den Fristen. Der Datums-Recall bleibt **wie bisher definiert**
(Datumsgleichheit, Art nicht geprüft). Zusätzlich und getrennt erfasst werden:

- Beendigungs-Recall;
- Beendigungstermine außerhalb der Referenz;
- erwarteter Beendigungstermin als Frist;
- Negativdatum als Vorschlag;
- Referenzfrist nur als Beendigungstermin.

Vergleich mit §73:

| Messung | §73 dev (8) | §74 dev (9, + LE-D009) | §73 test (4) | §74 test (4) |
|---|---|---|---|---|
| kritische Befunde / Lecks / Werte fremder Akten | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| Extraktion | 246/246 | 276/276 | 110/110 | 110/110 |
| Chronologie | 45/45 | 50/50 | 22/22 | 22/22 |
| geschützte Inhalte: keep-Werte erhalten | 49/49 | 54/54 | 25/25 | 25/25 |
| **Fristdatum-Recall** | 19/19 | **23/23** (19/19 der bisherigen Fälle) | 5/5 | **5/5** |
| Daten außerhalb der Fristreferenz | 2 | 3 (+ LE-D009 31.10. = Beendigungstermin) | 1 | 1 |
| Beendigungs-Recall | – | **1/1** | – | 0/0 (keine Referenz) |
| Beendigungstermin als Frist | – | 0 | – | 0 |
| Negativdatum als Vorschlag | – | 0 | – | 0 |
| Beendigungstermin außerhalb der Referenz | – | LE-D002 30.06., LE-D007 31.05. | – | LE-T004 30.04. |
| **Referenzfrist als Beendigungstermin eingeordnet** | – | **LE-D002 30.06.2026** | – | 0 |
| Lösungsgrundlage im Payload | 4/4 | 4/4 | 2/2 | 2/2 |
| erfundene Fundstellen markiert | 16/16 | 18/18 | 8/8 | 8/8 |

Weitere Proben unverändert: Freigabe, Aktenzuordnung, fremder Anhang.

**Art-Abweichung LE-D002 (offen, Owner):** Die bestehende Referenz führt „kündigen wir … ordentlich zum 30.06.2026“
unter `deadlines` (Art „Ende Arbeitsverhältnis laut Kündigung“). Nach §74 ist das ein Beendigungstermin. Der Fall wurde
**nicht** geändert, die Abweichung ist ausgewiesen. Eine Referenzkorrektur – Verschieben nach `terminations` – wäre
eine bewusste Korpusänderung durch die verantwortliche Person. LE-D007 und LE-T004 haben keine Beendigungsreferenz; ihre
Kündigungstermine sind als „außerhalb der Referenz“ ausgewiesen.

**Grenzen der Bewertung:**

- Exakte Werte sind kein Maßstab für die juristische Qualität von Modellantworten. Echte Modellantworten wurden nicht
  bewertet.
- Keine fremden Falllösungen übernommen; Quellenregister unverändert.

### 74.5 Installierte App (WebView2)

**Vorprüfung:**

- keine Lexono-Instanz und keine Test- oder QA-Prozesse;
- zwei fremde Ollama-Prozesse unberührt; die QA nutzt sie nicht (`LOCAL_AI_ENABLED=false`);
- Sicherungsziel frei, sechs frühere Sicherungen und Archiv vollständig;
- die installierte EXE entsprach `3019b7b`;
- Worktree sauber, echte DB `677CAFD8109F`.

**Build und Installation:**

- Build aus `fc7d3d3`. Bytecode-gleich: Extraktor (`_role`, `_termination_cue`, `_is_hard_newline`, `_candidate`),
  `review`, `finding_keys`, Export (alle 5 Funktionen), `_review_info`. Das Template enthält `type_label`.
- Sicherung `C:\Users\Bonit\lexono_install_backup_vor_fc7d3d3` (956/956). Installer `3019b7b` archiviert.
- Exit 0. Die installierte EXE ist byte-identisch (SHA-256
  `bff715a3a13cea2eed5b84bca424efc7f801391ae4444c21049781d26890d337`).
- Echte DB vor und nach allem `677CAFD8109F`.

**Lauf:**

- Methode wie §71–§73: isolierte QA-Daten, OCR an, alles über die Oberfläche, Anfragen nur an den lokalen
  Aufzeichnungsserver mit Testschlüssel.
- Nachweise: `C:\Users\Bonit\lexono_qa_s65\qa_nachweise\s74_legal_benchmark\` (`ergebnis_<Fall>.json` mit `arten`,
  `liste_typen`, Screenshots `*_beendigung_*.png`, `*_liste.png`).

| Fall | Fristen (Datum) | Beendigungstermine (gespeichert = UI) | Art-Abweichung zur Referenz | Status | Lecks / fremd |
|---|---|---|---|---|---|
| LE-D009 | 4/4, alle „Frist“, inkl. „Frist für Stellungnahme:“ und „Frist zur Stellungnahme an die Gegenseite:“ | 31.10.2026 (3×) „Beendigungstermin / Möglicher Beendigungstermin (kein Fristende)“, ohne Wirksamkeitsaussage | keine; kein Negativdatum; Zugang 28.01. als Fristbeginn ohne Datum | ungeprüft | 0 / 0 |
| LE-D002 | 2/2 | 30.06.2026 | Referenz: Frist (74.4) | ungeprüft | 0 / 0 |
| LE-D007 | 3/3 | 31.05.2026 | keine Referenz | ungeprüft | 0 / 0 |
| LE-T004 | 2/2 | 30.04.2026 | keine Referenz | ungeprüft | 0 / 0 |
| LE-D005, LE-D006, LE-D008 | 2/2, 2/2, 3/3 | – | keine | ungeprüft | 0 / 0 |

In allen Fällen gilt:

- Das Feld „Typ“ in Detail und Liste entspricht der gespeicherten Art.
- Extraktion und Chronologie sind vollständig.
- Die Lösungsgrundlage ist bei LE-D005, LE-D006 und LE-T004 vollständig.
- Nach den Läufen bleiben keine Lexono-, Test- oder QA-Prozesse zurück.

### 74.6 Offene Punkte

- LE-D002: Referenzkorrektur ja oder nein (Owner, siehe 74.4).
- Das Feld heißt für alle Arten „Fällig am“, auch beim Beendigungstermin. Typ und Art stellen die Einordnung klar; eine
  eigene Bezeichnung („Beendigung zum“) wäre eine kleine UI-Folgeänderung.
- Der Prüfhinweis „Datum verstrichen – bitte prüfen“ gilt für alle ungeprüften Vorschläge, auch für
  Beendigungstermine. Er ist als Prüfhinweis gedacht, behauptet aber kein Fristversäumnis.
- Altvorschläge mit „zum (Beendigung/Ablauf)“ bleiben „Frist“, bis sie geprüft werden.
- Der §72-Hinweis „mehrere mögliche Fristenden“ gilt weiterhin für zwei Fristenden im **selben Satz**, auch wenn es
  verschiedene Fristen sind. Getrennte Zeilen sind jetzt abgegrenzt.
- **NICHT VERIFIZIERT:** juristische Korrektheit echter Modellantworten, reale Kanzleitexte, Erinnerungen (nicht
  angebunden).

**Kein Beweis:** Keine Aussage über vollständige juristische Zuverlässigkeit oder vollständige Leckfreiheit. Der
Datenschutzstatus bleibt „teilweise nachgewiesen“.

## §75 Referenzkorrektur LE-D002, „Beendigung zum“, Hinweis nur bei konkurrierenden Fristenden (18.10.2026, Branch `feature/feedback-diagnose`, Code `ca695d8`)

Ausgangsstand: `fcb9520` (Doku §74) auf Code `fc7d3d3`, synchron mit dem Remote. `main` unverändert (`5735e5b`).

### 75.1 Referenz LE-D002 (dev)

- **Formulierung (D3):** „hiermit kündigen wir das Arbeitsverhältnis ordentlich zum 30.06.2026.“
- **Alt:** `reference.deadlines`, Art „Ende Arbeitsverhältnis laut Kündigung“.
- **Neu (18.10.2026, §75, Owner-Auftrag):** `reference.terminations`, Art „Beendigung des Arbeitsverhältnisses laut
  Kündigung (kein Fristende)“. Fallversion 3.
- **Begründung:** Nach der seit §74 geltenden fachlichen Klassifikation nennt die Formulierung den Zeitpunkt, zu dem
  das Arbeitsverhältnis enden soll, kein Fristende.
- **Nachweis:** Alte und neue Einordnung, Begründung und Datum stehen im Fall unter `reference_history`. Der Test
  `test_only_le_d002_carries_a_reference_history` sichert ab, dass nur dieser Fall eine Änderungshistorie trägt.
- **Unverändert:**
  - Fristen-Aufgabe Q5 (erwartete schon nur 13.04.2026);
  - keep-Wert 30.06.2026 und Fakt F3 – das Datum muss weiter im Payload stehen;
  - alle anderen Fälle, Referenzwerte und Schwellen;
  - der Test-Split: kein Diff seit §74.
- **Tests:** Kein bestehender Test setzte 30.06.2026 als Frist voraus. `test_legal_benchmark_e2e` prüft nur den
  Datums-Recall und Datenschutz von LE-D002. Die neue Einordnung wird über die Erkennung selbst geprüft und nicht nur
  über die Referenz.

### 75.2 Datumsfeld beim Beendigungstermin

- **Detailansicht** „Aufgaben & Fristen“ und **Bearbeitungsformular** des Vorschlags: „Beendigung zum“ statt „Fällig am“,
  wenn die Art „Beendigungstermin“ ist. Für alle anderen Arten bleibt „Fällig am“.
- **Aktenseite** (Reiter „Aufgaben & Fristen“): Die Art je KI-Vorschlag wird jetzt genannt, wie bisher schon in der
  Dokumentansicht. Ein Beendigungstermin steht dort nicht mehr unkommentiert unter den Fristen. Manuelle Einträge
  bleiben ohne Art.
- **Unverändert:**
  - Spaltenüberschrift der Liste („Fällig am“, gilt für alle Zeilen); die Typ-Spalte nennt „Beendigungstermin“;
  - Speicherung (keine Migration, Art in der Begründung);
  - Export (`kind`, §74);
  - Sortierung nach Datum;
  - Kalender (Ansicht weiterhin deaktiviert);
  - Erinnerungen (nicht angebunden);
  - Typ, Unsicherheit, Fundstelle und Begründung. Es gibt keine Wirksamkeitsaussage.

### 75.3 Hinweis „mehrere mögliche Fristenden“

**Ursache:** Bis §74 bekam jedes Fristende eines Satzes den Hinweis, sobald ein zweites, anderes Fristende im selben Satz
stand. So galten auch verschiedene Fristen als Alternativen („Wir erwarten Ihre Stellungnahme bis 06.03.2026 und die
Zahlung … bis 13.03.2026“).

**Änderung** (`_competing`, `_mark_alternative_deadlines`): Zwei Fristenden konkurrieren nur, wenn beides zutrifft:

1. Es gibt ein **Alternativsignal** vor oder zwischen ihnen: „vorsorglich“, „spätestens“, „hilfsweise“, „andernfalls“,
   „sonst“, „oder“, „liefe“, „bei Zugang/Zustellung/Bekanntgabe“.
2. Zwischen ihnen steht **kein anderer Gegenstand**. Erlaubt ist nur Fristvokabular (Frist, Fristende, Fristablauf,
   Zugang, Zustellung …). Klammerinhalte wie „(laut Mandantin)“ zählen nicht.

Jeder Vorschlag nennt nur seine tatsächlichen Konkurrenten. Ein Fristbeginn, ein Beendigungstermin oder ein
Dokumentdatum ist nie Konkurrent; das ist unverändert seit §72–§74. Fristen werden nicht entfernt, nur der Hinweis
ändert sich.

**Korpus vorher/nachher:** Der Hinweis steht weiterhin genau bei den drei echten Alternativpaaren:

- LE-D005: 28.04./04.05.;
- LE-D007: 05.03./06.03.;
- LE-T004: 05.02./06.02.

Die gefundenen Daten sind in allen Dokumenten identisch.

**Tests:** `tests/test_s75_competing_deadlines.py` mit 21 Tests:

- 6 konkurrierende Paare, darunter umgekehrte Reihenfolge, „bei Zugang … liefe … erst“, Klammerzusätze und „oder“;
- 6 Paare verschiedener Fristen, im selben Satz und in benachbarten Zeilen – alle Fristen bleiben erhalten, kein
  Hinweis;
- 5 Fälle mit Zustellung oder Zugang plus Fristende, Vermerk- und Schreibendaten sowie Beendigungstermin plus Fristende;
- UI: „Beendigung zum“ und „Fällig am“, Art-Angabe auf der Aktenseite;
- Referenz LE-D002 und ihre Historie.

### 75.4 Repository-QA

- Fristen-, UI-, Export-, Zweitmeinungs- und Benchmark-Suites (29 Dateien): **671 PASS**, 1 xfailed wie zuvor.
- Volle Suite (sechs Teile, Vordergrund): **5588 PASS**.
  - **4 FAIL, bereits bekannt:** `test_design_refresh` mit 4 Logo-Tests; die fremden Logo-Dateien fehlen in der
    Arbeitskopie. Kein neuer Fehler.
  - Dazu 10 xfailed, 1 xpassed (bekannt), 1 skipped.

**Benchmark** (`results.json`) im Vergleich zu §74:

| Messung | §74 dev | §75 dev | §74 test | §75 test |
|---|---|---|---|---|
| kritisch / Lecks / Werte fremder Akten | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| Extraktion / Chronologie / keep | 276/276 / 50/50 / 54/54 | unverändert | 110/110 / 22/22 / 25/25 | unverändert |
| Fristdatum-Recall | 23/23 | 22/22 (30.06.2026 ist jetzt Beendigungsreferenz) | 5/5 | 5/5 |
| **bisherige dev-Fälle:** Fristen + Beendigung | 19/19 (alles als Frist) | **18/18 + 1/1 = 19/19** | – | – |
| **LE-D009 (separat):** Fristen + Beendigung | 4/4 + 1/1 | 4/4 + 1/1 | – | – |
| Beendigungs-Recall | 1/1 | 2/2 | 0/0 | 0/0 |
| Referenzfrist als Beendigungstermin | LE-D002 30.06. | **keine** | 0 | 0 |
| Beendigungstermin außerhalb der Referenz | LE-D002, LE-D007 | LE-D007 31.05. | LE-T004 30.04. | LE-T004 30.04. |
| Beendigungstermin als Frist / Negativdatum als Vorschlag | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| Lösungsgrundlage | 4/4 | 4/4 | 2/2 | 2/2 |

Freigabe, Aktenzuordnung und fremder Anhang sind unverändert ohne kritischen Befund. Exakte Werte sind kein Maßstab für
die juristische Qualität von Modellantworten; Modellantworten wurden nicht bewertet.

### 75.5 Installierte App (WebView2)

**Vorprüfung:**

- keine Lexono-Instanz und keine Test- oder QA-Prozesse;
- Sicherungsziel frei, sieben frühere Sicherungen, Archiv vollständig;
- 71 GB frei;
- die installierte EXE entsprach `fc7d3d3`;
- Worktree sauber, echte DB `677CAFD8109F`.

**Build und Installation:**

- Build aus `ca695d8`. Bytecode-gleich: Extraktor (50 Funktionen, inklusive `_competing`) und Akten-Router (17
  Funktionen). Beide Templates enthalten die Änderungen.
- Sicherung `C:\Users\Bonit\lexono_install_backup_vor_ca695d8` (956/956). Installer `fc7d3d3` archiviert.
- Stilles Update, Exit 0. Die installierte EXE ist byte-identisch (SHA-256
  `e3eee2efada1471529fd546487fe9da914df10564c7f7331c2768bf4bbd67cff`).
- Echte DB vor und nach allem `677CAFD8109F`.

**Lauf:**

- Methode wie §71–§74: isolierte QA-Daten, OCR an, alles über die Oberfläche, Anfragen nur an den lokalen
  Aufzeichnungsserver mit Testschlüssel.
- Zusätzlich die synthetische QA-Akte `QA75-FRISTEN`. Sie ist nicht Teil des Benchmarks und ändert keine Referenz; ihre
  Erwartung war vor dem Lauf festgelegt.
- Nachweise: `C:\Users\Bonit\lexono_qa_s65\qa_nachweise\s75_legal_benchmark\`.

| Fall | Ergebnis (gespeicherte Art = UI-Typ = Datumsfeld) | Hinweis „mehrere“ |
|---|---|---|
| QA75-FRISTEN | 06.03. und 13.03. (verschiedene Fristen in einem Satz): Frist / „Fällig am“; 23.03. und 24.03. (vorsorglich/spätestens): Frist / „Fällig am“; 4/4 | 06.03./13.03. **nein**, 23.03./24.03. **ja**, wie erwartet, gespeichert und in der UI |
| LE-D002 | 13.04. Frist / „Fällig am“; 30.06. **Beendigungstermin / „Beendigung zum“ / „Möglicher Beendigungstermin (kein Fristende)“**, ohne Wirksamkeitsaussage; Aktenseite nennt beide Arten | nein |
| LE-D009 | 4/4 Fristen „Fällig am“; 31.10. (3×) „Beendigung zum“ | nein (verschiedene Fristen in benachbarten Zeilen) |
| LE-D005 | 28.04./04.05. Frist | ja (konkurrierend) |
| LE-T004 | 05.02./06.02. Frist; 30.04. Beendigungstermin / „Beendigung zum“ (Test-Split ohne Beendigungsreferenz, unverändert) | 05.02./06.02. ja |

In allen Fällen gilt:

- Prüfstatus „ungeprüft“.
- Extraktion vollständig.
- Payload: 0 Lecks, 0 Werte fremder Akten, nur Testschlüssel und `/v1/messages` des Aufzeichnungsservers.
- Die Aktenseite wurde zusätzlich mit einem App-Start ohne Anfrage geprüft (`ergebnis_lauf_AKTE-LE-D002.json`,
  `aktenseite_fristen.png`).
- Nach den Läufen bleiben keine Lexono-, Test- oder QA-Prozesse zurück.

### 75.6 Offen und nicht verifiziert

- Der Export mit `kind` ist nur im Repository geprüft (§74, PASS); in der App **NICHT VERIFIZIERT**.
- Die Typ-Spalte der Liste kürzt „Beendigungstermin“ auf „Beendigu…“ (schmale Spalte). Detail und Tooltip zeigen den
  vollen Text.
- Die Alternativsignale sind eine geschlossene Liste. Andere Formulierungen konkurrierender Fristenden („alternativ“,
  „im Fall …“) erhalten den Hinweis nicht. Das ist kein Fristverlust, aber ein fehlender Hinweis.
- Juristische Korrektheit echter Modellantworten und reale Kanzleitexte: **NICHT VERIFIZIERT**.

**Kein Beweis:** Keine Aussage über vollständige juristische Zuverlässigkeit oder vollständige Leckfreiheit. Der
Datenschutzstatus bleibt „teilweise nachgewiesen“.

## §76 Typ-Label, Alternativformeln, Export, Konzept Antwortqualität (18.10.2026, Branch `feature/feedback-diagnose`, Code `65ea0b0`)

Ausgangsstand: `6a16014` (Doku §75) auf Code `ca695d8`, synchron mit dem Remote; `main` unverändert (`5735e5b`).

### 76.1 Listenansicht: Typ-Label vollständig

- **Ursache:** Die Tabelle hat `table-layout: fixed` mit einer Typ-Spalte von 9 %, damit bei geöffnetem Detailpanel
  nichts horizontal scrollt (Beschluss vom 03.10.). Mit `td { overflow: hidden }` wurde „Beendigungstermin“ zu
  „Beendigu…“ abgeschnitten.
- **Änderung:** Die Spaltenbreiten bleiben gleich. Das Label (`.task-type-cell__label`) darf an einer weichen
  Trennstelle umbrechen („Beendigungs-⏎termin“, U+00AD, `overflow-wrap: anywhere`, `hyphens: manual`). Der Tooltip und
  `data-type-label` tragen den vollen Text.
- Detail und Bearbeitungsformular bleiben wie in §75: „Beendigung zum“ beim Beendigungstermin, „Fällig am“ bei Fristen.
- **Repository-Tests:** Label vollständig, mit weicher Trennstelle und Tooltip; die CSS-Regel ist vorhanden.
- **Installierte App:** schmale Fenster und Detailpanel **NICHT VERIFIZIERT** (76.5).

### 76.2 Alternativformeln mit Kontext

- **Neue Signale:** „alternativ“, „bzw.“, „beziehungsweise“, „im Fall(e)“, „je nach“, „falls“, „sofern“. Sie gelten
  weiterhin **nur** mit der Kontextbedingung aus §75: Zwischen den Daten steht kein anderer Gegenstand. Wörter der
  Alternativformeln selbst und Genitivformen („Alternativ“, „Fall“, „Zugangs“) zählen nicht als Gegenstand.
- **Bisher unerkannte Fristenden, behoben:**
  - Ein Datum nach „oder / bzw. / alternativ / hilfsweise / sonst / andernfalls“ ist ein Fristende, wenn das
    unmittelbar davor genannte Datum selbst eines ist. So wird „Fristende 05.03.2026, alternativ (bei Zugang am …)
    06.03.2026“ erkannt.
  - Ist das vorige Datum ein Fristbeginn, ein Dokumentdatum oder ein Termin, entsteht kein Fristende: „Zugang am
    12.02.2026 oder 13.02.2026“, „Schreiben vom …“.
  - „Fristende je nach/bei <Wort> <Datum>“ zählt als ausdrückliches Fristende.
- **Korpus:** In **keinem** Dokument ändern sich Vorschläge, Begründungen oder Hinweise gegenüber §75.
- **Tests:** `tests/test_s76_alternatives_list_export.py` mit 21 Tests:
  - 6 Alternativformeln, beide Fristenden erkannt und markiert;
  - 6 unabhängige Fristen, alle erkannt, kein Hinweis: „alternativ Ratenzahlung“, „falls Zahlung erfolgt, Frist zur
    Rücknahme“, satzbeginnendes „Alternativ …“, benachbarte Zeilen, „Im Fall der Zahlung …“;
  - 5 Fälle mit Zustellung, Vermerk, Fristbeginn-Alternative, Dokumentdatum und Termin;
  - bestehende Alternativen unverändert;
  - Liste und Export.

### 76.3 Export

- **Befund:** Seit §74 enthält das Manifest `kind` (z. B. `beendigung`). Für eine Prüfung war das nur eingeschränkt
  nachvollziehbar, denn der Export enthielt keine Bezeichnung, keine Begründung und keine Konfidenz.
- **Änderung (additiv):** `kind_label` („Möglicher Beendigungstermin (kein Fristende)“), `reasoning` (mit „kein
  Fristende“ und der fehlenden Wirksamkeitsaussage) und `confidence`. Der Export ist wie bisher ein unpseudonymisierter
  interner Datenauszug; `source_text` war schon enthalten.
- **Pfade:** Seite „Sicherung“ (`/dashboard/backup/matter/<id>`, Admin) und Mandanten-Datenauszug. Beide verwenden
  `MatterExportService.build_manifest`.
- **Repository-Test:** PASS.
- **Installierte App:** **NICHT VERIFIZIERT** (76.5).

### 76.4 Konzept für den Antwortqualitäts-Benchmark

Neuer Bereich `benchmarks/legal_answers_v1/` mit README, `rubric.json`, `specs/dev/LA-D005.json`,
`specs/dev/LA-D006.json` und `specs/test/LA-T004.json` (eingefroren). Dazu `scripts/legal_bench/answer_specs.py` und
`tests/test_legal_answer_specs.py` mit 17 Tests.

- **Kein Lauf:** Es wurden keine Modellantworten abgerufen, es gab keine Cloud-Aufrufe und keine Kosten.
- **Quellen:** Nur die quellenbasierten Fälle mit freigegebenen Quellen (Rechtsprechung im Internet, Gesetze im
  Internet).
- **Nicht verwendet:** OpenRewi (ShareAlike), ZJS und Moot Courts. Keine fremden Falllösungen.
- **Rubrik mit sieben getrennten Dimensionen:**
  - Ergebnis;
  - Rechtsgrundlagen;
  - Tatsachen;
  - Fristen;
  - Unsicherheiten;
  - unbelegte Behauptungen;
  - gefährliche Fehler (disqualifizierend).

  Ergebnis, unbelegte Behauptungen und gefährliche Fehler sind als **juristische Prüfung** gekennzeichnet, die übrigen
  als teilautomatisch (Vorprüfung als Hilfe, Note durch eine qualifizierte Person).
- **Spezifikationen, vorab festgelegt:**
  - zulässige Antwortvarianten;
  - Rechtsgrundlagen (müssen in der Referenzlösung und im Aktentext stehen);
  - entscheidende Tatsachen (mindestens eine Variante entspricht einem Aktenwert);
  - Fristen (müssen in der Referenz stehen);
  - Unsicherheiten;
  - gefährliche Fehler mit Verdachtsformulierungen;
  - typische Fehlentscheidungen.

  Status: `entwurf_juristische_freigabe_offen`.
- **Versionierung:**
  - `case_fingerprint` (SHA-256 über den kanonischen JSON-Inhalt des Falls): Eine geänderte Referenz wird erkannt.
  - `rubric_version`, `version`.
  - Der Test-Split ist eingefroren; das prüft der Validator.
- **Vorprüfung:** nur Hinweise, keine Note. Ein Test belegt die Grenze des Wortlautvergleichs: „Vorkaufsrecht **nicht**
  zu“ trifft die Ergebnisvariante und den Verdacht zugleich. Fundstellen außerhalb von Akte und Quelle werden zur
  Prüfung gelistet, ihre Existenz ist damit nicht widerlegt.
- **Grenzen:** Drei Fälle, keine statistische Aussage. Synthetische Akten aus amtlichen Leitsätzen. Die Bewertung hängt
  von qualifizierten Personen ab. Juristische Zuverlässigkeit: **NICHT VERIFIZIERT**.

### 76.5 QA

- **Volle Suite** (sechs Teile, Vordergrund): **5626 PASS** (§75: 5588, dazu 21 + 17 neue Tests).
  - **4 FAIL, bereits bekannt:** `test_design_refresh` mit 4 Logo-Tests; die fremden Logo-Dateien fehlen in der
    Arbeitskopie.
  - Dazu 10 xfailed, 1 xpassed (bekannt), 1 skipped.
- **Benchmark** `legal_e2e_v1`: in **allen** Kennzahlen identisch mit §75, abgesehen von Laufzeit und Payloadgröße.
  - dev: 0 kritisch / 0 Lecks / 0 Werte fremder Akten; Extraktion 276/276, Chronologie 50/50, keep 54/54, Fristen
    22/22, Beendigung 2/2.
  - test: Extraktion 110/110, Chronologie 22/22, keep 25/25, Fristen 5/5.
  - Lösungsgrundlage dev 4/4, test 2/2.
- **Build:** `65ea0b0`, PyInstaller und Inno Setup Exit 0. Extraktor (51 Funktionen), Export (5) und Aufgaben-Router
  (26) sind bytecode-gleich mit `65ea0b0`, Template und CSS enthalten die Änderung. SHA-256 der Build-EXE:
  `b8e9f368db70245162e8d7a13cd2367d1319b8c659b444b78ed559a7e5985953`. Installer `ca695d8` archiviert.
- **Installation und App-Test: NICHT VERIFIZIERT – blockiert** (Stand 20:40; überholt, siehe 76.7).
  - Bei der Vorprüfung lief die reguläre Kanzlei-Instanz: `Lexono.exe serve`, gestartet 18.10.2026 20:21 über
    `cmd.exe … >> C:\ProgramData\Lexono\app.log`, mit der echten Datenbank. Deren Hash war deshalb nicht lesbar
    (Datei in Benutzung).
  - Der Installer würde die laufende EXE ersetzen. Die Instanz wurde **nicht** beendet und **nicht** überinstalliert.
    Die echte Datenbank wurde nicht berührt.
  - Installiert bleibt `ca695d8` (§75).
  - Das QA-Skript für Listenbreiten, Detailpanel, Export über das Formular der Seite „Sicherung“ und Payloads liegt
    bereit und wird nach dem Schließen der Instanz ausgeführt.

### 76.6 Offen

- ~~App-Test von Liste, Export und Payload~~ – nachgeholt, PASS (76.7).
- Juristische Freigabe der Spezifikationen durch eine qualifizierte Person. Ein Lauf mit echten Modellantworten
  braucht die Freigabe des Owners und einen Kostenrahmen.
- **NICHT VERIFIZIERT:** juristische Korrektheit echter Modellantworten, reale Kanzleitexte.

**Kein Beweis:** Keine Aussage über vollständige juristische Zuverlässigkeit oder vollständige Leckfreiheit. Der
Datenschutzstatus bleibt „teilweise nachgewiesen“.

### 76.7 Nachtrag: Installation und App-Test (18.10.2026, nachdem die Kanzlei-Instanz um 20:47 regulär geschlossen wurde)

Die Kanzlei-Instanz wurde abgewartet, nicht beendet. Erst danach folgte die Vorprüfung, mit diesem Ergebnis:

- keine Instanz und keine QA-Prozesse;
- Sicherungsziel frei;
- echte DB `677CAFD8109F`, unverändert seit 13.10.

**Drei Installationszyklen, jeweils Sicherung (956/956), stilles Update (Exit 0), EXE byte-identisch, DB unverändert:**

| Build | Sicherung | installierte EXE (SHA-256) | Ergebnis App-Test |
|---|---|---|---|
| `65ea0b0` | `lexono_install_backup_vor_65ea0b0` | `b8e9f368…5953` | Export, Arten und Payload PASS. **Liste FAIL:** bei 1024 px mit Detailpanel Titel/Akte/Typ/Status abgeschnitten (Breakpoint lag bei 900 px, seit 06.10.); bei 1366 px Umbruch mitten in der Silbe (`overflow-wrap: anywhere`) |
| `c3787af` | `lexono_install_backup_vor_c3787af` | `280293ee…6dfc` | 1024 px behoben. **FAIL:** bei 1280 px mit Panel war „Beendigungstermin“ abgeschnitten; bei innerWidth 1279 griff `max-width: 1279px` in WebView2 nicht (Bruchteil-Breite) |
| `8291158` | `lexono_install_backup_vor_8291158` | `280293ee…6dfc` (nur CSS geändert, liegt außerhalb der EXE in `_internal`; installiertes CSS geprüft) | **PASS** |

Dazu kamen zwei Korrekturen:

- **`c3787af`:** Das Label bricht nur an weichen Silbentrennstellen um („Be-en-di-gungs-ter-min“, `overflow-wrap: normal`).
- **`8291158`:** Das Detailpanel wird unter **1366 px** unter die Liste gestapelt, mit `max-width: 1365.98px` (bisheriges Muster, vorher erst unter 900 px). Die Tabelle ist neben dem Panel für ≥ 1366 px ausgelegt.

Die Installer `65ea0b0` und `c3787af` sind archiviert.

**Abschließender App-Lauf (Build `8291158`, WebView2):** Fälle LE-D002, LE-D009, QA75-FRISTEN und LE-D005, isolierte QA-Daten, OCR an. Nachweise unter
`C:\Users\Bonit\lexono_qa_s65\qa_nachweise\s76_legal_benchmark\lauf4\`; die Läufe 1–3 sind als Befundverlauf erhalten.

- **Liste:** Geprüft bei 1920, 1440, 1366, 1365, 1280, 1024 und 800 px, jeweils mit und ohne Detailpanel. Abgeschnittene
  Labels wurden über die Text-Rechtecke gegen den Clip-Bereich der Zelle gemessen.
  - In **allen 56 Kombinationen** (4 Fälle × 7 Breiten × mit/ohne Panel): 0 abgeschnittene Labels, kein horizontales
    Scrollen, Text vollständig.
  - Ab 1366 px stehen Liste und Panel nebeneinander, darunter gestapelt.
  - Screenshots `*_liste_<Breite>[_panel].png` zeigen „Be-endi-gungs-ter-min“ bei 1366 px mit Panel; bei 1280 px liegt
    das Panel unter der Liste. **PASS**
- **Detail und Formular:** Gespeicherte Art = UI-Typ = Datumsfeld („Beendigung zum“ bzw. „Fällig am“) für jeden
  Vorschlag aller vier Fälle. **PASS**
- **Export:** über das echte Formular der Seite „Sicherung“ (`/dashboard/backup/matter/<id>`), Antwort 200
  `application/zip`. Gespeichert unter `<Fall>_export.zip`. Je Fall stimmt die Anzahl der Einträge mit der DB überein
  (2, 10, 4, 4). In jedem Eintrag entspricht `kind` der gespeicherten Art, `reasoning` ist identisch mit der Datenbank,
  dazu `kind_label` (z. B. „Möglicher Beendigungstermin (kein Fristende)“), `confidence` und `review_status`
  „unreviewed“. **PASS**
- **Hinweis „mehrere mögliche Fristenden“:** QA75-FRISTEN 06.03./13.03. ohne, 23.03./24.03. mit Hinweis, wie erwartet;
  LE-D005 wie bisher. **PASS**
- **Payload:** Anfragen nur an `/v1/messages` des lokalen Aufzeichnungsservers mit Testschlüssel; 0 Lecks, 0 Werte
  fremder Akten in allen Chat- und Schriftsatz-Anfragen. **PASS**
- **Volle Suite nach den Korrekturen:** 5626 PASS, 4 FAIL (bekannte fremde Logo-Tests).
- **Abschluss:** echte DB `677CAFD8109F`; keine Lexono- oder QA-Prozesse zurückgeblieben.

Abschnitt 76.5 „Installation und App-Test: NICHT VERIFIZIERT – blockiert“ ist damit **überholt**.

**Weiterhin NICHT VERIFIZIERT:** juristische Korrektheit echter Modellantworten (Konzept 76.4, kein Lauf), reale
Kanzleitexte.

## §77 Audit und Laufvorbereitung des juristischen Antwort-Benchmarks (18.10.2026, Branch `feature/feedback-diagnose`, Ausgangsstand `c31fb99`)

**Audit und Vorbereitung, kein Lauf.** Es wurden keine Modellantworten abgerufen, keine Cloud-Aufrufe ausgeführt und
keine Kosten verursacht. Ausführlicher Bericht: `benchmarks/legal_answers_v1/AUDIT_S77.md`; Checkliste:
`benchmarks/legal_answers_v1/FREIGABE_CHECKLISTE.md`.

**Ausgangsstand geprüft:** HEAD = Remote = `c31fb99`. Die fünf §76-Commits liegen seit `6a16014` in dieser Reihenfolge
vor: `65ea0b0`, `b8e3439`, `c3787af`, `8291158`, `c31fb99`. `main` ist unverändert (`5735e5b`).

| Prüfschritt | Status |
|---|---|
| Quellen und Nutzungsrechte (nur RII/GII `nutzbar`, Fälle `abgeleitet` mit ECLI, synthetische Akten) | **PASS** |
| Test-Split `legal_e2e_v1/cases/test` unverändert seit `fc9d3f4` (kanonische Hashes = Ausgangsstand), jetzt gesperrt | **PASS** |
| Audit der Spezifikationen | **7 nachweisbare Fehler (N1–N7) gefunden und technisch behoben**; 6 Grenzen/Vorschläge (V1–V6) dokumentiert |
| Validator v2 gegen Spezifikationen v2 inkl. Sperre | **PASS** |
| Laufvorbereitung (`check`, `plan`, Antwortdatensatz, blinder Prüfbogen, Zusammenfassung) | **PASS** (technisch) |
| Freigabe-Checkliste der Vorlage | **LAUF NICHT FREIGEGEBEN** (22 offene Punkte), wie erwartet |
| Juristische Freigabe (J1–J6) | **NICHT ERTEILT** – offen, nicht durch den Agenten |
| Tatsächliche Modellqualität | **NICHT VERIFIZIERT** – keine Modellantwort vorhanden |
| Volle Suite | **5664 PASS**; 4 FAIL = bekannte fremde Logo-Tests (`test_design_refresh`) |
| App-Build/Installation | **nicht nötig** – kein App-Code geändert |

**Nachweisbare Fehler (behoben):**

- **N1:** Die Vorprüfung suchte Begriffe als Teilwort. „zu“ traf „**Zum** Vorkaufsrecht kann ich nichts sagen“; das ergab
  einen falschen Ergebnis-Hinweis.
- **N2:** Sieben entscheidende Tatsachen der Referenzlösungen hatten kein Kriterium: LA-D005 F3/F5, LA-D006 F2–F4,
  LA-T004 F3/F5/F7.
- **N3:** Offener Punkt O2 war in allen drei Spezifikationen nicht eigenständig, O1 und O2 waren vermischt.
- **N4:** Eine verneinte Zuständigkeit löste in LA-T004 keinen Verdacht aus.
- **N5:** Die Rubrik hatte keine Regel gegen den Ausgleich kritischer Fehler und keine Regeln für n/a, Alternativen und
  Unentscheidbares.
- **N6:** Test-Spezifikation und Rubrik waren nicht gegen Änderung geschützt.
- **N7:** Es gab keine Änderungshistorie.

**Maßnahmen:**

- Suche als ganzes Wort und Mindestlänge für Begriffe.
- Abdeckungspflicht oder begründeter Ausschluss (`not_assessed`); neue Kriterien nur aus vorhandenen Aktenwerten.
- Ein Kriterium je offenem Punkt; Abgleich der Referenz-Fehlergebnisse.
- Rubrik v2: Ein bestätigter kritischer Fehler ergibt FAIL_KRITISCH ohne Ausgleich. Ohne festgelegte Schwellen gibt es
  nie PASS; Gewichte und Schwellen sind **OFFEN**.
- `LOCK.json` mit Neusperre nur per Begründung; Historie je Spezifikation.
- Alle Spezifikationsänderungen erfolgten **vor jedem Lauf**, ohne vorhandene Ergebnisse, und sind in `history`
  dokumentiert. Danach folgte die Erstsperre.

**Offene juristische Entscheidungen:**

- J1: Referenzlösungen;
- J2: Antwortvarianten und Verdachtsformulierungen, einschließlich der §77-Ergänzungen;
- J3: vertretbare Alternativergebnisse;
- J4: Gewichte und Schwellen;
- J5: bewertende Personen und Zweitprüfung;
- J6: n/a- und Unentscheidbarkeitsregeln.

**Freigabe-Checkliste C-1 bis C-17**, darunter:

- Modell und Version;
- Wiederholungen bzw. Aufrufzahl (derzeit 6 × Wiederholungen);
- Kosten- und Abbruchgrenzen;
- Datenschutz (Pflichtwerte fest, Freigabe offen);
- Ablage und Aufbewahrung;
- Ausführungsschritt über den pseudonymisierten Lexono-Pfad (nicht implementiert).

Alle Entscheidungen der Checkliste sind **OFFEN**.

**Nachweise:**

- `benchmarks/legal_answers_v1/audit/`:
  - `s77_ausgangshashes_c31fb99.json`;
  - `s77_validator_gegen_specs_v1.txt` (25 Befunde des gehärteten Validators an der §76-Fassung);
  - `s77_validator_gegen_specs_v2.txt`;
  - `s77_check_vorlage.txt`;
  - `s77_plan_vorlage.json`;
  - `s77_fingerprints.txt`.
- `LOCK.json`.
- Tests: `tests/test_s77_answer_benchmark_audit.py`, `tests/test_legal_answer_specs.py`.

**Kein Beweis:** Keine Aussage über die juristische Qualität eines Modells, über vollständige juristische
Zuverlässigkeit oder über vollständige Leckfreiheit. Der Datenschutzstatus bleibt „teilweise nachgewiesen“.

## §78 Benchmark-Freigabe und Datenschutzpfad adversarial geprüft (19.10.2026, Branch `feature/feedback-diagnose`, Ausgangsstand `307cecf`)

Nur technische Absicherung mit synthetischen Daten, temporären Kopien und lokalem QA-Recorder samt Testschlüssel. Kein
Modelllauf, kein Cloud-Aufruf, keine Kosten. Vollständiger Bericht: `benchmarks/legal_answers_v1/AUDIT_S78.md`.

- **Freigabesperre (`answer_run check`):**
  - §77-Fassung **FAIL** (G1): gelöschte Schlüssel, `false`/„nein“ als Freigabe und Unsinnswerte wurden freigegeben.
  - Gehärtet: Jedes Feld der Vorlage ist Pflicht, Werte werden regelgeprüft, Zusatzschalter, ein veralteter Commit und
    Kosten bzw. Aufrufe über dem Limit sind Verstöße.
  - 36/36 Manipulationen werden abgelehnt, die Positivkontrolle wird freigegeben. **PASS**
- **Sperre (LOCK.json):**
  - Lücken L1–L4 geschlossen: ungültiges JSON, Handbearbeitung (`entries_sha256`), alte/neue Hashes in der Historie,
    Registereinträge der Test-Quellen.
  - Die Neusperre ist begründet und dokumentiert; die Originale sind byte-gleich.
  - Grenze: Wer Lock und Historie gemeinsam fälscht, fällt nur in Git auf. **PASS**
- **Aufrufplan:** reproduzierbar, versioniert über `plan_fingerprint`, offline (Sockets gesperrt), ohne Cloud-Bibliothek;
  ohne Freigabe nicht ausführbar (Exit 2). **PASS**
- **Datenschutzpfad bis zur Leitung:**
  - LE-D005, LE-D006 und LE-T004 laufen über die echte Pipeline, den echten Anthropic-Provider und das SDK an den
    lokalen Recorder: 7 Anfragen, 0 Lecks eigener oder fremder Akten, nur der Testschlüssel.
  - Fail-Closed: Fällt die Namenserkennung aus, gehen 0 Anfragen hinaus. **PASS**
  - **Befund B1:** Der Schriftsatzpfad reicht die Ausnahme durch, statt geordnet zu blockieren. Er bleibt fail-closed,
    aber ohne Grund; offen, App-Änderung.
- **NICHT VERIFIZIERT:** TLS und realer Endpunkt, Relay-Server, die spätere Benchmark-Ausführung (C-14, nicht
  implementiert) und die Erkennung jenseits dieser Akten. Datenschutzstatus: **teilweise nachgewiesen**.
- **Nachweise:** `benchmarks/legal_answers_v1/audit/s78_*`; Tests `tests/test_s78_benchmark_gate.py` und
  `tests/test_s78_wire_privacy.py` (84).

## §79 B1 behoben: geordnete Blockierung bei Ausfall der Namenserkennung; Ausführungswächter des Benchmarks (19.10.2026, Branch `feature/feedback-diagnose`, Fix `bfbe53a`)

Kein Modelllauf, kein Cloud-Aufruf, keine Kosten. Vollständiger Bericht: `benchmarks/legal_answers_v1/AUDIT_S79.md`.

- **Ursache B1:** `ClaudePrivacyGateway.prepare_request` fing Fehler nur in der Kandidatensuche fail-closed ab. Eine
  Ausnahme der Erkennung in Pseudonymisierung oder Restrisiko-Scan stieg auf; Schriftsatz lieferte HTTP 500, Chat den
  allgemeinen technischen Fehler. Gesendet wurde nie etwas.
- **Behebung:** Beide Schritte im bestehenden fail-closed-Muster: `GatewayResult(allowed=False)` mit festem,
  inhaltsfreiem Grund, Kategorie `privacy_check_failed`, verständliche Meldung („Die Datenschutzprüfung ist
  fehlgeschlagen … Es wurde nichts an die KI übermittelt“). Log nur Schritt und Fehlertyp. Alle Aufrufer (Chat,
  Schriftsatz inkl. Streaming, Orchestrator, Review) nutzen den vorhandenen Blockierungsweg. **PASS**
- **Installierte App (Build `bfbe53a`, EXE `01bff911…99b9`, Sicherung `lexono_install_backup_vor_bfbe53a` 956/956,
  echte DB `677CAFD8109F` unverändert):** echter Ausfall durch umbenanntes spaCy-Modell in einer byte-gleichen Kopie:
  Chat und Schriftsatz zeigen die Meldung, **0 Anfragen** am Recorder. Normalbetrieb: 2 Anfragen, 0 Lecks. Vorher-Beleg
  im alten Build: HTTP 500. **PASS**
- **Benchmark:** `scripts/legal_bench/answer_exec.py` prüft vor **jedem** Provider-Aufruf Freigabe, Konfiguration,
  Commit, Lock, Plan, Limits, Siegel und Leaks und bricht fail-closed ab (14 Tests, lokaler Recorder). **PASS**
  Vollständige Ausführung: **NICHT VERIFIZIERT – bewusst nicht gebaut** (E1 Treiber, E2 Ablage, E3 Ist-Kosten,
  E4 Fortsetzen, E5 offene Freigaben).
- **QA:** volle Suite 5626 PASS, 4 FAIL (bekannte fremde Logo-Tests); neue §78/§79-Dateien 109 PASS.

## §80 Benchmark-Treiber, sichere Ablage, Kostenkontrolle, Wiederanlauf (19.10.2026, Branch `feature/feedback-diagnose`, Basis `0eba65c`)

Kein Modelllauf, kein Cloud-Aufruf, tatsächliche Kosten 0 EUR. Bericht: `benchmarks/legal_answers_v1/AUDIT_S80.md`.

- **E1:** `answer_driver.BenchmarkDriver` importiert die synthetischen Akten in eine In-Memory-DB und löst je geplantem
  Aufruf den regulären Chat bzw. Schriftsatz aus; `precheck` vor dem Workflow, `authorize` vor dem Provider. Recorder-
  Lauf (echter Provider, 127.0.0.1): 6 Anfragen, 0 Lecks. Echte Endpunkte gesperrt (`RealCloudDisabled`). **PASS**
- **E2:** `answer_store.RunStore` außerhalb von Repository und Lexono-Daten, Konto-ACL (1 Konto), Hashes je Datei,
  `verify`, kontrollierte Löschung. Abgelegt wird die **pseudonymisierte** Modellantwort; 0 geschützte Werte, kein
  Schlüssel in der Ablage. **PASS**
- **E3:** Kostenbuch trennt Schätzung und tatsächliche Kosten (nur Provider-Usage × freigegebener Tokenpreis, Schema 2);
  fehlende Metadaten oder Providerfehler = Kosten UNBEKANNT = kein weiterer Aufruf; Restbudget vor jedem Aufruf. **PASS**
- **E4:** Zustand dauerhaft; nach Absturz zwischen Aufrufen nur fehlende (0 Doppelaufrufe); Absturz während eines
  Aufrufs nie automatisch wiederholt; Stopp mit Diagnose bei geänderter Konfiguration, Commit, Freigabe, LOCK, Plan. **PASS**
- **QA:** volle Suite 5626 PASS, 4 FAIL (bekannte fremde Logo-Tests); §78–§80-Dateien 142 PASS.
- **Offen:** C-1–C-17, J1–J6; echter Lauf NICHT VERIFIZIERT und gesperrt.

## §81 Sicherheitsprüfung und Freigabevorbereitung des Antwort-Benchmarks (19.10.2026, Branch `feature/feedback-diagnose`, Basis `82e5d28`)

Externe Provideraufrufe 0, Kosten 0 EUR. Bericht: `benchmarks/legal_answers_v1/AUDIT_S81.md`.

- **Freigaben:** 10 von 15 Manipulationen wurden vorher freigegeben (Datum Zukunft/veraltet/unmöglich, kopierte Owner-
  Freigabe, Zusatzfeld `signatur`, juristische Freigabe trotz offener Spezifikationen bzw. fehlender Schwellen,
  unendliches Budget/Preis, Schätzung unter maximaler Ausgabe). Jetzt alle abgelehnt; Owner-Freigabe plangebunden.
  Mit dem heutigen Repository-Stand ist **keine** Konfiguration freigebbar. Signaturen nicht implementiert (kein
  Vertrauensanker) - Owner-Entscheidung F-1. **PASS**
- **C-7:** endliche Werte, unplausible Usage = UNBEKANNT, Aufrunden auf 1e-6 EUR. **OFFEN** (Preise, Währung,
  Zusatzgebühren, Abgleich).
- **C-11:** sichere IDs, erweiterte ACL und halb geschriebener Zustand werden erkannt, Junction ins Repository abgelehnt.
  Verschlüsselung bewertet (DPAPI/EFS/BitLocker/anwendungsseitig), nicht umgesetzt. **OFFEN**
- **E4:** `resolve_unclear` mit Person, Rolle, Begründung und Beleg; Datenschutzbefund beendet den Lauf dauerhaft
  (Lücke: wurde beim Wiederanlauf nicht fortgeführt). **PASS**
- **C-14:** **OFFEN**; Entscheidungsliste im Bericht.
- **QA:** volle Suite 5626 PASS, 4 FAIL (bekannte fremde Logo-Tests); §78–§81-Dateien 198 PASS.
