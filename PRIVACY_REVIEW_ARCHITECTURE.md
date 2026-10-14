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
- Eine E-Mail von der bekannten Mandantenadresse erreicht allein 0,3 Punkte, also nicht einmal die Prüfschwelle von
  0,4. Sie erscheint deshalb ohne Zuordnungsvorschlag.

**QA Etappe 9:**
- Matching-, Inbox- und Benchmark-Tests grün.
- Volle Suite: 4446 bestanden, nur die 4 bekannten Logo-Fehler.
