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
