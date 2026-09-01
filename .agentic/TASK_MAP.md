# TASK_MAP – Kategorisierter Aufgabenstand (Master Workstream V3, §5)

Stand: 01.09., früher Morgen. Kategorien A–K wie im Master-Workstream-
Auftrag vorgegeben. Diese Datei wird bei Bedarf aktualisiert, ersetzt
aber nicht `OPEN_ISSUES.md` (dort stehen die Details/Begründungen).

## A – Kritisch / Security / Privacy

- Privacy Gateway, Pseudonymisierung, Final Payload Gate, Aktenisolation:
  **ERLEDIGT** (Vorsessions, in dieser Session nicht verschlechtert,
  unabhängig gegengeprüft).
- Dokument-Workspace-Isolation, XSS im Highlighting, native
  Fenster-API-Exposition, Titelleiste auf Pre-Auth-Seiten: **ERLEDIGT**
  (unabhängige Subagent-Security-Review, 01.09., keine Funde).
- Multi-Kanzlei-/Cross-Tenant-Unterstützung (`PROMPT38_ANALYSIS.md`):
  **OFFEN, PRODUKTENTSCHEIDUNG ERFORDERLICH** - nicht ungefragt begonnen.

## B – Produktfunktion

- Chat, Dokument-Upload, Schriftsatz-Generator, bestehende Tools:
  **ERLEDIGT** (Vorsessions + heute integriert, nicht dupliziert).
- Feedback-/Kategorisierungssystem (`app/pilot_feedback/`): **ERLEDIGT**
  (bereits vorhanden, gegen Zielarchitektur geprüft, ausreichend für
  Pilotphase).

## C – Chat / UX

- Chat als Startseite, Composer, Enter/Shift+Enter: **ERLEDIGT**
  (Vorsessions).
- Büroklammer-Icon, Drag & Drop, Mikrofon-UI (bewusst nicht
  cloud-angebunden): **ERLEDIGT** (heute Nacht).
- KI-Ladezustand (Puls-Sprechblase): **ERLEDIGT** (heute Nacht,
  Commit `f55925b`).
- Eigene Fenster-Titelleiste (Task #61): **ERLEDIGT, funktional verifiziert**
  (Nutzer hat Schließen-Button real bestätigt: "x button closes the
  app"). Rein optische Feinheiten (Logo entfernt) sind im Code behoben
  und in der finalen Installation ausgeliefert, aber noch nicht vom
  Nutzer visuell bestätigt (Nutzer ist unterwegs).

## D – Dokumentworkflow

- Dokument-Workspace mit Pseudonymisierungs-Highlighting: **ERLEDIGT**
  (textbasiert, nicht PDF-Seiten-Rendering - bewusste Entscheidung,
  siehe OPEN_ISSUES.md).
- OCR-Status-Anzeige, Fehlerzustände: **ERLEDIGT** (Vorsessions).

## E – UI / Visual

- Branding (Lexono statt KanzleiAI in sichtbarer UI): **ERLEDIGT**,
  systematisch gegengeprüft (kein Rest in Templates).
- Doppelte Logo-Darstellung: **ERLEDIGT** (Code-seitig behoben, Commit
  `39a574d`; visuelle Bestätigung am echten Fenster noch **OFFEN**).
- CI-Farben/Design-System: **ERLEDIGT** (unverändert aus Vorsessions,
  konsistent genutzt).

## F – Agenten / Feedback

- Agentenorganisation (`agents/`, `skills/`, `.agentic/`): **ERLEDIGT**
  (heute Nacht aufgebaut, tatsächlich genutzt - u. a. ein
  Security-Review-Subagent).
- Feedback→Kategorisierung→Priorisierung→Freigabe-Architektur:
  **TEILWEISE** - Erfassung + lokale Kategorisierung vorhanden, keine
  automatisierte Priorisierungs-/Reporting-Stufe. Für die Pilotphase als
  ausreichend bewertet, nicht weiter ausgebaut (§13: "wenn ausreichend,
  nicht unnötig neu bauen").

## G – Model / AI

- Datenbasierte lokale Modellwahl (qwen2.5:1.5b): **ERLEDIGT**
  (Vorsession, echter Benchmark).
- Runtime-Erweiterbarkeit über Ollama hinaus (llama.cpp o. ä.):
  **NICHT BEGONNEN** - Architektur-Readiness verifiziert (Protocol-
  basiert, vorbereitete Erweiterungspunkte), aber keine echte
  Implementierung/Benchmark. Bewusst nicht ungefragt gestartet
  (mehrstündiger Download-/Kompilieraufwand).

## H – Installer / Deployment

- Installer-Build, WebView2-Bundling, Tesseract-Bundling: **ERLEDIGT**,
  real verifiziert (drei Rebuilds diese Nacht, finaler Rebuild inkl.
  aller UI-Fixes erfolgreich installiert + smoke-getestet).
- Silent-Install-Zuverlässigkeit: **TEILWEISE / bekanntes Risiko** -
  gelegentliches Hängen beim ersten Versuch, zuverlässig durch
  Kill+Retry behoben, Ursache nicht identifiziert (siehe OPEN_ISSUES.md).

## I – Tests / QA

- Unit-/Integrationstests: **ERLEDIGT**, 1486 passed / 1 skipped / 0
  failed (letzter voller Lauf).
- E2E-Pilot-Tests (lokal + über echten Gateway-Server): **ERLEDIGT**,
  bereits vorhanden (`test_e2e_pilot_scenario.py`,
  `test_e2e_gateway_pilot_scenario.py`), erfüllen §23 vollständig
  (Dokument verarbeitet, PII bleibt lokal, Rekonstruktion funktioniert).
- Visual QA mit echten Screenshots: **BLOCKIERT** - kein Browser-/
  Screenshot-Tool in dieser Umgebung verfügbar.

## J – Dokumentation / Repository-Hygiene

- `.agentic/`-Projektgedächtnis: **ERLEDIGT**, laufend gepflegt.
- Root-Markdown-Sichtung (63 `.md`-Dateien gesamt, 12 im Root):
  **TEILWEISE** - historische Prompt-Artefakte identifiziert
  (`FINAL_REVIEW_REPORT.md`, `HANDOFF_PROMPT36_37_WINDOWS.md`,
  `PROMPT38_ANALYSIS.md` u. a.), noch nicht mit Hinweis-Headern
  versehen. Keine umfangreiche Aufräumaktion (bewusst, §24: Produktarbeit
  hat Vorrang).

## K – langfristige Architektur

- Multi-Agenten-Organisation für Dauerbetrieb: **TEILWEISE** angelegt
  (siehe F), bewusst nicht zu einer großen Orchestrierungsplattform
  ausgebaut (explizit untersagt).
- Kontinuierliche-Verbesserung-Grundlage (Feedback→Test→Release):
  **TEILWEISE**, ausreichend für Pilotphase, siehe F.
