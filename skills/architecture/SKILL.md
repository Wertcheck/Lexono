# Skill: Architecture Review

## Zweck

Prüft vor größeren Änderungen, ob eine geplante Änderung mit der
bestehenden Zielarchitektur (lokale Verarbeitung → Pseudonymisierung →
Final Payload Gate → Gateway → Cloud-KI) vereinbar ist.

## Wann einsetzen

Vor jeder Änderung, die Modulgrenzen, Datenflüsse oder die Provider-Auswahl
(lokal/Gateway/direkt) betrifft.

## Voraussetzungen

`ARCHITECTURE.md` (Root) und `.agentic/DECISIONS.md` gelesen.

## Vorgehensweise

1. Betroffene Datenflüsse identifizieren (welche Daten, wohin, wann
   pseudonymisiert).
2. Prüfen: verlässt etwas den Kanzlei-PC, das nicht zuvor durch das Final
   Payload Gate lief? Wenn ja → stoppen, Agent H (Security) einbeziehen.
3. Prüfen: wird eine bereits funktionierende Komponente unnötig neu
   gebaut? (Masterprompt: „nicht bereits funktionierende Komponenten neu
   bauen“.)
4. Entscheidung + Begründung in `.agentic/DECISIONS.md` eintragen.

## Prüfungen

- Kein direkter Cloud-Aufruf unter Umgehung des Gateways.
- Keine Vermischung von Mandanten-/Aktenkontext.

## Typische Fehler

Blinde globale Refactorings ohne Cross-Cutting-Analyse (siehe
Masterprompt §33) – insbesondere bei Branding-Strings, die versehentlich
auch technische Pfade/Modulnamen treffen.

## Relevante Dateien

`ARCHITECTURE.md`, `.agentic/DECISIONS.md`, `.agentic/OPEN_ISSUES.md`
