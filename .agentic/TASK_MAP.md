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
- Doppelte Logo-Darstellung (Titelleiste + Sidebar gleichzeitig):
  **ERLEDIGT** (Code-seitig behoben, Commit `39a574d`; strukturell im
  ausgelieferten HTML bestätigt).
- **Logo-/Akzentfarbe grün statt Navy**: **BLOCKIERT, Produktentscheidung
  erforderlich** (01.09.) – siehe `OPEN_ISSUES.md`, Kategorie
  "Produktentscheidung erforderlich", ganz oben. Aktuelles `#101828`
  wurde in einer früheren Sitzung per Pixelmessung aus dem echten
  offiziellen Logo verifiziert (Navy, nicht grün) - echter Zielkonflikt
  mit dem aktuellen Auftrag, nicht eigenmächtig entschieden.
- **Weitere Akzentfarben (Chat-Schnellaktionen)**: **ERLEDIGT** (01.09.,
  später) - vier farblich unterschiedliche Icon-Badges
  (grün/blau/lila/orange), neue `--accent-blue`/`-purple`/`-orange`-Tokens
  in `app.css`, unabhängig von der offenen Primärfarben-Frage umgesetzt
  (die "grüne" Badge bindet weiterhin bewusst an `--seal-green` und
  übernimmt automatisch den finalen Wert, sobald die Logo-Frage geklärt
  ist). Getestet: `test_chat_empty_state_quick_actions_have_distinct_accent_colors`.
- CI-Farben/Design-System (Grundstruktur: Tinte/Papier/Akzent-Tokens):
  **ERLEDIGT** (unverändert aus Vorsessions, konsistent über ~72
  Verwendungsstellen genutzt - siehe Farbfrage oben für den konkreten
  Wert der Akzentfarbe).

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
- Root-Markdown-Hinweis-Header auf historischen Dokumenten: **ERLEDIGT**
  (`FINAL_REVIEW_REPORT.md`, `HANDOFF_PROMPT36_37_WINDOWS.md`,
  `PROMPT38_ANALYSIS.md`, `SECURITY_REVIEW.md` zeigen jetzt auf
  aktuellere Quellen, Inhalt unverändert). Keine umfangreiche
  Aufräumaktion darüber hinaus (bewusst, §24: Produktarbeit hat Vorrang).
- **Dokumentationskonsolidierung (01.09., expliziter Nutzerauftrag)**:
  **ERLEDIGT**. `.agentic/SESSION_LOG.md` neu (Archiv der bisherigen
  chronologischen Verlaufserzählung), `PROJECT_STATE.md` auf reinen
  Ist-Zustand reduziert, `TEST_STATE.md` (veraltete Zahl 1463→1486)/
  `OPEN_ISSUES.md` (erledigte MEDIUM-Punkte entfernt)/`TASK_MAP.md`
  gegen den tatsächlichen Stand aktualisiert.
- **Klarstellung verbindlicher Architekturstand vs. historische
  Kehrtwenden in `ARCHITECTURE.md` (01.09., expliziter Nutzerauftrag)**:
  **ERLEDIGT**. Neuer "AKTUELLER VERBINDLICHER ARCHITEKTURSTAND"-Block
  direkt nach dem Titel (lokale KI zwingend, Presidio zwingend,
  Lexono-Gateway verbindlich, natives Windows-Fenster zwingend,
  CI-Farbe offen). §57/§60/§63 (dokumentierte Kehrtwenden zu lokaler
  KI/zentralem Proxy) bekamen "ÜBERHOLT"-Markierungen direkt am
  Abschnittsanfang - nichts gelöscht, nur gekennzeichnet.

## K – langfristige Architektur

- Multi-Agenten-Organisation für Dauerbetrieb: **TEILWEISE** angelegt
  (siehe F), bewusst nicht zu einer großen Orchestrierungsplattform
  ausgebaut (explizit untersagt).
- Kontinuierliche-Verbesserung-Grundlage (Feedback→Test→Release):
  **TEILWEISE**, ausreichend für Pilotphase, siehe F.
