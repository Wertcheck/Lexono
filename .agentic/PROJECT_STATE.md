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
  vorbereitetem (nicht cloud-angebundenem) Mikrofon-Button. Seit 01.09.
  (Referenzbild-Redesign) vollständig überarbeitet: 4 große
  Schnellaktions-Karten (Icon-Kreis + Titel + Beschreibung, ersetzt die
  vorherigen kompakten Pillen), 4. Karte "Weitere Funktion hinzufügen"
  verlinkt die bestehende Standard-Prompts-Verwaltung, ausführlicher
  Datenschutzhinweis (Icon + Text + "Mehr erfahren"-Link) nur auf der
  reinen Startansicht. Die vorherige, ständig sichtbare eigene
  "Unterhaltungen"-Spalte existiert NICHT mehr (Nutzerfeedback) - die
  Historie ist jetzt als Aufklapp-Unterpunkte der Sidebar-Gruppe "Chat"
  zu finden (nur auf der Chat-Seite selbst befüllt, sonst bleibt "Chat"
  ein flacher Link ohne zusätzliche DB-Abfrage). `?new=1`-Parameter auf
  `GET /dashboard/chat` erzwingt einen echten Leerzustand (behobener
  Bug: zeigte vorher bei bestehendem Verlauf immer die letzte
  Unterhaltung, auch bei explizitem "Neue Unterhaltung"-Klick).
- **Dokument-Workspace**: `/dashboard/chat/{conversation_id}/document/
  {document_id}` – extrahierter Text mit Pseudonymisierungs-Highlighting,
  Aktenisolation getestet. Kein PDF-Seiten-Rendering (bewusst, siehe
  OPEN_ISSUES.md).
- **Fenster-Chrome**: eigene, frameless Titelleiste (Task #61) statt
  nativer OS-Titelleiste – Schließen-Button vom Nutzer real bestätigt
  funktionsfähig. Logo-Entfernung aus der Titelleiste ebenfalls
  bestätigt (kein `app-titlebar__logo` mehr im HTML).
- **Statusanzeigen**: Lokale-KI-/Cloud-KI-Status global in der Sidebar
  (`base.html`, aus `request.app.state`, ohne Router-Änderungen), seit
  01.09. auf JEDER Seite inkl. Chat sichtbar (vorher dort ausgeblendet
  wegen Dopplung mit dem Chat-Header - die Header-Kopie wurde im
  Gegenzug entfernt, Sidebar ist jetzt die einzige Quelle). Cloud-KI vor
  Lokaler KI, eigene Cloud-/CPU-Icons statt generischem Zahnrad.
- **Branding/Logo**: neues Logo (grünes Schildsymbol + weißes
  Kettensymbol, `app/web/static/img/logo.svg`) - löst den vorherigen
  reinen Navy-Icon-Stand ab. Neue eigenständige Markenfarbe
  `--brand-green` (Logo/Sendebutton/"Neuen Chat starten"/aktive Chat-
  Navigation), GETRENNT von `--seal-green` (bleibt Navy/Tinte für
  generelle UI-Elemente). Schnellaktions-Icons nutzen bewusst NICHT
  Grün (bleibt exklusive Markenfarbe) - blau/lila/orange/neutral.
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

## CI-/Branding-Frage: AKTUALISIERT (01.09., später)

**Überholt der vorherige Stand dieses Abschnitts** (der eine reine
Navy-Wortmarke/-Icon als final bestätigt ansah): der Nutzer stellte
danach eine explizite, verbindliche Bild-Referenz bereit, die ein
grünes Schild-/Logo-Icon mit weißem Kettensymbol UND eine separate
navyfarbene "Lexono"-Wortmarke zeigt - beides existiert nebeneinander,
kein Widerspruch. Umgesetzt: `logo.svg` grün (`#16a34a`), Wortmarken-Text
bleibt bei `--ink-900`/navy. Neue, von `--seal-green` (bleibt Navy/Tinte
für generelle UI-Elemente) GETRENNTE Markenfarbe `--brand-green` für
Logo/Sendebutton/aktive Chat-Navigation. Die zuvor genannten Werte
(`#101828`/`#f8fafc`/`#ffffff`/`#64748b`) bleiben weiterhin als Ink-/
Canvas-/Card-Töne gültig - nur die Aussage "Logo ist rein Navy" ist
überholt. Kein weiterer offener Punkt.

## Installer

**Neunter Rebuild (01.09., ~14:18-14:23 Uhr) erfolgreich GEBAUT,
INSTALLIERT UND SMOKE-GETESTET.** Enthält zusätzlich zum siebten Rebuild
(Lokale-KI-Settings + `.env`-Injection-Fix): `AppMutex` im Installer
(Commit `cfa68bc`, empirisch verifiziert - Reinstall bei laufender App
wird jetzt sauber abgelehnt statt Dateien zu riskieren) und den
Minimieren-Icon-Sichtbarkeitsfix (Commit `391361a`, Nutzerfeedback).
Silent-Install lief erneut ohne Stall. Per HTTP-Smoke-Test bestätigt:
Login, Dokument-Workflow, alle Bestandsseiten erreichbar; Lokale-KI-
Konfiguration korrekt auf Standardzustand (deaktiviert) zurückgesetzt
nach dem `mistral:7b`-Testlauf. Läuft aktuell für den Nutzer
(`kanzlei_ai.exe serve`).

**Silent-Install-Stall**: in dieser Sitzung ECHT REPRODUZIERT (60s+ ohne
CPU-Fortschritt vor der Extraktion), Ursache aber NICHT zweifelsfrei
bewiesen - beste Hypothese: Defender-Cloud-Scan des unsignierten
~525-MB-Executables. Siehe OPEN_ISSUES.md (HIGH) für die vollständige,
ehrliche Root-Cause-Dokumentation.

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
