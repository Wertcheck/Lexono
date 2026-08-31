# Skill: Chat-Feature-Änderung

## Zweck

Änderungen an der zentralen Chat-Oberfläche sicher vornehmen, ohne den
Privacy-Fluss (lokale KI → Pseudonymisierung → Gateway → Cloud-KI) zu
umgehen.

## Vorgehensweise

1. `app/chat/service.py::ChatService.send_message` verstehen – ruft immer
   `DraftingService.create_draft()`, NIE direkt einen Cloud-Provider.
2. UI-Änderungen in `chat.html` isoliert testen (siehe
   `tests/test_web_chat.py` als Muster für neue Tests).
3. Bei neuen Statuszuständen: alle Zustände in der UI abdecken (nicht nur
   den Erfolgsfall) – siehe bestehendes Muster mit
   `checking/disabled/ready/runtime_missing/runtime_unreachable/
   model_missing` für `local_ai_status`.

## Prüfungen

- Quick-Actions/Composer funktionieren weiterhin ohne aktive Unterhaltung
  (leerer Zustand) UND mit aktiver Unterhaltung.
- Dokument-Anhänge zeigen korrekten Status (VERARBEITET/WARTET AUF OCR/
  VERARBEITUNG FEHLGESCHLAGEN/FORMAT NICHT UNTERSTÜTZT).

## Typische Fehler

`app.state`-abhängige Werte (z. B. `local_ai_status`) in Tests ohne
Berücksichtigung der Testreihenfolge setzen – siehe
`.agentic/TEST_STATE.md`, bekannter Testisolationsvorfall.

## Relevante Dateien

`app/chat/service.py`, `app/web/chat_router.py`,
`app/web/templates/chat.html`
