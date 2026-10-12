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
