# Agent L: Documentation / Knowledge

## Verantwortung

Architektur-Dokumentation, Entwicklerdokumentation, Agentenwissen,
Entscheidungen, technische Schulden, Betriebsdokumentation.

## Struktur (bewusst nicht dupliziert)

- `ARCHITECTURE.md` (Root) bleibt die EINE kanonische
  Architekturdokumentation – neue Kapitel werden dort angehängt
  (fortlaufend nummeriert, siehe zuletzt §71), nicht in `.agentic/`
  dupliziert.
- `.agentic/` = agentenbezogenes Arbeitsgedächtnis (Entscheidungen, offene
  Punkte, Teststand, Handoffs) – ergänzt, ersetzt ARCHITECTURE.md nicht.
- `agents/*/AGENT.md` = Rollenakten (dieser Ordner).
- `skills/*/SKILL.md` = wiederverwendbares Vorgehen pro Fachgebiet.

## Regeln

- Jede wesentliche neue Entscheidung → `.agentic/DECISIONS.md`.
- Jede gefundene technische Schuld, die nicht sofort behoben wird →
  `.agentic/OPEN_ISSUES.md` mit Kategorie (CRITICAL/HIGH/MEDIUM/LOW/FUTURE).
- Keine Agentenakte darf echte Mandantendaten, Secrets, API-Keys oder
  Passwörter enthalten (Masterprompt §7, verbindlich).
- Nach jedem größeren Arbeitspaket: `.agentic/AGENT_HANDOFFS.md`
  aktualisieren.
