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
  nicht mehr auftauchen.
  **Stand 12.09. (aktuell, ersetzt die vorherigen Einträge vom 01.09./
  12.09. früher am selben Tag):** auf ausdrücklichen, erweiterten
  Nutzerauftrag vollständig durchgeführter Rename über das gesamte aktive
  Produkt - `windows/kanzlei_ai.spec` → `windows/lexono.spec`
  (`EXE`/`COLLECT`-Name "Lexono"), `pyproject.toml`-Paketname → `lexono`,
  Session-Cookie-Name/-Salt, Backup-Archiv-Dateiname, Log-Download-
  Dateiname, Uvicorn-Thread-Name, `Start.vbs`-exe-Suche, sowie die beiden
  einzigen verbliebenen sichtbaren CLI-Hinweistexte in `backup.html`/
  `settings.html`. Installer (`Lexono.exe`/`Lexono_Setup.exe`) neu gebaut
  und die tatsächlich gebündelten Template-Dateien direkt inspiziert, um
  zu bestätigen, dass die Korrektur wirklich ausgeliefert wird.
  Bewusst UNVERÄNDERT (Datenmigration statt Rename, siehe DECISIONS.md):
  DB-Dateiname `kanzlei_ai.db`, Log-Dateiname `kanzlei_ai.log`, der
  Legacy-Env-Var-Name `KANZLEI_AI_DATA_DIR` (nur noch als Fallback) und
  der `AppId`/`AppMutex` des Installers (Upgrade-Kontinuität).
  `%ProgramData%\KanzleiAI` wird beim nächsten echten Start automatisch
  und sicher (atomares Rename, Fallback bei Fehlschlag) nach
  `%ProgramData%\Lexono` migriert - bestehende Daten werden dabei NICHT
  gelöscht. Verifiziert: volle Testsuite (1538 bestanden, 1 übersprungen,
  0 fehlgeschlagen); Zero-Active-Legacy-Scan über den gesamten
  Quellbaum durchgeführt (Ergebnisliste ausschließlich historische
  Dokumentation und bewusst beibehaltene Legacy-Fallback-Pfade).
- Zielgruppe: Steuer-/Wirtschaftskanzleien (nicht primär Arbeitsrecht).

## Architektur-Kernprinzip (nicht verhandelbar)

Sensible Mandantendaten bleiben lokal. Lokale Verarbeitung → Pseudonymisierung
(Presidio, intern) → Final Payload Gate → NUR pseudonymisierter Payload →
Lexono Gateway → Cloud-KI (Anthropic). Der Gateway ist Infrastruktur für
Schlüssel-/Zugriffsverwaltung, NICHT die Privacy-Prüfstelle. Siehe
`ARCHITECTURE.md` §§ zur Gateway- und Local-AI-Architektur (zuletzt §71).

## Aktueller funktionaler Stand

- **Cloud AI / Claude, aktueller Pilotpfad (12.09., P0 Cloud-AI Direct-
  Anthropic-Validierungslauf)**: **Direct-Anthropic-Pilotpfad (kein
  Gateway, `LEXONO_GATEWAY_URL` ungesetzt) real end-to-end verifiziert** -
  echter Presidio-Lauf, echte Pseudonymisierung, echter lokaler
  Ollama-Schritt (`qwen3:8b`), echter direkter Anthropic-API-Aufruf,
  echte Rekonstruktion, alles ueber die reale
  `service_factory.get_drafting_service()`/`DraftingService.create_draft()`
  - dieselbe Funktion, die auch der echte Chat-Endpunkt aufruft. Der
  Lexono-Gateway (`gateway/`, siehe Absatz unten "produktiv
  einsatzbereit") bleibt fuer diesen Pilotpfad bewusst **DEFERRED**, nicht
  Teil dieses Nachweises - "produktiv einsatzbereit" unten bezieht sich
  auf den Gateway-CODE, nicht auf einen tatsaechlich verifizierten,
  deployten Lauf (siehe `LEXONO_MASTER_PRODUCT.md` P0-08 fuer die
  praezise Unterscheidung). Nicht verwechseln: Direct-Key-Pilot verifiziert
  != Gateway-Produktionspfad verifiziert.

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
- **Installierter Windows-Produktpfad, real Ende-zu-Ende bewiesen (12.09.)**:
  Installer → echter Clean-Room-DATA_DIR (`%ProgramData%\KanzleiAI`
  reversibel umbenannt, nicht gelöscht) → First Run (Migration + Admin-
  Anlage) → Login → erzwungener Passwortwechsel → Neustart → Login erneut
  → Local-AI-Bootstrap MIT echtem, zuvor deinstalliertem Ollama (echte
  Neuinstallation + Modell-Download `qwen3:8b` trotz real beobachteter
  Netzwerk-Stalls, siehe Ollama-eigenes `server.log`) → echte lokale
  Inferenz (~96s) - alles über die tatsächlich installierte .exe, nicht
  den Entwicklungsbetrieb. Passwort-Recovery (`kanzlei_ai.exe
  reset-admin-password`, siehe `run.py::cmd_reset_admin_password`) real
  gegen einen bestehenden Admin verifiziert. Bekanntes Risiko dabei
  gefunden: siehe `OPEN_ISSUES.md` CRITICAL, Antivirus kann Teile des
  installierten Bundles nachträglich entfernen.
- Natives Windows-Fenster-Chrome (12.09.): Rückbau der bisherigen
  `frameless=True`-Lösung (Masterprompt V2 Task #61) auf natives
  Fenster-Chrome (`run.py::_serve_with_window`) - reale Win32-Style-Bits
  (`WS_CAPTION`/`WS_MAXIMIZEBOX`/`WS_MINIMIZEBOX`/`WS_THICKFRAME`, kein
  `WS_POPUP`) und `DwmGetWindowAttribute` (`DWMWA_WINDOW_CORNER_
  PREFERENCE=2`) bestätigen echtes natives Maximieren/Resize + native
  abgerundete Ecken. `_NativeApi`s vier alte JS-Methoden entfernt
  (dadurch deaktiviert sich die alte Custom-Titelleiste selbst, siehe
  deren Feature-Detection in `app_titlebar.js` - keine Template-/CSS-
  Änderung nötig).
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

**Redaktion (12.09., zweiter Release-Engineering-Run):** die beiden
vorherigen Zeilen dieses Abschnitts enthielten ein Test-Admin-Passwort
im Klartext - das ist ein Verstoss gegen die CLAUDE.md-Grundregel
"Niemals Secrets in Code oder Logs schreiben" (gilt auch für reine
Testinstanz-Passwörter) und wurde ersatzlos entfernt. Falls ein
Test-Login für eine lokale Instanz benötigt wird: neuen Admin über
`kanzlei_ai.exe create-admin` (ADMIN_EMAIL/ADMIN_INITIAL_PASSWORD als
Prozess-Umgebungsvariablen, NIE in Dateien) anlegen, nicht dokumentieren.

**Aktueller Release Candidate (12.09., Zero-Excuse-Release-Run NACH dem
First-Run-Fix in `run.py`):** `dist\installer\Lexono_Setup.exe`, SHA-256
`01351b5fd32ee9ed74911e1ead503d995195d92a5d7b68f7b49ff60557783239`,
525.416.953 Bytes, gebaut 12.09. ~20:18 Uhr, Commit
`90897cea90f4acf8137b72fcd16a75bff053a94c` (Working Tree). Installer-Hash
der vorherigen Baseline (`6b36026c...`, 18:51 Uhr) ist damit fuer den
Code-Zustand nach dem First-Run-Fix UNGÜLTIG (Change Invalidation Rule).
**Echter Endanwender-Fehler real behoben und verifiziert:** ein
tatsaechlich real gemeldeter P0-Fehler (Endanwender landet nach
Installation auf der Login-Seite ohne bekannte Zugangsdaten) wurde im
Code root-verursacht (`run.py::main()` pruefte nur `.env`-Praesenz, nicht
ob ein Benutzer existiert), minimal gefixt
(`_first_run_setup_required()`), und am tatsaechlich installierten,
frisch gebauten Release Candidate real reproduziert UND als behoben
verifiziert - echte Rekonstruktion des Fehlerzustands (fehlgeschlagene
`create-admin`, kein Trick/Mock), vorher: stiller Sprung zur Login-Seite;
nachher: korrekte Konsolenmeldung + erneuter Setup-Versuch. Voller
Clean-Room-Zyklus danach durchgefuehrt: frisches `%ProgramData%\KanzleiAI`
(reversibel umbenannt, nicht geloescht), echter First-Run-Pfad (Setup-
Assistent korrekt automatisch gestartet, real bestaetigt bis zur
Eingabeaufforderung - die eigentliche Tastatureingabe bleibt die bekannte,
seit laengerem dokumentierte `getpass`/Automatisierungsgrenze, siehe
OPEN_ISSUES.md GEKLAERT), Admin-Anlage, Login, erzwungener
Passwortwechsel, Neustart, erneuter Login, echter Chat-Workflow ueber den
tatsaechlichen HTTP-Endpunkt der installierten `.exe` (echte
Presidio-Erkennung, echter lokaler `qwen3:8b`-Aufruf, echter direkter
Anthropic-Aufruf, korrekte Rekonstruktion - nach zwei real beobachteten,
nicht mit dem Fix zusammenhaengenden Blockierungen durch bereits
bestehende, unveraenderte Datenschutz-/Qualitaetsgates ("Interner
Konsistenzfehler"/"moeglicherweise nicht erkannte Namen") gelang ein
vollstaendiger, echter Entwurf: "Sehr geehrte/r Max Mustermann, vielen
Dank fuer Ihre Nachfrage..." - kein Platzhalter-Leak). **Zusaetzlicher,
unerwarteter Befund waehrend dieses Laufs:** das zuvor als vollstaendig
bestaetigte Bundle verlor erneut selektiv `app/`/`migrations/`/
`presidio_analyzer/`/`de_core_news_lg/`/`tesseract/` (dieselbe
Antivirus-verdaechtige Symptomatik wie zuvor, siehe OPEN_ISSUES.md HIGH) -
durch Neuinstallation behoben, nicht Ursache dieses First-Run-Fixes.
Alle Test-Zugangsdaten nach Abschluss aus dem DATA_DIR entfernt, keine
Secrets in Logs/Dokumentation.

**Aktueller Release Candidate (12.09., Legacy-Cleanup-/Start.vbs-Fix-Run,
zweiter unabhaengiger Fund derselben Fehlerklasse):**
`dist\installer\Lexono_Setup.exe`, SHA-256
`a973cf1f66eb77beef3a3e3be5e62b22af78ba750ddbff9092b18385a8e0f258`,
525.424.998 Bytes, gebaut 12.09. ~21:46 Uhr, Commit
`90897cea90f4acf8137b72fcd16a75bff053a94c` (Working Tree). Installer-Hash
der vorherigen Baseline (`01351b5f...`, 20:18 Uhr) ist damit UNGÜLTIG.
**Zweiter, unabhaengiger Fund derselben Fehlerklasse:** `Start.vbs` (der
tatsaechliche Startmenue-/Desktop-Verknuepfungs-Mechanismus) hatte eine
eigene, unkorrigierte Kopie der exakt gleichen `.env`-Praesenz-Logik wie
der zuvor in `run.py` gefixte Fehler - entdeckt bei der Untersuchung eines
echten Nutzerberichts (`bonitzki@live.de`: First Run hatte tatsaechlich
funktioniert, echter Benutzer real in der DB, aber das einmalig gezeigte
Passwort war nicht mehr zugaenglich - vermutlich vom sofort folgenden
nativen Fenster verdeckt). Fix: `.setup_complete`-Marker (siehe
DECISIONS.md), real gegen die neu installierte `.exe` ueber echte
Produktions-Subprozessaufrufe verifiziert (Marker korrekt fehlend nach
echtem `create-admin`-Fehlschlag, korrekt vorhanden nach echtem Erfolg).
Voller Clean-Room-Zyklus mit einem GENUIN NEUEN Testkonto (nicht dem
echten Nutzerkonto) durchgefuehrt und bestanden: First Run, Login,
erzwungener Passwortwechsel, Neustart, erneuter Login, echter
Chat-Workflow (Presidio, lokaler `qwen3:8b`, direkter Claude-Aufruf,
korrekte Rekonstruktion - nach zwei real beobachteten, unveraenderten,
nicht mit diesem Fix zusammenhaengenden Blockierungen durch bereits
bestehende Qualitaetsgates gelang ein vollstaendiger echter Entwurf).
**Legacy-Artefakt-Analyse (expliziter Nutzerauftrag):** kein separates
`dist\KanzleiAI`-Verzeichnis, keine `KanzleiAI_Setup.exe` - nur EIN Spec,
EIN Installer-Output. `kanzlei_ai.exe`/`Start.vbs`/`%ProgramData%\KanzleiAI`/
`_internal\` sind alle Klasse A (produktiv, bewusst, ARCHITECTURE.md §59).
Der real betroffene Endnutzer wurde per `reset-admin-password` sofort
wieder zugangsfaehig gemacht (legitime Wiederherstellung des EIGENEN
echten Kontos, kein Clean-Room-Ersatz) - neues Passwort ausschliesslich
in der Konversation genannt, nirgends dokumentiert/geloggt.

**Silent-Install-Stall**: bleibt ein bekanntes, nicht zweifelsfrei
bewiesenes Risiko (siehe OPEN_ISSUES.md) - trat auch bei diesem
Reinstall nicht auf, aber das allein ist kein Beweis für eine Behebung.

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
