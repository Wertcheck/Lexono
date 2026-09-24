# LEXONO CHAT INTELLIGENCE — ARCHITECTURE DECISION MEMO

**Task:** CHAT-INT-DIAG · **Datum:** 15.09.2026 · **Status: RED**
**Rollen:** `agents/chat`, `agents/local_ai`, `agents/ai_quality`, `agents/architecture`
**Art:** Reine Diagnose. Kein Produktionscode geändert.

**Messbasis (explizit):**
- Code/Pipeline: Repo `C:\Users\Bonit\Lexono`, Working Tree (uncommitted).
- Laufzeitmessungen: eigene In-Memory-SQLite, **nicht** die installierte Instanz.
  Die installierte DB (`C:\ProgramData\Lexono\data\kanzlei_ai.db`) wurde nur
  lesend für Struktur-/Namensbefunde herangezogen.
- Local AI: **echtes Ollama, `qwen3:8b`** (wie `C:\ProgramData\Lexono\.env`).
- Cloud AI: **Spy statt echtem Claude** — Cloud-Netzlatenz ist deshalb in den
  Zahlen NICHT enthalten und wird nirgends behauptet.

---

## A. Aktueller tatsächlicher Flow

Eine Chatnachricht durchläuft real (belegt durch Code + Spy-Lauf):

```
UI (chat.html / app_chat_stream.js)
 → POST /dashboard/chat/send  bzw.  /send-stream   (app/web/chat_router.py:345/487)
 → ChatService.record_user_message()      → ChatMessage(role="user") persistiert
 → ChatService.send_message()             (app/chat/service.py:~470)
    ├─ FAST PATH 1: _looks_like_pure_norm_question()  → reine "§ N Gesetz"-Frage
    │     → _find_law_section() → _format_norm_answer()
    │     → KEIN Presidio, KEIN Local AI, KEIN Claude.  ENDE.
    ├─ drafting_service is None → Standardtext "Cloud-KI nicht konfiguriert". ENDE.
    └─ trace.step("routing"): purpose = formulate_draft | chat_response
       → DraftingService.create_draft(matter_id, purpose, attorney_anmerkungen=content)
          ├─ local_ai.prepare_draft_context(matter_id)   ← AKTE+DOKUMENTE, nie ChatMessage
          ├─ trace("retrieval")        Rechtsquellen + Kanzleiwissen
          ├─ trace("privacy_gateway")  Presidio → ClaudeRequestPayload (7 Felder)
          ├─ Kostenkontrolle
          ├─ trace("local_ai_preanalysis")  Ollama fasst Sachverhalt zusammen
          │     → Ergebnis wird als zusätzlicher Argumentationspunkt angehängt
          ├─ trace("claude")           writing_provider.write(payload)
          ├─ trace("validation")       validate_claude_response()  ← Stufe 1 + 2
          └─ trace("reconstruction")   Platzhalter → Klartext (lokal)
 → ChatMessage(role="assistant") persistiert → UI
```

**Zentrale Strukturaussage:** Der Chat hat **keine eigene Chat-Pipeline**. Er ist
ein Aufrufer der **Drafting**-Pipeline. Es gibt kein `messages`-Array, keine
Rollenabwechslung, keinen Dialogzustand — nur ein einmalig gebautes Formular.

## B. Conversation-Memory-Befund — **Das Modell bekommt den Verlauf NICHT.**

Belegt zur Laufzeit (`payload_spy.py`, Spy auf `ClaudeWritingProvider.write`),
4-Turn-Dialog in EINER Konversation:

| Turn | Nachricht | `anonymisierte_anwaltliche_anmerkungen` im Payload |
|---|---|---|
| 1 | Hallo | `'Hallo'` |
| 2 | Ich habe eine Frage zum Steuerrecht. | `'Ich habe eine Frage zum Steuerrecht.'` |
| 3 | Es geht um einen Steuerbescheid. | `'Es geht um einen Steuerbescheid.'` |
| 4 | Welche Frist gilt? | `'Welche Frist gilt?'` |

`conversation_id` blieb über alle 4 Turns identisch, 8 Nachrichten korrekt
persistiert (`user/assistant` × 4). Der **Payload von Turn 4** enthielt:

```
enthaelt 'Steuerrecht'   (Turn 2): False
enthaelt 'Hallo'         (Turn 1): False
enthaelt 'Steuerbescheid'(Turn 3): True   ← FALSCHER TREFFER
```

Der `Steuerbescheid`-Treffer stammt **nicht** aus Turn 3, sondern aus dem
`anonymisierter_sachverhalt` = `'Akte: Einspruch Steuerbescheid 2025'`, also
dem **Aktentitel**. Persistenz ist also korrekt — aber die Historie erreicht
das Modell an keiner Stelle.

**Warum strukturell unmöglich:** `ClaudeRequestPayload`
(`app/privacy/gateway_schema.py:27`) hat exakt 7 Felder und **kein** Feld für
Gesprächsverlauf, ausdrücklich „kein Freitext-Escape-Hatch". `create_draft`
(`app/drafting/service.py:177`) hat keinen History-Parameter.
`_build_sachverhalt` (`app/ai_providers/local_ai_provider.py:93-111`) baut den
Sachverhalt aus `Matter.title` + `Document.extracted_text` — `ChatMessage` wird
dort **nie abgefragt**.

Das ist kein Bug in einer Zeile, sondern eine fehlende Fähigkeit der Architektur.

## C. Local-AI-Befund — **Local AI ist NICHT der Chatgenerator.**

| Aspekt | Befund |
|---|---|
| Trigger | jeder Nicht-Fast-Path-Request, wenn `local_llm_provider` injiziert (Prod: `LOCAL_AI_ENABLED=true`) |
| Input | die bereits pseudonymisierte `ClaudeRequestPayload` |
| Output | Fließtext-Zusammenfassung → angehängt als Argumentationspunkt mit Präfix `"Lokale Vorabanalyse (automatisiert, Ollama, keine rechtliche Bewertung): "` |
| Modell | `qwen3:8b` (installierte Instanz) |
| Zweck | (1) Vorabanalyse/Zusammenfassung, (2) Validierungs-Stufe 2 |
| Generative Chatrolle | **KEINE** |
| Latenz gemessen | **44,9 s cold / 10,1–11,1 s warm** |
| Zwingend? | nur wenn `_should_skip_llm_privacy_layers()` False — siehe G |
| Cachebar? | ja, Input ist je Akte weitgehend stabil (nicht implementiert) |
| Parallelisierbar? | ja, unabhängig vom Claude-Aufruf (heute strikt sequenziell) |

Systemprompt (`ollama_provider.py:53`): „Du fasst einen bereits anonymisierten
Sachverhalt … zusammen." Für „Hallo" fasst das 8B-Modell 10–45 s lang eine
einzeilige Akten-Überschrift zusammen, die der Antwort nichts hinzufügt.

## D. Cloud-AI-Befund

Claude ist der **einzige** Generator. Systemprompt ist seit 13.09. korrekt
zweigeteilt (`CHAT_SYSTEM_PROMPT` vs. `WRITING_SYSTEM_PROMPT`,
`claude_writing_provider.py:112/46`) — der bekannte „Brief statt Antwort"-Fehler
ist dort bereits behoben. Der finale User-Prompt (Zeilen 225-244) ist jedoch ein
**Formular**:

```
Schreibauftrag: chat_response
Sachverhalt:
Akte: [MANDANT_01] offen 1
Anwaltliche Anmerkungen (…für diese Version):
Welche Frist gilt?
```

Kein Dialog, keine Vorgeschichte, keine Rollen. Selbst ein perfektes Modell kann
daraus keinen kontextfähigen Chat machen.

## E. Prompt-/Payload-Befund

Payload-Felder (Spy, alle 4 Turns identisch):
`schreibauftrag, gewuenschter_stil, anonymisierter_sachverhalt,
anonymisierte_argumentationspunkte, anonymisierte_quellenverweise,
schreibvorlage, anonymisierte_anwaltliche_anmerkungen`.

Zwischen den Turns änderte sich **ausschließlich** das letzte Feld. Die
Allowlist ist als Datenschutzmaßnahme korrekt und soll bleiben — sie ist aber
zugleich der Grund, warum Verlauf strukturell nicht durchkommt.

## F. Routing-Befund

Drei Pfade, alle regelbasiert (kein LLM-Routing):

1. **Norm-Fast-Path** — `_NORM_QUESTION_PATTERN`, streng verankert (`^…$`).
   Sauber gebaut, kein Claude/Presidio nötig. Funktioniert.
2. **Drafting** — `_DRAFTING_TRIGGER_PATTERN` (Verb + Dokumentsubstantiv).
3. **chat_response** — Default.

**Lücke:** Es gibt keine Klasse für *konversationelle* Nachrichten
(Begrüßung, Dank, Rückfrage). Diese landen auf `chat_response`, das die volle
Akten-/Retrieval-/Local-AI-/Validierungs-Maschinerie auslöst — für „Hallo"
fachlich sinnlos. Ein *Verhaltens*-Fast-Path fehlt (kein Hardcoding nötig:
es reicht, Akten-/Retrieval-Kontext wegzulassen, wenn kein Aktenbezug besteht).

## G. Performance-Befund (gemessen, `qwen3:8b`, Claude ge-spy-t)

| Szenario | Aktentitel | total | `local_ai_preanalysis` | `privacy_gateway` |
|---|---|---:|---:|---:|
| A1 „Hallo" COLD | „Steuersache 2025" | **48,07 s** | 44,92 s | 3,12 s |
| A2 „Hallo" WARM | „Steuersache 2025" | **10,11 s** | 10,06 s | 0,03 s |
| B1 „Hallo" COLD | „Muster, Anna offen 1" | **10,90 s** | 10,85 s | 0,03 s |
| B2 „Hallo" WARM | „Muster, Anna offen 1" | **10,78 s** | 10,72 s | 0,03 s |
| C „Welche Frist gilt?" | „Muster, Anna offen 1" | **11,15 s** | 11,09 s | 0,03 s |
| D „Hallo" **ohne** Local AI | „Muster, Anna offen 1" | **0,042 s** | — | 0,02 s |

**Local AI ist zu ~99 % der Latenz.** Ohne sie: 42 **Milli**sekunden.
Retrieval (0,003–0,007 s) und Persistenz sind irrelevant.

**Der Skip greift in der Praxis fast nie.** `_should_skip_llm_privacy_layers`
(`drafting/service.py:88`) verlangt `purpose==chat_response` **und** kein
Dokumentkontext **und** `mappings == []`, also **null** PII-Treffer in der
GESAMTEN Payload. In der echten Datenbank heißen Akten aber „Muster, Anna
offen 1", „Testmann, Paul erledigt 2" — der Mandantenname steht **im
Aktentitel**, erzeugt also immer ein Mapping. Selbst im neutral betitelten
Szenario A entstand ein `[PERSON_01]`. Ergebnis: die teure Stufe läuft praktisch
immer, auch für „Hallo".

Referenzwerte des Auftrags (≈2–3 s TTFR für einfache Antworten) werden um den
Faktor **4–20** verfehlt.

## H. Privacy-Befund — **GRÜN, unverändert wirksam**

| # | Invariante | Status | Beleg |
|---|---|---|---|
| 1 | Keine unpseudonymisierten PII über die Grenze | OK | 7-Feld-Allowlist, einziger Ausgang `gateway.prepare_request` |
| 2 | Mapping bleibt lokal | OK | `GatewayResult.mappings` verlässt den Prozess nie |
| 3 | Cloud erhält nur Zulässiges | OK | Spy: exakt 7 Felder, nichts sonst |
| 4 | Rekonstruktion lokal/deterministisch | OK | `trace("reconstruction")`, kein LLM |
| 5 | Kein LLM entscheidet über Mapping | OK | Stufe 1 deterministisch **vor** Stufe 2 |
| 6 | Fail-Closed | OK | s. u. |
| 7 | Performance-Fixes brechen nichts davon | einzuhalten | Auflage für alle Folge-Tasks |

**Regressionsfall „Frau Müller" ist intakt** (real ausgeführt):

```
sachverhalt: 'Frau Müller hat gestern angerufen. Sie wohnt in der Hauptstraße 5.'
→ allowed: False
→ reasons: ["Möglicherweise nicht erkannte Namen/Entitäten gefunden: ['Frau Müller', ...]"]
```

`tests/test_privacy_gateway.py`, `test_privacy_security_check.py`,
`test_privacy_detectors.py`: **84 passed**.

> Diese Diagnose schlägt **keine** Lockerung der Privacy-Grenze vor.

## I. Modell vs. Architektur — **Es ist ein Architekturproblem.**

Kontrollierter Vergleich, gleiche Eingaben, echtes `qwen3:8b`, direkter
`/api/chat` mit echtem `messages`-Array:

```
"Hallo"                       →  5,0 s  →  "Hallo! 😊 Wie kann ich dir heute helfen?"
Mehrturn (B,C,D mit History)  → 34,7 s  →  bezieht "Welche Frist gilt?" korrekt
                                            auf den Steuerbescheid aus Turn 3
```

Das lokale Modell kann **beides**, was Lexono heute nicht liefert: natürlich
begrüßen und Mehrturn-Kontext auflösen. Es scheitert nur, weil die Architektur
ihm die Historie nie gibt und seine Rolle auf „Zusammenfasser" begrenzt.

**Wichtige Differenzierung (nicht beschönigen):** Die inhaltliche Rechtsauskunft
im Mehrturn-Test war **sachlich falsch** („ein Jahr" statt ein Monat
Einspruchsfrist, § 355 AO). `qwen3:8b` ist für konversationelle **Form**
geeignet, für juristische **Substanz** nicht. Das bestätigt die bestehende
Rollenteilung: Rechtsinhalt über Claude + echte Rechtsquellen, nicht lokal.

→ **Kein Modellwechsel empfohlen.** `qwen3:8b` bleibt Referenz.

## J. Konkrete Root Causes

**RC-1 — Die Antwortprüfung verwirft jede natürliche Chatantwort. (P0)**
`app/privacy/security_check.py:229-244` (`check_placeholders_present`),
angewandt auf die **eingehende** Antwort über `check_response_placeholder_integrity`
(Zeile 280), verdrahtet in `app/drafting/service.py:539-584`.
Die Regel verlangt, dass **jeder** Platzhalter des Mappings in der Antwort
vorkommt. Isolierter Beweis:

```
BLOCKIERT | 'Guten Tag, wie kann ich Ihnen helfen?'        → [MANDANT_01] fehlt
BLOCKIERT | 'Hallo! Gerne helfe ich Ihnen bei …Fragen.'    → [MANDANT_01] fehlt
BLOCKIERT | 'Vielen Dank.'                                 → [MANDANT_01] fehlt
OK        | 'Sehr geehrte Damen und Herren, in der Sache [MANDANT_01] …'
```

Nur ein förmliches Schreiben besteht. Semantische Verwechslung: die Regel ist
laut eigenem Docstring „Vorgabe-Punkt 5" für **ausgehenden** Payload
(„haben wir wirklich alles ersetzt?"). Auf die **eingehende** Antwort angewandt
bedeutet sie etwas völlig anderes: „das Modell muss jeden Mandanten erwähnen".
`skip_semantic_check` überspringt nur Stufe 2 — Stufe 1 läuft immer.
**Das ist die direkte Ursache der Nutzerbeobachtung „Hallo wird nicht
zuverlässig beantwortet".**

**RC-2 — Gesprächsverlauf erreicht das Modell nie. (P0)**
`app/chat/service.py` `send_message` übergibt nur `content`;
`app/drafting/service.py:177` kennt keinen History-Parameter;
`app/privacy/gateway_schema.py:27` hat kein History-Feld;
`app/ai_providers/local_ai_provider.py:93-111` liest Akte+Dokumente statt
`ChatMessage`. Beleg: Spy-Tabelle in B.

**RC-3 — Der Chat läuft durch die Drafting-Pipeline. (P0, architektonisch)**
Ein Single-Shot-Formular statt eines Dialogs. Folge: kein Dialogzustand, und
jede Nachricht zahlt die vollen Drafting-Kosten (Retrieval, Vorabanalyse,
Validierung), auch wenn sie gar keinen Aktenbezug hat.

**RC-4 — Der Performance-Skip greift bei echten Daten praktisch nie. (P1)**
`drafting/service.py:88`: verlangt `mappings == []`. Echte Aktentitel enthalten
Mandantennamen → immer ein Mapping → Local AI läuft (10–45 s) auch für „Hallo".
Gemessen: mit Local AI 10,8 s, ohne 0,042 s.

**RC-5 — Technische Fehler werden als Datenschutz-Block angezeigt. (P2, klein)**
`app/privacy/api_logger.py:96`: unbekannte Gründe → `"unknown_block_reason"` →
nicht in `_FRIENDLY_BLOCK_MESSAGES` → Fallback `"Blockiert aus
Datenschutzgründen."` Real beobachtet: der interne Fehler „Interner Fehler bei
der Textproduktion" erschien dem Anwalt als Datenschutz-Blockade. Irreführend
und erschwert jede Fehlersuche im Pilotbetrieb.

**Nebenbefund (Doku vs. Code):** `agents/local_ai/AGENT.md` nennt
`qwen2.5:1.5b` als Standard; `app/config/settings.py:186` ebenso — die
installierte Instanz fährt jedoch `qwen3:8b` (`.env`). Drei Quellen, zwei
Wahrheiten.

## K. Quick Wins

| # | Maßnahme | Aufwand | Wirkung | Risiko |
|---|---|---|---|---|
| QW-1 | RC-5: eigene, ehrliche Meldung für technische Fehler | sehr klein | Diagnostizierbarkeit | sehr gering |
| QW-2 | Doku/Default `qwen3:8b` vereinheitlichen | sehr klein | Verwirrung weg | keine |
| QW-3 | Local-AI-Vorabanalyse überspringen, wenn kein Dokumentkontext **und** Sachverhalt nur aus Aktentitel besteht | klein | 10,8 s → ~0,1 s | mittel (Privacy-Begründung nötig) |

QW-3 **nicht** blind umsetzen: die heutige Bedingung ist bewusst konservativ.
Die tragfähige Formulierung ist „kein Dokumentkontext", nicht „keine Mappings" —
das braucht eine dokumentierte Sicherheitsbegründung + Tests.

## L. Notwendige Architekturänderungen

1. **Antwortprüfung nach Zweck differenzieren.** „Alle Platzhalter müssen
   vorkommen" gilt für `formulate_draft`, nicht für `chat_response`. Die
   sicherheitsrelevanten Teile von `check_response_placeholder_integrity`
   (unerwartete/veränderte Tokens, wiederaufgetauchte Originalwerte) bleiben
   **für beide** Zwecke Pflicht. **Nur die Vollständigkeitsforderung entfällt
   für Chat.** Kein Sicherheitsverlust: sie schützt nichts, sie erzwingt nur
   Erwähnung.
2. **Conversation History als eigenes Allowlist-Feld** —
   `anonymisierter_gespraechsverlauf: list[str]`, das **denselben**
   Gateway-Durchlauf nimmt wie alle anderen Felder. Kein Umgehungspfad, kein
   zweiter Privacy-Weg. Mit Turn-Limit + Zeichenbudget.
3. **Echte Request-Klassifikation** vor dem Pipelinebau (siehe unten), damit
   nicht jede Nachricht die maximale Pipeline zahlt.
4. **Local AI conditional + parallel + cachebar**, statt immer sequenziell.

### Request-Klassen → benötigte Komponenten

| Klasse | Presidio | Local AI | Retrieval | Cloud AI | Validierung | Warum |
|---|---|---|---|---|---|---|
| Begrüßung / Dank | ja¹ | nein | nein | ja | Teil² | Kein Akten-/Rechtsbezug; Presidio bleibt, falls der Nutzer doch einen Namen tippt |
| Einfache Konversationsfrage | ja | nein | nein | ja | Teil² | kein Dokumentkontext |
| Normabfrage („§ 558 BGB") | nein | nein | lokal | nein | nein | bereits gelöster Fast Path, amtlicher Text liegt lokal |
| Aktenfrage | ja | nein³ | ja | ja | voll | Aktenkontext, aber kein Dokumenttext nötig |
| Dokumentfrage / -analyse | ja | **ja** | ja | ja | voll | Dokumentinhalt verlässt die Grenze → volle Schutzkette |
| Faktenextraktion | ja | **ja** | ja | ja | voll | wie oben |
| Fristerkennung | ja | ja | ja | ja | voll | rechtsfolgenrelevant |
| Drafting / Einspruch | ja | **ja** | ja | ja | **voll inkl. Vollständigkeit** | formelles Schriftstück, Platzhalter müssen vorkommen |
| Komplexes Reasoning | ja | ja | ja | ja | voll | höchste Sorgfaltsstufe |

¹ Presidio ist billig (0,02–0,03 s warm) — nie als Performance-Maßnahme entfernen.
² „Teil" = Stufe 1 ohne Vollständigkeitsforderung (siehe L-1).
³ Wenn Dokumenttext einfließt, wird daraus automatisch „Dokumentfrage".

### Rollentrennungsmatrix

| Komponente | AKTUELLE Rolle | ZIELROLLE | Notwendigkeit |
|---|---|---|---|
| Presidio | Privacy (Erkennung) | unverändert | **Pflicht, immer** |
| Lokale Pseudonymisierung | Privacy (Ersetzung) | unverändert | **Pflicht, immer** |
| Local AI | Preanalysis + Validierung 2 | dito, aber **conditional** (nur bei Dokumentkontext) | bedingt |
| Retrieval | Context | dito, nur bei Akten-/Rechtsbezug | bedingt |
| Legal Database | Retrieval/Generation (Fast Path) | ausbauen | hoch |
| Cloud AI | **einziger Generator** | unverändert | Pflicht für Substanz |
| Validation | Security + Qualität | **nach Zweck differenziert** | Pflicht (angepasst) |
| Conversation Memory | **nur Persistenz/Anzeige** | **auch Modellkontext** | **fehlt heute** |
| Chat Router | HTTP + Streaming | dito | ok |
| Chat Service | Persistenz + Delegation an Drafting | **eigene Orchestrierung** mit Klassifikation | Umbau |

## M. Risiken

- **R1 (hoch):** RC-1 zu grob „reparieren" und die Vollständigkeitsprüfung auch
  für Entwürfe entfernen → echter Qualitätsverlust bei Schriftsätzen.
  *Gegenmaßnahme:* strikt an `purpose` binden, Test für beide Zweige.
- **R2 (hoch):** History-Feld als Schlupfloch an Presidio vorbei.
  *Gegenmaßnahme:* ausschließlich über `gateway.prepare_request`, plus Test, der
  nachweist, dass ein Name in Turn 1 auch in Turn 5 noch pseudonymisiert ist.
- **R3 (mittel):** Kontextfenster/Kosten durch Historie.
  *Gegenmaßnahme:* Turn-/Zeichenbudget, gemessen.
- **R4 (mittel):** Local AI conditional zu machen entzieht Dokumentfällen Schutz,
  wenn die Bedingung falsch gezogen wird. *Gegenmaßnahme:* „hat Dokumentkontext"
  als einziges Kriterium, konservativ.
- **R5 (niedrig):** Messbasis — Cloud-Latenz fehlt in allen Zahlen. Vor einem
  TTFR-Versprechen muss einmal mit echtem Claude gemessen werden.

## N. Testplan

1. `test_chat_greeting_is_not_blocked_by_placeholder_completeness` — „Hallo" in
   einer Akte mit Mandantenname ⇒ Antwort ohne Platzhalter besteht.
2. `test_draft_still_requires_all_placeholders` — Gegenprobe für
   `formulate_draft` ⇒ unverändert blockiert.
3. `test_unexpected_placeholder_token_still_blocks_chat` — Sicherheitsteil bleibt.
4. `test_original_value_reappearing_still_blocks_chat` — Sicherheitsteil bleibt.
5. `test_conversation_history_reaches_the_payload` — Turn 4 enthält Turn 2/3.
6. `test_conversation_history_is_pseudonymized` — Name aus Turn 1 erscheint in
   Turn 5 nur als Platzhalter.
7. `test_history_is_truncated_to_budget`.
8. `test_frau_mueller_still_fails_closed` — unveränderter Regressionsfall.
9. Performance-Assertion: „Hallo" ohne Dokumentkontext < 2 s (ohne Cloud).
10. Volle Suite ohne Regression (Baseline zum Zeitpunkt der Messung: 1807/1/0).

## O. Empfohlene Implementierungsreihenfolge

1. **CHAT-01** (RC-1) — größter Effekt, kleinster Eingriff, macht den Chat
   überhaupt erst benutzbar.
2. **CHAT-05** (RC-5/QW-1) — ehrliche Fehlermeldungen; erleichtert alles Weitere.
3. **CHAT-02** (RC-2) — History als Allowlist-Feld. Erst nach 1, sonst wird die
   Verbesserung von der Blockade verdeckt.
4. **CHAT-04** (RC-4) — Local AI conditional. Erst nach 1+2, damit die Messung
   nicht durch verworfene Antworten verfälscht wird.
5. **CHAT-03** (RC-3) — Request-Klassifikation/eigene Chat-Orchestrierung.
   Größter Umbau, zuletzt, auf Basis der dann realen Messwerte.

## P. Erwartete Performanceverbesserung

| Fall | heute | nach CHAT-01 | nach CHAT-04 |
|---|---:|---:|---:|
| „Hallo" (warm) | 10,8 s → **verworfen** | 10,8 s, Antwort kommt an | **~0,1 s** + Cloud |
| Anschlussfrage | 11,1 s → verworfen | 11,1 s | ~0,1 s + Cloud |
| Dokumentfrage | unverändert | unverändert | unverändert (Local AI bleibt Pflicht) |

Alle Zahlen **ohne** Cloud-Latenz. Das Auftrags-Ziel „2–3 s TTFR" ist mit
CHAT-01 + CHAT-04 + bestehendem Streaming realistisch erreichbar.

## Q. Erwartete Verbesserung der Chat-Intelligenz

- Nach **CHAT-01**: Der Chat antwortet auf Begrüßungen, Dank und allgemeine
  Fragen überhaupt — heute werden diese Antworten erzeugt und dann verworfen.
- Nach **CHAT-02**: „Welche Frist gilt?" bezieht sich nachweislich auf die
  vorherigen Turns (im Direkttest mit History gelingt genau das bereits).
- Nach **CHAT-03**: Lexono entscheidet je Nachricht, welcher Workflow nötig ist —
  aus dem Formularausfüller wird die im Produktauftrag verlangte
  Orchestrierungsschicht.
- **Unverändert:** juristische Substanz kommt weiter von Claude + echten
  Rechtsquellen, nicht vom lokalen Modell (siehe I).

---

# FOLGE-TASKS (Vorschlag — vom Hauptstrang in TASK_MAP/OPEN_ISSUES einzutragen)

### CHAT-01 — Antwort-Vollständigkeitsprüfung nach Zweck differenzieren
- **Priority:** P0
- **Problem:** Jede natürliche Chatantwort wird verworfen; der Anwalt sieht „Blockiert aus Datenschutzgründen".
- **Root Cause:** `check_placeholders_present` (security_check.py:229) verlangt alle Mapping-Platzhalter in der **eingehenden** Antwort; verdrahtet über `check_response_placeholder_integrity` (:280) in `drafting/service.py:539-584`. `skip_semantic_check` überspringt nur Stufe 2.
- **Goal:** Vollständigkeitsforderung nur für `formulate_draft`. Token-Manipulations- und Originalwert-Leak-Prüfung bleiben für **beide** Zwecke.
- **Dependencies:** keine
- **Security Impact:** Keine Absenkung — die entfallende Regel schützt keine Daten, sie erzwingt Erwähnung. Beide schützenden Teilprüfungen bleiben.
- **Performance Impact:** keiner
- **Product Impact:** sehr hoch — macht den Chat benutzbar
- **Files:** `app/privacy/security_check.py`, `app/drafting/response_validation.py`, `app/drafting/service.py`
- **Acceptance:** „Hallo" in Akte mit Mandantennamen liefert eine Antwort; Entwurf ohne Platzhalter bleibt blockiert.
- **Tests:** N-1..N-4
- **Measurement:** Anteil `response_validation_failed` im API-Log vor/nach
- **Risk:** mittel (Privacy-nah) — Änderung eng an `purpose` binden
- **Parallelization:** exklusiv (berührt Privacy-Kern)

### CHAT-02 — Gesprächsverlauf als achtes Allowlist-Feld
- **Priority:** P0
- **Problem:** Das Modell erhält den Verlauf nie; Anschlussfragen sind unverständlich.
- **Root Cause:** kein History-Parameter in `create_draft`; kein Feld in `ClaudeRequestPayload`; `_build_sachverhalt` liest nie `ChatMessage`.
- **Goal:** `anonymisierter_gespraechsverlauf: list[str]` über **denselben** Gateway-Durchlauf; Turn-/Zeichenbudget.
- **Dependencies:** CHAT-01
- **Security Impact:** hoch — muss zwingend durch `gateway.prepare_request`; kein zweiter Pfad.
- **Performance Impact:** leicht negativ (mehr Tokens)
- **Product Impact:** hoch
- **Files:** `app/privacy/gateway_schema.py`, `app/privacy/gateway.py`, `app/privacy/security_check.py` (Allowlist), `app/drafting/service.py`, `app/chat/service.py`, `app/ai_providers/claude_writing_provider.py`
- **Acceptance:** Turn 4 enthält Inhalte aus Turn 2/3; Namen dort pseudonymisiert.
- **Tests:** N-5..N-8
- **Measurement:** Spy-Harness erneut laufen lassen (`payload_spy.py`)
- **Risk:** hoch
- **Parallelization:** exklusiv

### CHAT-03 — Request-Klassifikation und eigene Chat-Orchestrierung
- **Priority:** P1
- **Problem:** Jede Nachricht zahlt die volle Drafting-Pipeline.
- **Root Cause:** `ChatService` delegiert unverändert an `DraftingService.create_draft`.
- **Goal:** Klassifikation nach der Tabelle in L; nur benötigte Komponenten aktivieren.
- **Dependencies:** CHAT-01, CHAT-02, CHAT-04
- **Security Impact:** hoch — jede Fast-Path-Klasse braucht dokumentierte Begründung
- **Performance Impact:** hoch positiv
- **Product Impact:** sehr hoch (Orchestrierungsschicht laut Produktauftrag)
- **Files:** `app/chat/service.py`, `app/drafting/service.py`
- **Acceptance:** je Klasse nachweisbar nur die vorgesehenen Stufen im `PerfTrace`
- **Tests:** je Klasse ein Trace-Test
- **Risk:** hoch
- **Parallelization:** exklusiv, zuletzt

### CHAT-04 — Local-AI-Vorabanalyse an Dokumentkontext binden
- **Priority:** P1
- **Problem:** 10,8 s (warm) / 44,9 s (cold) für „Hallo".
- **Root Cause:** `_should_skip_llm_privacy_layers` verlangt `mappings == []`; echte Aktentitel enthalten Mandantennamen → nie erfüllt.
- **Goal:** Kriterium „kein Dokumentkontext" statt „keine Mappings"; zusätzlich Parallelisierung/Caching prüfen.
- **Dependencies:** CHAT-01
- **Security Impact:** mittel — braucht dokumentierte Begründung, warum ohne Dokumenttext keine Vorabanalyse nötig ist
- **Performance Impact:** 10,8 s → ~0,1 s (gemessen als Vergleichsfall D)
- **Product Impact:** hoch
- **Files:** `app/drafting/service.py:88`
- **Acceptance:** „Hallo" ohne Dokumentkontext < 2 s ohne Cloud; Dokumentfall unverändert mit Local AI
- **Tests:** N-9 + Gegenprobe Dokumentfall
- **Risk:** mittel
- **Parallelization:** nach CHAT-01

### CHAT-05 — Technische Fehler nicht als Datenschutz-Block anzeigen
- **Priority:** P2
- **Problem:** „Interner Fehler bei der Textproduktion" erscheint als „Blockiert aus Datenschutzgründen."
- **Root Cause:** `api_logger.py:96` Fallback für `unknown_block_reason`.
- **Goal:** eigene, ehrliche Meldung für technische Fehler — weiterhin **ohne** rohe Gründe (PII-Leck-Schutz bleibt).
- **Dependencies:** keine
- **Security Impact:** keiner (rohe `reasons` bleiben unsichtbar)
- **Performance Impact:** keiner
- **Product Impact:** mittel (Pilot-Diagnostizierbarkeit)
- **Files:** `app/privacy/api_logger.py`
- **Acceptance:** technischer Fehler ⇒ technische Meldung; Datenschutz-Block ⇒ unverändert
- **Tests:** 2 Tests; bestehender `test_blocked_reason_never_leaks_pii_into_redirect_url` muss grün bleiben
- **Risk:** gering
- **Parallelization:** frei

### CHAT-06 — Modell-Default in Code und Doku vereinheitlichen
- **Priority:** P3
- **Problem:** `settings.py:186` und `agents/local_ai/AGENT.md` sagen `qwen2.5:1.5b`, die installierte Instanz fährt `qwen3:8b`.
- **Goal:** eine Wahrheit; Historie in DECISIONS.md erhalten.
- **Files:** `app/config/settings.py`, `agents/local_ai/AGENT.md`, `.agentic/MODEL_EVALUATION.md`
- **Risk:** sehr gering · **Parallelization:** frei

---

## Rohdaten

- `payload_capture.json` — 4-Turn-Payloads (Spy)
- `perf_results.json` — Stufenzeiten aller 6 Szenarien
- `payload_spy.py`, `perf.py`, `block_reasons.py`, `why_blocked.py`, `trace_exc.py`
