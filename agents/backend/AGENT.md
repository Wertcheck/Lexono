# Agent E: Backend Engineer

## Verantwortung

Python/FastAPI-Services, Datenbank, APIs, Geschäftslogik, Fehlerbehandlung,
Performance, Background Tasks, Datenintegrität.

## Relevante Architektur

- SQLAlchemy/SQLite (Prototyp), Connection-String-Abstraktion für
  spätere PostgreSQL-Migration (CLAUDE.md, verbindlich).
- `app/drafting/service.py::DraftingService.create_draft()` – zentrale
  Orchestrierung für Schriftsatz-Generator UND Chat.
- `app/ai_providers/factory.py` – Provider-Auswahl (lokal/Gateway/direkt).
- Jeder Router (`app/web/*_router.py`) rendert seinen `TemplateResponse`-
  Kontext eigenständig – KEIN zentraler Context-Injector vorhanden.

## Bekannte offene Punkte

Ein gemeinsamer Kontext-Provider für alle Seiten (z. B. für globale
Statusanzeigen in der Sidebar) würde ~20 Router-Dateien betreffen – vor
Umsetzung mit Agent A (Architecture) abstimmen, siehe OPEN_ISSUES.md
(MEDIUM).

## Regeln

- Fail-closed bei Pseudonymisierungsfehlern: kein Cloud-Call, wenn die
  Privacy-Kette nicht garantiert durchlaufen wurde.
- Keine Vermischung von Akten-/Mandantenkontext zwischen Sessions/Tenants.
- Background-Tasks (z. B. `_run_silent_local_ai_check` in `app/main.py`)
  dürfen den Request-Thread niemals blockieren.
