# Lexono – Projektgedächtnis: PROJECT_STATE

Kanonische Architekturquelle bleibt `ARCHITECTURE.md` (Root) – dieses
Verzeichnis dupliziert sie NICHT, sondern ergänzt sie um agentenbezogenes
Arbeitsgedächtnis (Entscheidungen, offene Punkte, Teststand, Handoffs).

## Produktidentität

- Produktname: **Lexono**. „KanzleiAI“/„Kanzlei AI“ war ausschließlich ein
  früherer interner Arbeitstitel und darf in sichtbarer Produktidentität
  nicht mehr auftauchen (Ausnahme: interne technische Pfade/Modulnamen wie
  `kanzlei_ai.exe`, `app/`-Paketstruktur – siehe DECISIONS.md, kein blindes
  globales Rename).
- Zielgruppe: Steuer-/Wirtschaftskanzleien (nicht primär Arbeitsrecht).

## Architektur-Kernprinzip (nicht verhandelbar)

Sensible Mandantendaten bleiben lokal. Lokale Verarbeitung → Pseudonymisierung
(Presidio, intern) → Final Payload Gate → NUR pseudonymisierter Payload →
Lexono Gateway → Cloud-KI (Anthropic). Der Gateway ist Infrastruktur für
Schlüssel-/Zugriffsverwaltung, NICHT die Privacy-Prüfstelle. Siehe
`ARCHITECTURE.md` §§ zur Gateway- und Local-AI-Architektur (zuletzt §71).

## Aktueller Stand (31.08., Beginn Masterprompt V2)

- Gateway-Architektur: produktiv einsatzbereit, Baseline, nicht neu
  diskutiert.
- Local AI: Pflichtkomponente, über Ollama (`qwen2.5:1.5b`, datenbasiert
  gewählt) angebunden, real per Setup-Wizard verdrahtet (`local-ai-setup`
  CLI-Subcommand).
- Chat: zentrale Startseite nach Login (`/dashboard/chat`), mit zwei
  Statusindikatoren (Lokale KI / Cloud-KI) im Chat-Header.
- Test-Baseline: **1463 passed, 1 skipped, 0 failed** (siehe TEST_STATE.md).
- Installer zuletzt real gebaut+installiert+smoke-getestet: erfolgreich
  (WebView2-Fix bestätigt stabil).

## Agentenorganisation

Siehe `agents/` (Rollen/Akten) und `skills/` (wiederverwendbares Vorgehen).
Diese Struktur wurde am 31.08. im Rahmen des Masterprompts V2 neu angelegt
(vorher nicht vorhanden) – siehe AGENT_HANDOFFS.md für den Log.

## Nächste größere Workstreams (noch nicht begonnen)

Siehe OPEN_ISSUES.md für die vollständige, kategorisierte Liste. Die
größten offenen Posten sind: vollständiger Dokument-Workspace mit
Pseudonymisierungs-Highlighting (Referenzbild 2), Model-Evaluation-Engine
über mehrere Runtimes/Modelle, echter Visual-QA-Screenshot-Loop, und die
Entscheidung zum nativen Fenster-Chrome (frameless vs. aktuelles
natives WinForms-Fenster).
