# Agent D: Chat / Document Workspace

## Verantwortung

Chat, Unterhaltungsliste, Composer, Datei-Upload, Büroklammer, Drag & Drop,
Spracheingabe, Dokumentstatus, Dokumentansicht, Pseudonymisierungs-
darstellung, Kontextleiste, Mandanten-/Aktenkontext.

## Relevante Architektur

- `app/chat/service.py::ChatService.send_message` → ruft
  `DraftingService.create_draft()` (dieselbe Pipeline wie der
  Schriftsatz-Generator) → lokale KI (optional, Pflicht wenn konfiguriert)
  → Pseudonymisierung/Final Payload Gate → Cloud-KI.
- `app/web/chat_router.py` – Rendering-Kontext für `chat.html`, inkl.
  `local_ai_status`, `provider_configured`.
- `app/web/templates/chat.html` – zentrale Chat-Seite (Landingpage nach
  Login).

## Bestehende Funktionen (nicht neu bauen)

- Vier Quick-Actions im leeren Chat-Zustand (Dokument analysieren,
  Schriftsatz erstellen, Dokument zusammenfassen, Akte öffnen).
- Dokumentstatus-Badges pro Anhang: Verarbeitet / Wartet auf OCR /
  Verarbeitung fehlgeschlagen / Format nicht unterstützt.
- Zwei Statusindikatoren im Chat-Header: Lokale KI (5 Zustände) und
  Cloud-KI (Gateway- oder Dev-Key-bewusst).

## Größte offene Aufgabe

Dokument-Workspace mit inline PDF-Ansicht, Pseudonymisierungs-Highlights
und rechter Kontextleiste (Referenzbild 2) – siehe
`.agentic/OPEN_ISSUES.md`, Kategorie HIGH, und `agents/ux/AGENT.md` für das
Designkonzept. Erfordert Abstimmung mit Agent H (Security/Privacy), da nur
REAL erkannte Presidio-Kategorien angezeigt werden dürfen, keine
Fake-Datenlogik (Masterprompt §13).

## Sicherheitsregeln

- Keine unpseudonymisierten Mandantendaten in Fehlermeldungen oder
  Dateipfaden in der UI.
- Nur reale erkannte PII-Kategorien anzeigen, niemals synthetisch erfundene.
