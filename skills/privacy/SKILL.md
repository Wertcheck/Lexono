# Skill: Privacy / DSGVO-Datenfluss-Prüfung

## Zweck

Für jede Änderung am Datenfluss prüfen: bleibt die Pseudonymisierung vor
jedem Cloud-Call garantiert?

## Vorgehensweise

Datenfluss Schritt für Schritt durchgehen: Dokument → lokale Speicherung →
OCR → lokale KI → Presidio → Pseudonymisierung → Final Payload Gate →
Gateway → Anthropic → Antwort → lokale Rekonstruktion → Nutzer. Für jeden
Schritt: Ist die Datenform (Klartext/pseudonymisiert) korrekt? Wird sie
gespeichert? Geloggt? Verlässt sie die Maschine? Kann ein Fehlerfall die
Kette umgehen?

## Prinzip

Fail-closed: Wenn Pseudonymisierung fehlschlägt → KEIN Cloud-Call.

## Nutzerseitige Begrifflichkeit

In der normalen Produkt-UI „Pseudonymisierung“ verwenden, nicht
„Presidio“ (Bibliotheksname). Ausnahme: Seiten, die sich explizit als
technische Transparenz-/Dokumentationsseiten ausweisen (siehe
`.agentic/DECISIONS.md`).

## Relevante Dateien

`agents/security/AGENT.md`, ARCHITECTURE.md (Privacy-Gateway-Kapitel)
