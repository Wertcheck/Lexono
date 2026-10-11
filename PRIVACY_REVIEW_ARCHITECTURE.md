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
