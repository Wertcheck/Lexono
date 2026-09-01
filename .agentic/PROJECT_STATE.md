# Lexono – Projektgedächtnis: PROJECT_STATE

**Single Source of Truth für den aktuellen Zustand.** Diese Datei enthält
NUR den Ist-Zustand, keine chronologische Verlaufserzählung mehr - für
die Historie siehe `SESSION_LOG.md`. Bei jeder wesentlichen Änderung wird
diese Datei aktualisiert, nicht durch einen neuen datierten Abschnitt
ergänzt.

Kanonische Architekturquelle bleibt `ARCHITECTURE.md` (Root) – dieses
Verzeichnis dupliziert sie NICHT, sondern ergänzt sie um agentenbezogenes
Arbeitsgedächtnis. Weitere Dateien in diesem Verzeichnis:
`OPEN_ISSUES.md` (offene Punkte, kategorisiert), `DECISIONS.md`
(Entscheidungen mit Begründung), `TASK_MAP.md` (Gesamtstand nach
Kategorien A–K), `TEST_STATE.md` (Testbaseline), `MODEL_EVALUATION.md`,
`VISUAL_QA.md`, `AGENT_HANDOFFS.md`, `SESSION_LOG.md` (Archiv).

## Produktidentität

- Produktname: **Lexono**. „KanzleiAI“/„Kanzlei AI“ war ausschließlich ein
  früherer interner Arbeitstitel und darf in sichtbarer Produktidentität
  nicht mehr auftauchen (Ausnahme: interne technische Pfade/Modulnamen wie
  `kanzlei_ai.exe`, `app/`-Paketstruktur, `%LOCALAPPDATA%\Lexono` intern
  weiterhin `KanzleiAI` als `ProgramData`-Verzeichnisname – siehe
  DECISIONS.md, kein blindes globales Rename bestehender Datenpfade).
  Verifiziert: kein „KanzleiAI“-Rest mehr in Templates oder sichtbaren
  UI-Strings (Stand 01.09., vollständig gegengeprüft).
- Zielgruppe: Steuer-/Wirtschaftskanzleien (nicht primär Arbeitsrecht).

## Architektur-Kernprinzip (nicht verhandelbar)

Sensible Mandantendaten bleiben lokal. Lokale Verarbeitung → Pseudonymisierung
(Presidio, intern) → Final Payload Gate → NUR pseudonymisierter Payload →
Lexono Gateway → Cloud-KI (Anthropic). Der Gateway ist Infrastruktur für
Schlüssel-/Zugriffsverwaltung, NICHT die Privacy-Prüfstelle. Siehe
`ARCHITECTURE.md` §§ zur Gateway- und Local-AI-Architektur (zuletzt §71).

## Aktueller funktionaler Stand

- **Gateway-Architektur**: produktiv einsatzbereit, Baseline.
- **Local AI**: Pflichtkomponente (wenn aktiviert), über Ollama
  (`qwen2.5:1.5b`, datenbasiert gewählt, AUSDRÜCKLICH austauschbar - kein
  fest verdrahtetes Modell, siehe `LocalLLMProvider`-Protocol in
  `app/ai_providers/local_llm_provider.py`) angebunden, per CLI-Setup-
  Wizard verdrahtet. Seit 01.09. zusätzlich über die Web-Settings-Seite
  (`/dashboard/settings`, Abschnitt "Lokale KI") sichtbar UND das
  Modell-Tag/die Basis-URL änderbar - vorher nur per `.env`/CLI möglich.
- **Chat**: zentrale Startseite nach Login (`/dashboard/chat`), mit
  KI-Ladezustand (Puls-Sprechblase), Büroklammer-Upload, Drag & Drop,
  vorbereitetem (nicht cloud-angebundenem) Mikrofon-Button.
- **Dokument-Workspace**: `/dashboard/chat/{conversation_id}/document/
  {document_id}` – extrahierter Text mit Pseudonymisierungs-Highlighting,
  Aktenisolation getestet. Kein PDF-Seiten-Rendering (bewusst, siehe
  OPEN_ISSUES.md).
- **Fenster-Chrome**: eigene, frameless Titelleiste (Task #61) statt
  nativer OS-Titelleiste – Schließen-Button vom Nutzer real bestätigt
  funktionsfähig. Logo-Entfernung aus der Titelleiste ebenfalls
  bestätigt (kein `app-titlebar__logo` mehr im HTML).
- **Statusanzeigen**: Lokale-KI-/Cloud-KI-Status global in der Sidebar
  (`base.html`, aus `request.app.state`, ohne Router-Änderungen) sowie
  weiterhin im Chat-Header.
- **Feedback-System**: `app/pilot_feedback/` – Erfassung + lokale
  Keyword-Kategorisierung + Admin-Freigabe-Schleife für System-relevante
  Vorschläge. Für die Pilotphase als ausreichend bewertet.
- **Agentenorganisation**: `agents/` (Rollenakten) + `skills/`
  (wiederverwendbares Vorgehen), tatsächlich genutzt (u. a. ein
  Security-Review-Subagent in dieser Sitzung). Audit 01.09.: kein
  Multi-Agenten-Laufzeitsystem, sondern rollenbasierte Kontextdateien für
  die eine ausführende Instanz + das `Agent`-Tool als einziger echter
  Delegationsmechanismus – siehe `agents/lead/AGENT.md` ("Funktionsweise
  der Delegation") und `AGENT_HANDOFFS.md`.
- **Dokument-Workspace-Schnellaktionen** ("Antwort entwerfen"/"Fristen &
  Risiken prüfen"/"Zusammenfassung erstellen"): ergänzt in der
  Kontextleiste, referenzieren den echten Dateinamen, nutzen den
  bestehenden Prefill-/Sende-Weg (kein neuer Code-Pfad).

## CI-/Branding-Frage: GEKLÄRT (01.09.)

Der Nutzer hat die verbindliche CI explizit benannt (Primary/Logo
`#101828`, Canvas `#f8fafc`, Cards `#ffffff`, Secondary Text `#64748b`)
- Gegenprüfung ergab, dass der Code bereits exakt diese Werte verwendet.
Keine Änderung nötig, kein offener Punkt mehr. Siehe `OPEN_ISSUES.md`
für die Herleitung.

## Installer

**Siebter Rebuild (01.09., ~13:06-13:11 Uhr) erfolgreich GEBAUT,
INSTALLIERT UND SMOKE-GETESTET.** Enthält zusätzlich zum sechsten
Rebuild die neue "Lokale KI"-Sektion in den Web-Settings sowie den
`.env`-Injection-Sicherheitsfix (Commits `31a3ede`/`ba2f286`). Silent-
Install erneut ohne Stall (~60s). Per HTTP-Smoke-Test bestätigt: Login,
Dokument-Upload/-Workspace mit Schnellaktionen, alle Bestandsseiten
UND die neue Settings-Seite mit "Lokale KI"-Sektion erreichbar. Läuft
aktuell für den Nutzer (`kanzlei_ai.exe serve`).

Admin-Testlogin unverändert: `admin@kanzlei.de` /
`Lexono-Smoke-Test-Pw-2026-Neu!` (nur lokale Testinstanz, keine echten
Mandantendaten).

Admin-Testlogin für die lokale Installation:
`admin@kanzlei.de` / `Lexono-Smoke-Test-Pw-2026-Neu!` (nur lokale
Testinstanz, kein Produktivsystem, keine echten Mandantendaten).

## Test-Baseline

Siehe `TEST_STATE.md` für den exakten, aktuell gültigen Stand.

## Git

Alle Änderungen lokal committet, **kein Push** (durchgehend eingehalten).
Working Tree sauber halten – vor jeder größeren Änderung `git status`
prüfen.

## Größte offene Workstreams

Siehe `OPEN_ISSUES.md` für die vollständige, kategorisierte Liste.
Zusammengefasst die wichtigsten: (1) Logo-/Akzentfarben-Zielkonflikt
(Entscheidung ausstehend), (2) Model-Evaluation über mehrere Runtimes
(Architektur-Readiness verifiziert, Umsetzung nicht begonnen), (3)
echter Visual-QA-Screenshot-Loop (kein Tool verfügbar), (4) echtes
PDF-Seiten-Rendering im Dokument-Workspace (bewusst zurückgestellt).
