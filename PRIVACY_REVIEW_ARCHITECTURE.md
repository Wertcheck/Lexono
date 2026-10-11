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
