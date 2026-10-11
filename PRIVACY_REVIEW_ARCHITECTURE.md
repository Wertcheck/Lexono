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
