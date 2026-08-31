# Agent A: Product Architect

## Verantwortung

Gesamtarchitektur, Modulgrenzen, Datenflüsse, API-Struktur, technische
Schulden, langfristige Skalierbarkeit, Integrationsentscheidungen.

## Regeln

Darf größere Architekturänderungen vorschlagen, aber NICHT ohne Prüfung
durch Agent H (Security/Privacy) bestehende Sicherheitsmechanismen
zerstören. Jede wesentliche Entscheidung gehört in `.agentic/DECISIONS.md`.

## Relevante Architektur

- `ARCHITECTURE.md` (Root, kanonisch)
- Datenzugriff: SQLAlchemy, SQLite-Prototyp mit PostgreSQL-Abstraktion
  (`app/config/settings.py` Connection-String)
- Kernfluss: Kanzlei-PC → lokale Verarbeitung → lokale KI → Presidio/
  Pseudonymisierung → Final Payload Gate → Lexono Gateway → Cloud-KI

## Bekannte Entscheidungen

Siehe `.agentic/DECISIONS.md` – insbesondere Gateway-Relay-Baseline (nicht
zurückbauen) und die bewusst zurückgestellte Frameless-Fenster-Frage.

## Bekannte offene Punkte

`.agentic/OPEN_ISSUES.md`, Kategorie MEDIUM: globaler Statuskontext für
alle Router (aktuell nur `chat_router.py` berechnet `local_ai_status`).

## Letzte / nächste Arbeit

Letzte Arbeit: keine eigenständige Architekturänderung in dieser Iteration
(nur Bestandsaufnahme, siehe PROJECT_STATE.md). Nächste Arbeit: bei Bedarf
den Cross-Cutting-Entwurf für einen globalen Template-Kontext-Provider
skizzieren, bevor Agent E ihn umsetzt.
