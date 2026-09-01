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

- **Gateway-Architektur**: produktiv einsatzbereit, Baseline. Erweitert
  (01.09., separater Hetzner-VPS-Umsetzungsauftrag, noch KEIN echter
  Server/Key/Tenant): (1) zentrale Modellsteuerung -
  `GatewaySettings.default_model` bestimmt jetzt ausschliesslich, welches
  Anthropic-Modell tatsaechlich aufgerufen wird (`gateway/relay.py`
  `call_anthropic(model=...)` statt `request.model`); das vom Client
  gesendete `model`-Feld wird weiterhin gegen `allowed_models` geprueft
  (Abwaertskompatibilitaet, bestehende Tests unveraendert gueltig), hat
  aber keinen Einfluss mehr auf den tatsaechlichen Aufruf - ein
  Modellwechsel ist damit reine `.env.gateway`-Aenderung + Neustart, kein
  Client-Rebuild. Neuer `model_validator` verhindert eine in sich
  widerspruechliche Konfiguration (`default_model` nicht in
  `allowed_models`). (2) `GatewaySettings.max_request_bytes` (Default
  200 KB) - Groessenpruefung in `relay_messages()` VOR dem Anthropic-
  Aufruf, HTTP 413 + `error_category="payload_too_large"` bei
  Ueberschreitung, Payload selbst wird dabei nicht zusaetzlich
  gespeichert. Zweite, groebere Schutzschicht im vorbereiteten
  `deploy/Caddyfile` (`request_body max_size 250KB`). (3)
  `log_relay_request()` um `input_tokens`/`output_tokens` erweitert (rein
  numerisch, Nutzungsbasis fuer spaetere Auswertung - keine Inhalte). (4)
  `scripts/revoke_gateway_tenant.py`/`rotate_gateway_tenant_secret.py`
  ergaenzt (duenne Wrapper um bereits vorhandene
  `gateway/tenant_admin.py`-Funktionen, keine neue Credential-Logik). (5)
  `deploy/`-Verzeichnis NEU: `lexono-gateway.service` (systemd,
  `--workers 1` bewusst wegen In-Memory-Rate-Limiter, startet via
  `python -m uvicorn` statt des Konsolenskripts, da `gateway`/`app` nicht
  ueber `pyproject.toml`s `packages.find` als Pakete installiert werden),
  `Caddyfile` (TLS-Terminierung + Bodylimit-Vorlage, Platzhalter-Domain),
  `README.md` (vollstaendiges Hetzner-/Linux-Runbook: Firewall/SSH-
  Haertung, Secrets-Konfiguration, API-Key-/Tenant-Credential-Rotation,
  Update/Rollback, Backup - durchgehend nur Platzhalter, kein echter Key).
  38 neue Tests (`tests/test_gateway.py` erweitert +
  `tests/test_gateway_tenant_scripts.py` neu), volle Suite weiterhin
  gruen (1515 passed/2 skipped). Noch **kein** echter Hetzner-Server,
  **kein** echter Anthropic-Key, **keine** produktive Kanzlei-Credential -
  ausdruecklich nur Code/Tests/Deployment-Vorlagen, wie beauftragt.
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
  reinen Startansicht. Die Unterhaltungshistorie ist seit der ZWEITEN
  Nutzerkorrektur (01.09., spaeter) ein rechtes FLYOUT, KEINE dauerhaft
  sichtbare Spalte mehr: `.chat-conversations` ist standardmaessig
  breite 0/unsichtbar (nimmt weder horizontal noch vertikal Platz ein)
  und faehrt erst per Klick auf "Chat" in der Haupt-Sidebar (Hook:
  `#sidebar-chat-link`, siehe `app_sidebar.js`) als Spalte rechts neben
  der Sidebar auf (`.chat-shell--history-open`) - schliesst sich wieder
  bei erneutem Klick oder bei jeder echten Navigation (frischer
  Seitenaufruf rendert die Klasse serverseitig nie). Git-Historie-
  Recherche (auf explizite Nutzeranweisung VOR der Neuimplementierung)
  ergab: ein echtes Klick-Toggle hat es fuer dieses Element nie gegeben -
  nur den flachen Link (urspruenglich) und zwei vom Nutzer verworfene
  Zwischenstaende: (1) vertikal unter "Chat" in der Sidebar eingeblendet
  (machte die Sidebar bei laengerer Historie hoeher als das Fenster,
  untere Menuepunkte nur noch nach Scrollen erreichbar) und (2) eine
  IMMER sichtbare statische Spalte rechts (nahm dauerhaft Platz ein,
  selbst im "Normalzustand"). Beide Male per echter nativer
  UI-Automatisierung (dev + installierter Build) verifiziert: geschlossen
  = nur Haupt-Sidebar, kein Scrollbedarf, alle unteren Menuepunkte
  sichtbar; Klick auf "Chat" = Flyout rechts, Sidebar-Hoehe unveraendert;
  erneuter Klick bzw. Navigation zu anderem Menuepunkt = Flyout wieder zu.
  `?new=1`-Parameter auf `GET /dashboard/chat` erzwingt weiterhin einen
  echten Leerzustand (behobener Bug: zeigte vorher bei bestehendem
  Verlauf immer die letzte Unterhaltung, auch bei explizitem "Neue
  Unterhaltung"-Klick) - dieser Fix blieb von der Korrektur unberührt.
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
- **Standard-Prompts** (`/dashboard/library/prompts`): volle CRUD
  (Anlegen/Bearbeiten/Löschen) war bereits vollständig implementiert
  (`prompt_library_router.py` + Templates) - bei der Nutzeranfrage nach
  fehlendem Löschen 01.09. stellte sich heraus, dass nur eine Verifikation
  nötig war, kein neuer Code. Echtes hartes Löschen (`db.delete()` +
  Audit-Event), keine Soft-Delete-Attrappe. Per echter nativer
  UI-Automatisierung (Anlegen → Bearbeiten inkl. Versionssprung v1→v2 →
  Löschen → Leerzustand "Noch keine Kanzlei-Prompts angelegt.") im
  installierten Build bestätigt.

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

**Dreizehnter Rebuild (01.09., ~18:08-18:13 Uhr) erfolgreich GEBAUT,
INSTALLIERT UND VERIFIZIERT.** Enthält das Chat-Historie-Flyout (zweite
Korrektur, siehe oben) + die verifizierte Standard-Prompts-CRUD. Silent-
Install lief ohne Stall. Per echter nativer UI-Automatisierung im
installierten Build bestätigt: Login, Chat-Flyout (geschlossen/offen/
Navigation-schliesst-wieder, Sidebar-Höhe konstant), identisches
Verhalten wie im Dev-Server. Läuft aktuell für den Nutzer
(`kanzlei_ai.exe serve`).

Vorheriger (zwölfter) Rebuild: enthielt das komplette Referenzbild-
Redesign (Logo/Farben/Sidebar/Chat) inkl. der ERSTEN Nutzerkorrektur
(Chat-Historie als eigene, aber noch dauerhaft sichtbare Spalte) - diese
Zwischenstufe ist mit dem 13. Rebuild überholt (siehe Flyout oben).

**Silent-Install-Stall**: bleibt ein bekanntes, nicht zweifelsfrei
bewiesenes Risiko (siehe OPEN_ISSUES.md, HIGH) - trat bei den letzten
mehreren Rebuilds dieser Sitzung nicht mehr auf, aber das allein ist
kein Beweis für eine Behebung.

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
