# Skill: Dokumentation aktualisieren

## Zweck

Architektur- und Agentenwissen aktuell und widerspruchsfrei halten.

## Vorgehensweise

1. Neues ARCHITECTURE.md-Kapitel: fortlaufend nummerieren, an bestehende
   Struktur anhängen, alte Kapitel NICHT löschen (Historie bleibt
   nachvollziehbar) – aber sicherstellen, dass keine widersprüchliche
   „aktuelle“ Aussage neben der neuen stehen bleibt (CLAUDE.md §23-
   Analogon aus dem Phase-3-Masterprompt).
2. Neue Entscheidung: `.agentic/DECISIONS.md`, Format DECISION/REASON/DATE.
3. Neue technische Schuld: `.agentic/OPEN_ISSUES.md`, kategorisiert.
4. Nach Abschluss eines Arbeitspakets: `.agentic/AGENT_HANDOFFS.md`
   ergänzen.

## Regeln

Keine Redundanz: `.agentic/` dupliziert `ARCHITECTURE.md` nicht, sondern
verweist darauf.

## Relevante Dateien

`ARCHITECTURE.md`, `.agentic/*`, `agents/documentation/AGENT.md`
