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
  **UPDATE 13.09. (P0 Performance-Follow-up)**: auf der real getesteten
  Referenzmaschine ist tatsächlich `qwen3:8b` konfiguriert (von der
  `RecommendationEngine` als `primary` gewählt, siehe MODEL_EVALUATION.md
  für einen realen erneuten Benchmark gegen `qwen2.5:1.5b` und einen
  dabei gefundenen, noch offenen Mismatch zwischen Katalogwissen und
  Auswahllogik, siehe OPEN_ISSUES.md). NEU: die beiden LLM-gestützten
  §65-Schritte (Vorabanalyse + semantische Antwortvalidierung) werden
  jetzt risikobasiert übersprungen für `chat_response`-Nachrichten ohne
  Aktendokument und ohne von Presidio erkanntes PII (siehe DECISIONS.md,
  `app/drafting/service.py::_should_skip_llm_privacy_layers`) -
  Presidio/Pseudonymisierung bleibt in JEDEM Fall Pflicht.
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
kein Widerspruch. Umgesetzt: `logo.svg` grün, Wortmarken-Text bleibt bei
`--ink-900`/navy. Neue, von `--seal-green` (bleibt Navy/Tinte für
generelle UI-Elemente) GETRENNTE Markenfarbe `--brand-green` für
Logo/Sendebutton/aktive Chat-Navigation. Die zuvor genannten Werte
(`#101828`/`#f8fafc`/`#ffffff`/`#64748b`) bleiben weiterhin als Ink-/
Canvas-/Card-Töne gültig - nur die Aussage "Logo ist rein Navy" ist
überholt. **NACHTRAG (19./20.09.)**: `--brand-green` war zunächst
`#16a34a` (Näherungswert) - der Owner gab danach den exakten CI-Wert
`#249D74` explizit vor (verifiziert gegen die tatsächlichen Pixelwerte
des `logo-mark.png`-Assets), seither aktueller Wert. Kein weiterer
offener Punkt.

## Installer

**Redaktion (12.09., zweiter Release-Engineering-Run):** die beiden
vorherigen Zeilen dieses Abschnitts enthielten ein Test-Admin-Passwort
im Klartext - das ist ein Verstoss gegen die CLAUDE.md-Grundregel
"Niemals Secrets in Code oder Logs schreiben" (gilt auch für reine
Testinstanz-Passwörter) und wurde ersatzlos entfernt. Falls ein
Test-Login für eine lokale Instanz benötigt wird: neuen Admin über
`kanzlei_ai.exe create-admin` (ADMIN_EMAIL/ADMIN_INITIAL_PASSWORD als
Prozess-Umgebungsvariablen, NIE in Dateien) anlegen, nicht dokumentieren.

**Aktueller Release Candidate (14.09., NACH dem Desktop-Blocker-Fix in
`Start.vbs`):** `dist\installer\Lexono_Setup.exe`, SHA-256
`9425E41FCD7FB89CAA26DFDEDA947FCFDF0357D16AC71DA8B9ABB1BC17062719`,
525.630.982 Bytes, gebaut 14.09. 23:19 Uhr (Working Tree auf Commit
`b17c2b1`). Real verifiziert, nicht nur gebaut:
- **Upgrade-Installation** `/VERYSILENT`, Exit 0. Beweiskraeftig gemacht,
  indem die installierte `Start.vbs` VORHER absichtlich verfaelscht wurde -
  danach stimmte ihr SHA-256 wieder exakt mit der Quelle ueberein, der
  Installer hat also tatsaechlich ersetzt (ein blosser Hashvergleich ohne
  diese Manipulation haette nichts bewiesen).
- **Clean-Installation** nach vorheriger Deinstallation, Exit 0.
  Deinstallation entfernt GENAU EIN Desktop-Element (die Verknuepfung),
  die Neuinstallation legt GENAU EINES wieder an - damit ist auch
  empirisch belegt, dass der Installer nur einen Desktop-Eintrag besitzt.
- **Datenverzeichnis ueberlebt die Deinstallation** (wie beabsichtigt, kein
  `[UninstallDelete]`): `data\kanzlei_ai.db` mit 40 Mandanten, 42 Akten,
  177 Fristen, 27 Chat-Unterhaltungen unveraendert vorhanden.
- **Start und Beenden hinterlassen kein zusaetzliches Desktop-Element**
  (Symbolgitter + alle drei Desktop-Ordner mit Groessen verglichen, in
  beiden Installationsvarianten jeweils "keine Aenderung"), keine
  Restprozesse.
- `app.log` landet jetzt in `%PROGRAMDATA%\Lexono\`, der Programmordner
  bleibt unberuehrt.

**Vorheriger Release Candidate (12.09., Zero-Excuse-Release-Run NACH dem
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

## Aktueller Stand nach "AUTONOMOUS PRODUCT COMPLETION MASTER DIRECTIVE" (14.09.)

Realer Zustand vor diesem Zyklus geprueft (Auftrag §38): Installer war
buildbar aber nicht verifiziert installiert; Streaming war umgesetzt,
aber lokale KI in der REALEN Produktionsinstanz war NICHT wie in der
Dev-Umgebung deaktiviert, sondern aktiv (`LOCAL_AI_ENABLED=true`,
`qwen3:8b`) und zeigte real einen 181s-Vorfall im echten Log.

**In diesem Zyklus erledigt (alle real getestet, nicht nur Code):**

1. **Ollama-Kaltstart-Fix** (`keep_alive: "30m"`) - real gemessen 144.67s
   kalt vs. 6.79-10.26s warm. Siehe DECISIONS.md.
2. **Realer Installer-Rebuild + Clean-Upgrade-Install + E2E-Smoke-Test**
   gegen die tatsaechlich installierte Produktionsinstanz (Login, Chat
   mit Dokument+PII, volle Pipeline inkl. echtem Ollama + echtem
   Claude, korrekte Rekonstruktion, danach vollstaendig aufgeraeumt).
3. **Gesetzesbibliothek massiv erweitert**: 22 echte Gesetze, 8049
   Normen (vorher: 1 Gesetz, 2518). Zwei echte Fundamentalfehler dabei
   gefunden und behoben (law_code-Jahres-Suffix; Artikel- vs.
   Paragraphen-Deep-Link-Schema). Chat-Fast-Path erkennt jetzt auch
   "Art"/"Artikel"-Zitate.

**Weiterhin offen (priorisiert nach Auftrag §6):**

4. **Modell-Benchmark abgeschlossen (14.09., "PERFORMANCE DECISION
   DIRECTIVE")** - `qwen2.5:1.5b` klar disqualifiziert (Timeouts +
   False-Positive-Validierung), `qwen2.5:7b-instruct` real getestet
   (qualitativ leicht besser, 9/9 vs. qwen3:8b 8/9), aber NICHT
   signifikant schneller (269s vs. 278s, ~3 %) - Entscheidungsmodell
   verlangt einen echten Geschwindigkeitsgewinn als harte Bedingung,
   daher KEIN Wechsel. `qwen3:8b` bleibt Baseline. Siehe DECISIONS.md.
   Diese Frage gilt als abschliessend untersucht, nicht mehr als "P1,
   noch zu benchmarken" offen zu fuehren.

- **P1 (umformuliert, s. o.)**: volle Pipeline (Dokument/PII) bleibt bei
  ~30-50s (drei sequenzielle KI-Aufrufe: Vorabanalyse -> Claude ->
  Validierung, real im Smoke-Test gemessen: 8.5s + 3.3s + 20.1s =
  34.19s). Modellwechsel wurde real geprueft und verworfen (s. o.).
  Kleineres `max_tokens` fuer `chat_response` wurde ebenfalls geprueft
  und bewusst NICHT umgesetzt (echtes Trunkierungs-/Qualitaetsrisiko bei
  laengeren Antworten, kein Zeitgewinn bei ohnehin kurzen - siehe
  DECISIONS.md). Keine weitere sichere Reduktion in diesem Zyklus
  gefunden - strukturell durch die Architektur bedingt (drei bereits
  einzeln optimierte KI-Aufrufe), keine Sicherheitsabstriche vorgenommen.
5. ~~Posteingang "Automatische Zuordnung"~~ — **ERLEDIGT (14.09.)**, real
   getestet + real installiert verifiziert (siehe DECISIONS.md). Dabei
   real gefunden UND behoben: die gesamte automatische Mail-Ingestion/
   Aktenzuordnung war zuvor NIE mit der laufenden Anwendung verbunden
   (vollstaendig implementiert, aber nie aufgerufen) - jetzt als
   Hintergrund-Task in `app/main.py` aktiv. Ausserdem real gefunden UND
   behoben: ein echter CSRF-Fund in `chat_router.py::link_matter`
   (nutzte `require_login` statt `require_role()`, dadurch fehlte die
   Token-Pruefung). Ein automatisierter Fehlalarm ("55 Endpunkte ohne
   CSRF-Pruefung") wurde durch eigenes Nachlesen des Quellcodes korrekt
   als falsch positiv erkannt und verworfen (nur 1 Endpunkt real
   betroffen) - siehe DECISIONS.md fuer die volle Einordnung.
   **Verbleibend, bewusst nicht in diesem Lauf**: kein Frist-Vorschlag in
   der Karte (nur Mandant/Akte).
6. ~~Sozialrecht-Kern (SGB I-XII)~~ — **ERLEDIGT (14.09.)**: 12 Buecher,
   3.115 echte Normen, real per Chat getestet (5 vorgegebene Testfragen).
   Dabei 3 echte Fehler gefunden+behoben (fehlende Verb-Phrase "was
   regelt"; mehrteiliges Kuerzel "SGB I"/"SGB 1" mit Leerzeichen; ein
   echter Duplikat-Parserfehler bei SGB XII). **Zusaetzlich real
   gefunden+behoben**: die GESAMTE bisherige Gesetzesbibliothek war bis
   dahin NUR in der Dev-DB, nie in der echten Produktionsdatenbank - jetzt
   alle 34 Gesetze (11.137 Normen, 0 Duplikate) real in Produktion
   importiert und per E2E-Chat-Test gegen die installierte Anwendung
   verifiziert. Siehe DECISIONS.md.
7. ~~Mail-Anhänge wurden nie extrahiert~~ — **BEHOBEN (14.09.)**, real
   gefunden beim Reassess nach dem SGB-Import (gleiche Fehlerklasse wie
   die Mail-Ingestion selbst, eine Ebene tiefer). `_run_periodic_mail_
   ingestion` verarbeitet jetzt jedes neue Dokument über
   `DocumentProcessingService`, identisch zu Chat-/Schriftsatz-Upload.
8. ~~`ClassificationService` projektweit nie verbunden~~ — **BEHOBEN
   (14.09.)**: `DocumentProcessingService.process_document` ruft
   `classify_document` jetzt zentral für alle drei Upload-Pfade auf.
   **Wichtige, real gemessene Einschränkung (kein neuer Bug, bewusstes
   Sicherheitsdesign)**: der `PlaceholderDocumentClassifier` deckelt
   seine Konfidenz auf 0.4, der Standard-Schwellwert ist 0.6 - "auto_
   assigned" für Nachrichten mit Anhang bleibt daher weiterhin
   unerreichbar, bis ein leistungsfähigerer Klassifikator existiert.
   Klassifikationsdaten sind aber jetzt erstmals real verfügbar. Siehe
   DECISIONS.md/OPEN_ISSUES.md.
9. ~~Realer Security-/Privacy-Regressionsfall "Frau Müller" (Overnight-
   Direktive §8)~~ — **TEILWEISE BEHOBEN (14.09.)**: root-caused (weder
   known_entities noch Presidio/spaCy-NER noch die deterministische
   Regel-Heuristik erkannten bisher einen blossen Nachnamen mit Anrede-/
   Rollenwort ohne Vornamen). Fix in `security_check.py::
   _find_possible_unrecognized_names` + `local_ai_provider.py::
   _build_known_entities`. Real E2E gegen die installierte Anwendung
   verifiziert (echter Block bei "Frau Müller ohne Matter-Kontext, kein
   Fehlalarm bei harmloser Nachricht). Bewusst offen gelassene
   Restlücke: ein komplett anredeloser Nachname ohne Aktenzuordnung
   bleibt strukturell unerkannt (ehrlich dokumentiert, kein
   false-green). Siehe DECISIONS.md/OPEN_ISSUES.md/TEST_STATE.md.
10. Performance-Benchmark der Kern-Workflows (Akte-Analyse 83,0s,
    Schriftsatz-Entwurf 105,5s, real gegen die installierte Anwendung
    gemessen) — **zwei echte Zusatzfunde**: (a) Strassennamen-Regex-
    Luecke ("Elbchaussee") — **BEHOBEN**; (b) NER-Durchlauf-
    Inkonsistenz bei Organisationsnamen — dokumentiert, bewusst nicht
    behoben (kein risikoarmer Fix erkennbar). **Wichtigster Befund**:
    beide Kern-Workflows liefern TTFR == TOTAL (kein Streaming-Feedback
    für 80-105s) — widerspricht dem eigenen Performance-/UX-Ziel der
    Overnight-Direktive, als P1 offen dokumentiert (OPEN_ISSUES.md).
11. UI/UX-Audit real begonnen (echte Screenshots der installierten
    Anwendung, echter simulierter Login, gegen `assets/ux-ui/` verglichen
    und tatsächlich implementiert, nicht nur dokumentiert):
    - Login-Bildschirm — **BEHOBEN** (Logo/Headline/Feature-Icons/
      Illustration/Karte vergrößert, CSS-only). Bewusst NICHT ergänzt:
      drei Login-Funktionen aus der Referenz ohne Backend-Support
      (remember-me, Passwort-vergessen, Nutzer wechseln) — tote UI wäre
      schlechter als die Lücke.
    - Chat-Startseite — **BEHOBEN** (Schnellaktions-Karten von
      kompakten Pillen auf farbig getönte vertikale Karten mit
      Untertitel umgestellt, inkl. real gefundenem CSS-Spezifitäts-Bug
      und fehlendem `text-decoration:none`, beide behoben).
    - Akten-Übersicht — **teilweise behoben**: fehlende Spalte "Letzte
      Aktivität" + farbiger Status-Punkt ergänzt. Zwei größere
      Abweichungen (Neue-Akte-Button+Löschfunktion, Dropdown-Filter)
      bewusst nicht nachgebaut — echte Produktentscheidungen bzw.
      destruktive Aktionen, kein risikoarmer UI-Abgleich. Siehe
      DECISIONS.md/OPEN_ISSUES.md.
    - Alle real am neu gebauten/installierten Build verifiziert
      (Hash-Verifikation + echter Screenshot). Verbleibende 38
      Referenzbilder noch nicht geprüft.
12. Synthetische **Steuerfachanwaltskanzlei** als wiederverwendbare Test-/
    Demobasis (Nachtrag, 14.09.): der BESTEHENDE Mechanismus
    (`app/synthetic_data/` + `scripts/seed_synthetic_data.py`, bereits
    steuerrechtlich ausgerichtet) wurde erweitert statt ersetzt — jetzt
    **idempotent, resetbar (`--reset`), deterministisch und sichtbar als
    Demo-Daten gekennzeichnet** (`DEMO-0001`…), mit vollständigen
    Mandantenstammdaten und **echten, extrahierbaren PDF-Dateien**
    (`--document-dir`). Damit real durchgespielter Workflow gegen den
    produktiven `DocumentProcessingService` — dabei **zwei echte
    Produktfehler gefunden und behoben**: (a) es gab keinen einzigen
    steuerrechtlichen Dokumenttyp (jeder Steuerbescheid war "Unbekannt"),
    (b) die Rechtsbehelfsbelehrung auf jedem Bescheid machte ihn
    fälschlich zum "Einspruch". Siehe DECISIONS.md.
13. **Posteingangs-Zuordnung (Kern des End-to-End-Benchmarks) real
    gemessen und verbessert (14.09.)**: auf der synthetischen
    Kanzlei-Datenbasis erhielt der Anwalt für **jede** eingehende
    Mandantenmail **keinerlei** Aktenvorschlag (8/8 `no_match`, Score
    0.30). Zwei strukturelle Fehler gefunden: der **Mandant selbst** wurde
    beim Namensabgleich nie berücksichtigt (nur `matter.parties`), und
    eine blosse E-Mail-Adresse wurde als "Anzeigename" behandelt und gegen
    Personennamen verglichen. Nach dem Fix: **8/8 `needs_review` (0.50)** —
    der Anwalt bekommt jetzt einen konkreten Vorschlag zum Bestätigen.
    **Korrigiert dabei eine frühere Annahme dieser Sitzung**: nicht die
    Klassifikations-Konfidenzgrenze war hier der Blocker (Gegenprobe mit
    `classification_ok=True` ergibt denselben Score 0.30). Schwellwerte
    bewusst NICHT verschoben — vollautomatische Zuordnung ohne
    Aktenzeichen-Treffer würde Aktenisolation riskieren.
14. **"Aufgaben & Fristen" zeigte KEINE einzige Frist (14.09., P1, BEHOBEN)**:
    die Seite fragte ausschließlich `Task` ab — ein Modell, das **kein
    Code-Pfad der Anwendung je erzeugt**. Gleichzeitig lagen 178 real
    erkannte `Deadline`-Sätze in der Datenbank, sichtbar nur in der
    Einzelakte. Für eine Kanzlei ist die versäumte Frist der
    folgenreichste Fehler überhaupt. Jetzt werden die erkannten Fristen
    angezeigt (überfällig als **Text** markiert, nicht nur farblich;
    Prüfstatus sichtbar; auf 50 begrenzt mit ehrlichem Gesamthinweis).
15. **Gold-Workflow-Eingangszustand fehlte in den Demo-Daten (14.09.,
    BEHOBEN)**: der Posteingang meldete "0 ohne Aktenzuordnung" — die
    gesamte Zuordnungs-Oberfläche war mit Demo-Daten nicht darstellbar.
    Jetzt kommt jeder dritte Fall als noch nicht zugeordnete Post herein.
    **Gold-Workflow-Segment real durchgespielt**: Vorschlag der korrekten
    Akte (50 %) → "Übernehmen" → Nachricht **und Anhang** der Akte
    zugeordnet, AuditEvent geschrieben — in der Datenbank verifiziert.
- **P2**: volle RAG-Integration der Gesetzesbibliothek in
  `LegalResearchService` (Fast Path bleibt auf reine Zitatfragen
  begrenzt).
- **Prozess-Risiko (nicht auftragsspezifisch, aber real)**: sehr grosser
  unkommittierter Git-Diff (viele Dateien seit mehreren Sitzungen nicht
  committet) - Nutzer wurde nicht gefragt, ob committet werden soll,
  daher bewusst unangetastet gelassen; sollte bei Gelegenheit adressiert
  werden (Reproduzierbarkeits-/Rollback-Risiko).

Bewusst zurueckgestellt (kein aktueller Auftrag): Rechtsprechungs-Modul
(BGH/BVerfG/BAG/BFH/BVerwG), Hetzner-Gateway-Produktivbetrieb (aktuell
kein Blocker), BORA (keine Primaerquelle auf gesetze-im-internet.de).

## Test-Baseline

Siehe `TEST_STATE.md` für den exakten, aktuell gültigen Stand.

## Git

Alle Änderungen lokal committet, **kein Push** (durchgehend eingehalten).
Working Tree sauber halten – vor jeder größeren Änderung `git status`
prüfen.

## Größte offene Workstreams

Siehe `OPEN_ISSUES.md` für die vollständige, kategorisierte Liste.
Zusammengefasst die wichtigsten: (1) ~~Logo-/Akzentfarben-Zielkonflikt
(Entscheidung ausstehend)~~ — **ÜBERHOLT (19./20.09.)**: exakter CI-Wert
`#249D74` vom Owner explizit vorgegeben und umgesetzt (`--brand-green` in
`app.css`, verifiziert gegen die tatsächlichen Pixelwerte von
`logo-mark.png`) — kein offener Punkt mehr. (2) Model-Evaluation über
mehrere Runtimes (Architektur-Readiness verifiziert, Umsetzung nicht
begonnen). (3) ~~echter Visual-QA-Screenshot-Loop (kein Tool
verfügbar)~~ — **ÜBERHOLT (14.09.)**: real funktionierender Screenshot-
Loop gegen die laufende, installierte Anwendung demonstriert und im
UI/UX-Audit produktiv genutzt (PowerShell `System.Drawing`/
`GetWindowRect` für den Screenshot, `mouse_event`/`SendKeys` für
simulierte Login-/Formulareingaben, Bild-Lesetool für den Vergleich
gegen `assets/ux-ui/`) — kein externes Tool nötig, funktioniert mit
Bordmitteln; siehe DECISIONS.md (Login-/Chat-Startseiten-Audit) für das
reale Vorgehen als Vorlage für weitere Referenzbild-Vergleiche. (4)
~~echtes PDF-Seiten-Rendering im Dokument-Workspace (bewusst
zurückgestellt)~~ — **ÜBERHOLT (20.09.)**: echter visueller
Dokumentviewer gebaut und live verifiziert (PDF UND DOCX, via PyMuPDF-
Seiten-Rendering, `app/documents/rendering.py`) — siehe OPEN_ISSUES.md
für die volle Dokumentation. (5) KI-Waiting-/Buffering-UX projektweit
(20.09.) — **ÜBERHOLT (20.09.)**: systematischer Audit + Fix für alle
fünf real gefundenen synchronen KI-Aufrufe ohne Waiting-Feedback, siehe
OPEN_ISSUES.md.

## Server-/Model-Registry-Architektur (Zielbild dokumentiert, 14.09.)

ARCHITECTURE.md §72: Model Registry + Legal-Source-Registry als
Erweiterung der bereits real existierenden Gateway-Infrastruktur (§70)
dokumentiert - NUR Dokumentation, kein Code, wie von der Overnight-
Direktive §19-24 explizit gefordert ("in den nächsten Tagen", jetzt nicht
implementieren). Baut bewusst auf Bestehendem auf (lokale `ModelCatalog`
§67, `gesetze_im_internet`-Import §24, App-Update-Checker) statt einer
zweiten, parallelen Architektur.

## Flow-First-Audit nach "AUTONOMOUS PRODUCT COMPLETION / FLOW-FIRST
UI/UX + REAL WORKFLOW EXECUTION"-Direktive (18.09.)

Direktive verlangt: nicht mehr einzelne IST→SOLL-Lücken abarbeiten,
sondern die 6 benannten End-to-End-Workflows live durchlaufen und ihre
tatsächliche Funktionsfähigkeit (nicht nur Quellcode-Existenz) belegen.
Dreizehnter Installer-Rebuild gebaut, installiert, SHA-256-verifiziert;
alle folgenden Befunde live gegen die installierte Instanz per
HTTP-Cookie-Verifikation (nicht nur Testsuite) geprüft:

- **FLOW 2 (Posteingang → Akte)**: kritischer, produktionsblockierender
  Fund + Fix in dieser Sitzung (siehe OPEN_ISSUES.md P0) - "Antworten"
  schlug real mit Pseudonymisierungs-Konsistenzfehler fehl. Behoben
  (`chat_triggered`-Signal in `DraftingService`), live gegen die exakte
  ursprüngliche Reproduktion (Sabine-Schmidt-Nachricht) verifiziert:
  echter, persistierter `Draft` entsteht jetzt statt Fehlermeldung.
  Zusätzlich verifiziert: "Anwalt prüft → bearbeitet → speichert" ist
  real (manuelles Edit erzeugt eine neue, persistierte Entwurfsversion
  mit dem bearbeiteten Inhalt - kein Fake-Save). Nachrichten-Detail →
  Akte-Link-Fund (dead-end, jetzt echter Link) ebenfalls in diesem Flow
  gefunden und behoben.
- **FLOW 1 (Mandant → Akte)**: Statuswechsel (abschließen/wieder
  öffnen), Dokumente-/Kommunikation-/Aufgaben & Fristen-/Verlauf-
  Sektionen auf der Akte-Detailseite alle live mit echten, aus der DB
  gelesenen Zahlen und funktionierenden Formularen bestätigt (keine
  hartcodierten Platzhalter). "Akte anlegen" ab Mandant-Seite bewusst
  über den Schriftsatz-Generator (nicht separates leeres Formular) -
  bereits am 17.09. bewusst gegen Mandanten-Dubletten abgesichert,
  kein Fund.
- **FLOW 3 (Dokument)**: öffnen → Vorschau (Dokumentinhalt) → 4 echte
  KI-Aktionen (analysieren/zusammenfassen/Daten extrahieren/Schriftsatz-
  Entwurf erstellen) - alle über dieselbe, jetzt reparierte Chat-Pipeline
  geroutet. Umbenennen/Herunterladen ebenfalls real.
- **FLOW 5 (Aufgaben/Fristen)**: manuelles Anlegen einer Frist (18.09.
  neu gebaut) live end-to-end verifiziert - persistiert korrekt nach
  Neuladen.
- **FLOW 4 (Schreiben)**: Briefkopf-/Signatur-Infrastruktur
  (`app/export/letterhead.py`) bereits projektweit vorhanden und vom
  neuen `DraftPdfExportService` bereits korrekt wiederverwendet (kein
  separater/paralleler Weg). Schriftsatz-Generator-Seite lädt fehlerfrei.
- **FLOW 6 (Chat)**: implizit durch alle obigen Chat-getriggerten
  Rundlaufe mitverifiziert (Intent → Aktenkontext → Aktion → Ergebnis →
  Speicherung funktioniert real).

Kein neuer Fund in Flow 1/3/4/5 über den bereits behobenen FLOW-2-Bug
hinaus - alle in dieser und der vorherigen Sitzung gebauten Features
bestehen die Live-Prüfung. Nächster Schritt: weitere Flow-Wiederholungen
nur bei konkretem Verdacht, sonst gezielte neue IST→SOLL-Suche (§9) oder
visuelle Qualitätsprüfung (§7) fortsetzen.

## Tiefen-E2E des Schriftsatz-Generators (Flow 4) - zwei echte Funde,
beide behoben (19.09., Owner-Direktive "CONTINUE AUTONOMOUS PRODUCT
COMPLETION" + "TARGETED TEST AUTHORIZATION + SECURITY-PRESERVING
VERIFICATION")

Der o.g. Flow-4-Haken "Schriftsatz-Generator-Seite lädt fehlerfrei" war
nur ein oberflächlicher Check - der angeforderte TIEFE E2E-Test (echte
Generierung → Entwurf → Bearbeitung → Freigabe → Export → Persistenz)
deckte zwei echte, aufeinanderfolgende Funde auf, siehe OPEN_ISSUES.md
für die volle Herleitung:

1. **Derselbe Pseudonymisierungs-Fund wie "Antworten" zuvor**, jetzt am
   kanonischen Schriftsatz-Generator-Weg selbst (message_id=None,
   chat_triggered=False - bis dahin der einzige Fall, der noch als
   "sollte volle Abdeckung brauchen" gehalten wurde). Root Cause:
   `prepare_draft_context` baut Mappings für JEDEN Zweck identisch aus
   der GESAMTEN Akte. Fix: `_RELAXED_COVERAGE_PURPOSES = {"chat_response",
   "formulate_draft"}` - **wichtige Selbstkorrektur während der
   Umsetzung**: ein erster Versuch setzte das Flag fälschlich
   UNBEDINGT für ALLE Zwecke (echter Scope-Fehler), von zwei sofort
   fehlschlagenden `improve_draft`-Regressionstests aufgedeckt und vom
   Owner per expliziter Direktive korrigiert, bevor weitergemacht wurde.
2. **Von der Lockerung selbst freigelegte Zweitfolge**: ein Claude-Aufruf
   kann `max_tokens` (2000) komplett verbrauchen, ohne sichtbaren Text zu
   liefern - live 3 von 3 Mal deterministisch reproduziert auf einer
   großen Akte. Vor der Lockerung wurde das als ZUFÄLLIGER Nebeneffekt
   der (jetzt entfernten) Vollständigkeitsprüfung abgefangen; danach wäre
   ein leerer Entwurf still als `success=True` durchgereicht worden -
   echter §4-Verstoß ("REAL OBJECTS - NO FAKE UI"). Fix: eigenständiger,
   purpose-unabhängiger Mindestinhalt-Check in BEIDEN Pfaden (streaming +
   nicht-streaming), plus eigene Fehlerkategorie
   (`empty_writing_response`) statt der irreführenden generischen
   "aus Datenschutzgründen blockiert"-Meldung.

**Sicherheitsprüfung** (Kontrollfluss nachverfolgt, nicht aus Testnamen
abgeleitet, siehe OPEN_ISSUES.md für Details): die beiden tatsächlich
schützenden Prüfungen in `check_response_placeholder_integrity`
(erfundene/veränderte Platzhalter-Tokens, geleakter Originalwert) laufen
unverändert für JEDEN Zweck; das separate, strengere ausgehende Final
Payload Gate (`check_payload_placeholder_integrity`) ist von beiden Fixes
komplett unberührt.

**Vierzehnter Installer-Rebuild** (drei Iterationen in dieser Sitzung, je
mit SHA-256-Verifikation), volle Testsuite final: 2023 passed, 1 skipped,
0 failed. Live-E2E vollständig durchlaufen: Generierung → echter Entwurf
→ manuelle Bearbeitung (neue Version) → erneutes Öffnen (Persistenz) →
Freigabe → PDF-Export (200, echte Bytes) → DOCX-Export (200, echte Bytes)
→ erneutes Öffnen nach Freigabe (Persistenz bestätigt) - alles per HTTP
gegen die SHA-256-verifizierte installierte Instanz, keine Mocks. Fail-
Closed-Gegenbeweis über die bestehenden, gegen die reale Pipeline
laufenden Manipulations-/Leck-Tests (kein Versuch, eine echte Claude-
Antwort live zum Leaken zu bewegen - unzuverlässig und nicht
zielführend). Test-Artefakte aus der QA-Fixture-Akte aufgeräumt.

Flow 4 (Schreiben) gilt damit als tief E2E-geprüft, nicht nur
oberflächlich als "Seite lädt".

## Zweite Eskalation desselben Pseudonymisierungs-Funds + UI/UX-
Referenzsweep-Runde (19.09., Owner-Direktiven "CONTINUE AUTONOMOUS
PRODUCT COMPLETION" / "TARGETED TEST AUTHORIZATION")

**Root-Cause-Fix, korrigiert unter Owner-Aufsicht**: die
`require_full_placeholder_coverage`-Lockerung (s. o.) wurde in einem
ersten Versuch faelschlich UNBEDINGT fuer ALLE Zwecke gesetzt statt nur
fuer die beiden live belegten (`chat_response`/`formulate_draft`) - ein
echter Scope-Fehler, von zwei sofort fehlschlagenden `improve_draft`-
Regressionstests aufgedeckt, nicht durch Testschwaechung verdeckt. Nach
Korrektur zusaetzlich EIN ECHTER, VON DER LOCKERUNG SELBST FREIGELEGTER
Zweitfund: ein Claude-Aufruf kann `max_tokens` komplett verbrauchen ohne
sichtbaren Text - live 3/3 Mal reproduziert vor dem Fix, 2/2 Mal
erfolgreich danach (das Auftreten selbst ist modellseitig
wahrscheinlichkeitsbasiert). Eigenstaendiger Mindestinhalt-Check in
beiden Pfaden (streaming + nicht-streaming) ergaenzt, plus eigene
Fehlerkategorie (`empty_writing_response`) statt der irrefuehrenden
"aus Datenschutzgruenden blockiert"-Meldung. Alle Sicherheits-Checks
(Manipulation/Leck-Erkennung, ausgehendes Final Payload Gate)
nachweislich unberuehrt. Volle Kette: 2019 → 2022 → 2023 passed ueber
drei Iterationen, jedes Mal 0 failed.

**UI/UX-Referenzsweep, fünf real behobene Lücken** (jede: gebaut →
getestet → Installer-Rebuild → SHA-256-verifiziert installiert → per
HTTP UND per echtem GUI-Screenshot gegen die installierte Instanz
geprüft):
1. Akten-Übersicht: Zeilen-Schnellzugriffsmenü (öffnen/Chat/archivieren)
   - deckte dabei einen echten CSS-Bug auf (`.row-menu[hidden]` wurde
   von einer spezifischeren Regel überschrieben, Menüs blieben dauerhaft
   sichtbar) - per Screenshot gefunden UND per Screenshot als behoben
   bestätigt.
2. Mandanten-Übersicht: dasselbe Muster, diesmal ohne den CSS-Fund (aus
   dem ersten Mal gelernt).
3. Akte-Detailseite: von einer langen Einzelseite auf die in DREI
   unabhängigen Referenzbildern konsistent gezeigte Tab-Architektur
   umgestellt (Übersicht/Dokumente/Kommunikation/Aufgaben & Fristen/
   Beteiligte/Notizen/Verlauf) + neues `Note`-Modell (vorher projektweit
   nicht existent).
4. Sidebar-Fuß (jede Seite): der 18.09. gebaute `display_name` hatte
   ausgerechnet hier, der sichtbarsten Stelle im Produkt, keine Wirkung.
5. Mandanten-Detailseite: dasselbe Tab-Muster wie bei Akten, `Note`
   dafür generalisiert (nullable `matter_id`/`client_id`, GENAU eines
   gesetzt, Migration `schritt3_014`) statt eines zweiten Modells;
   "Aufgaben & Fristen" auf Mandantenebene über alle Akten aggregiert.

Bei JEDEM Fund wurde nach §8 (Decompose) explizit geprüft, was
decision-independent gebaut werden kann und was zurückgestellt bleibt -
mehrfach wiederkehrend zurückgestellt: strukturierte KI-Analyse-UI
(Schweregrad-klassifizierte Befunde), PDF-Live-Vorschau im
Dokument-Workspace, Dokumentkategorien/Verschieben/Kopieren, Multi-
Briefkopf-Verwaltung, beA-Anbindung, E-Mail-Versand - alle bereits an
anderer Stelle als groß/decision-dependent identifiziert, hier bei jedem
erneuten Auftauchen bestätigt statt erneut diskutiert.

Zwanzig Installer-Rebuilds in dieser Sitzungsrunde, jeder SHA-256-
verifiziert. Volle Testsuite am Ende: 2039 passed, 1 skipped, 0 failed.

**Nächster Schritt**: weitere Referenzbild-Stichproben zeigen zunehmend
bereits behobene oder bereits korrekt zurückgestellte Muster (z. B.
"03_posteingang_uebersicht.png" trotz Namens erneut derselbe
Dokumentkategorien-Workspace) - Ertrag der reinen Referenzabgleich-
Strategie sinkt (§9). Für die nächste Runde: entweder gezielt noch nicht
gesampelte, einfachere Seiten (Posteingang-Liste selbst, Kanzleiwissen)
prüfen, oder zur Flow-Vervollständigung zurückkehren.

## Installer-/Live-Verifikations-Zyklus (24.09., "AUTONOMOUS
RELEASE-READINESS CONTINUATION")

Erster Build+Install+E2E-Zyklus seit dem letzten Commit (`b17c2b1`,
13.09.) - bestätigt, dass der GESAMTE seit dem 13.09. unkommittierte
Arbeitsstand (145 Dateien, siehe "Prozess-Risiko" unten) tatsächlich
baut, installiert und real läuft, nicht nur Quellcode-seitig existiert.
PyInstaller+Inno-Setup-Build erfolgreich (Exit 0), Silent-Install
erfolgreich (Exit 0, kein Guardrail-Treffer diesmal), installierte
`Lexono.exe` SHA-256-identisch zum frischen Build-Artefakt. DB-Migration
automatisch auf aktuellem Kopf (`schritt3_016`). Volle Regressionssuite
zu Beginn bestätigt: 2154 passed/1 skipped/0 failed.

Da die Bildschirmumgebung dieser Sitzung ein geteiltes, vom Nutzer aktiv
genutztes Desktop mit überlagernden Browser-Fenstern zeigt (native
UI-Automatisierung würde reales Risiko einer Fehlinteraktion mit
Nutzer-Fenstern bergen, s. DECISIONS.md), wurde die Tiefenverifikation
stattdessen per echtem HTTP-Zyklus gegen den laufenden installierten
Server durchgeführt (etabliertes Ausweichmuster dieses Projekts): Login
(inkl. erzwungenem Passwortwechsel des QA-Testkontos), 20-Seiten-
Navigations-Sweep (alle 200), manuelle Frist-Anlage+Prüfstatus-Änderung,
und ein vollständiger Gold-Workflow-Tiefendurchlauf (Posteingang-Nachricht
→ "Antworten" → echter Presidio→Ollama `qwen3:8b`→Anthropic-Claude→
Rekonstruktion, placeholder-leck-frei) inkl. echter PDF-/DOCX-Export-
Validierung (Byte-Inhalt extrahiert und geprüft, nicht nur "200 OK").
Alle Test-Artefakte danach entfernt (Audit-Trail bewusst nicht
mitgelöscht - vom Auto-Mode-Filter korrekt als "Logging/Audit Tampering"
blockiert, nicht umgangen).

Kein neuer Produktfund in dieser Runde (der Fund-Suchprozess davor - siehe
DECISIONS.md - lieferte nur den bereits als Owner-Entscheidung
dokumentierten `SourceService`/"Rechtsquellen"-Kandidaten, keinen neuen).
GUI-Referenzbild-Abgleich bleibt für eine Sitzung mit unkompliziertem
Bildschirmzugriff offen - ehrlich als NV vermerkt.

## Fortsetzung (24.09., "ROADMAP-ALIGNED PRODUCT COMPLETION"): zweiter Erbschaftsteuer-Komplexfall + echter Session-Timing-Sicherheitsfund

Nach der obigen Kontextkorrektur (Installer-/Runtime-Sweep war reine
Reproduzierbarkeits-Bestaetigung, keine neue Roadmap-Arbeit) zwei echte,
entscheidungsunabhaengige Punkte umgesetzt:

1. **`generate_complex_case_erbschaftsteuer()`** - der am 20.09. bereits
   als zulaessig entschiedene, aber nie gebaute zweite Komplexfall-Typ
   (Rechtsgebiet "Erbschaftsteuer", Aktenzeichen-Kuerzel "ErbSt" existierte
   schon vorher). 5 verbundene Dokumente, echte Frist, echter Einspruchs-
   Entwurf. `_FIRMENNAMEN_MUSTER` um Einzelunternehmen/Personen-
   gesellschaften erweitert (Direktive §10: erkennbare Rechtsform-Vielfalt).
   12 neue Tests, CLI real getestet, ein echter Fall in die Produktions-DB
   gesaet (`2026/0735-ErbSt`). WORKSTREAM B bleibt "TEILWEISE" (Details
   siehe OPEN_ISSUES.md).
2. **ECHTER, live reproduzierter Sicherheitsnaher Fund**: Passwortaenderung
   + sofortiger Neu-Login (73ms Abstand, real gemessen) fuehrte wegen
   unterschiedlicher Zeitstempel-Praezision (itsdangerous sekundengenau vs.
   `sessions_invalidated_after` mikrosekundengenau) zu einer faelschlich
   sofort verworfenen, voellig legitimen Session. Fix in
   `app/auth/session.py` (eigenes, mikrosekundengenaues `issued_at`-Feld
   im signierten Payload). Ein erster, unsicherer Loesungsversuch (grobes
   Sekunden-Abschneiden von `invalidated_after`) haette eine ECHTE
   Session-Widerruf-Luecke wiedereroeffnet - von bestehenden Tests sofort
   aufgedeckt, verworfen, nicht committet. Volle Herleitung siehe
   DECISIONS.md. Volle Suite: 2167 passed, 1 skipped, 0 failed.
   **Live-QA: VERIFIZIERT** - Installer neu gebaut + installiert (Hash-
   identisch), GENAU die urspruengliche Fehlerreproduktion zweimal gegen
   die frisch installierte Instanz wiederholt (196ms bzw. 191ms Abstand
   zwischen Passwortaenderung und Neu-Login, jeweils DIESELBE Wanduhr-
   Sekunde) - beide Male jetzt 200 statt der vorherigen 303. Details siehe
   OPEN_ISSUES.md/AGENT_HANDOFFS.md.

`SourceService`/Rechtsquellen und `Policy`/Kanzleiregeln bleiben
unveraendert Owner-Entscheidungen, nicht angefasst. Kein Commit.

## Fortsetzung 2 (24.09., Owner-Direktive "HARD ROADMAP PRIORITY / PRODUCT COMPLETION CONTINUATION"): Workload vergroessert, Golden-Path-E2E bewiesen, zweiter echter Sicherheitsnaher Fund

Direktive: keine weitere Installer-/Local-AI-/Infrastrukturarbeit ohne
konkreten neuen Befund - Fokus auf UI/Workflow/realistische Testkanzlei/
Workload/E2E. Umgesetzt:

1. **Workload vergroessert**: `--count 25 --complex-cases 4` in die reale
   Produktions-DB gesaet - jetzt 41 Demo-Mandanten (vorher 12), 94 Akten,
   96 Dokumente, 118 Entwuerfe, alle vier Rechtsformen aus
   `_FIRMENNAMEN_MUSTER` real vertreten. Workload-Check (Mandantensuche/
   -uebersicht, Posteingang, Entwuerfe, Aufgaben & Fristen,
   Gesetzesbibliothek) per HTTP: alle 200, 8-110ms - keine
   Performance-Auffaelligkeit.
2. **Zweiter echter Sicherheitsnaher Fund**: Presidio-NER erkannte das
   isolierte Wort "Erbschaftsteuerbescheid" faelschlich als PERSON und
   blockierte dadurch JEDE KI-Aktion auf Erbschaftsteuer-Dokumenten
   (Final Payload Gate, `original_value_leaked`, kein Claude-Aufruf fand
   je statt). Root-Cause per instrumentiertem Direktaufruf gegen die
   reale Produktions-DB lokalisiert. Fix: `_NEVER_ENTITY_WORDS`
   (bestehender Mechanismus seit Prompt 28) um das eine, konkret belegte
   Wort ergaenzt - keine neue Architektur. 1 neuer Test, Rot→Gruen-Beweis.
   Volle Suite: 2168 passed, 1 skipped, 0 failed.
3. **Voller Golden-Path-E2E bewiesen** (Mandant suchen → Profil → Akte →
   Dokument → echter Seitenbild-Viewer → KI-Shortcut "Antwort formulieren"
   → echter Entwurf → manuelle Bearbeitung → neue Version persistiert →
   echter PDF-Export → echter DOCX-Export → in der Akte wiedergefunden) -
   alle 13 Schritte erfolgreich, auf dem neu vergroesserten Datenbestand,
   getestet gegen den Python-Dev-Server (identischer Code, reale
   Produktions-DB) statt eines weiteren Installer-Rebuilds (Owner-Direktive
   §17: kein Rebuild ohne konkreten Installer-Befund). Alle Test-Artefakte
   danach vollstaendig entfernt.

Volle Herleitung beider Funde siehe DECISIONS.md/OPEN_ISSUES.md.
`SourceService`/`Policy` weiterhin unangetastet. Kein Commit.

## Fortsetzung 3 (24.09., Owner-Direktive "PRODUCT COMPLETION MODE"): Entwurf-Editor frisch gegen Referenzbilder geprueft

Frischer, direkter Blick (Bilder selbst angesehen, nicht nur alte
Notizen) auf `12_dokument_editor.png`/`24_dokument_editor_ki_assistent.
png`/`38_dokumenteditor_ki_vorschlaege.png`/`39_schreiben_draft_und_ki_
assistent.png` gegen `draft_detail.html`. Ergebnis: EIN echter, klar
umsetzbarer Unterschied gefunden und umgesetzt (feste "Vorschläge"-
Schnellaktionszeile: Formulierung präzisieren/Text kürzen/Rechtliche
Prüfung/Ton anpassen - reines Vorausfuellen ueber dieselbe bestehende
Infrastruktur wie die Standard-Prompts). Alle anderen sichtbaren
Unterschiede (Rich-Text-Toolbar, strukturierte Betreff-/Empfaenger-Felder,
kartenbasierte KI-Analyse-Ausgabe mit kontextuellen Folgeaktionen, "Als
Aktendokument speichern") wurden gegengeprueft und bestaetigt als bereits
dokumentierte FALL-3-Architekturfragen bzw. (im letzten Fall) als mit der
strengeren Lexono-Aktenisolations-Architektur unvereinbar - siehe
DECISIONS.md fuer die volle Einzelbegruendung. 2 neue Tests, volle Suite
2170 passed/1 skipped/0 failed. Kein Commit.

Danach zweiter Fund im selben Referenz-Sweep (§9, Posteingang): die
Zuordnungs-Karte (`04_posteingang_nachricht_detail.png`) zeigt ein
drittes Feld "Frist" neben Mandant/Akte - eine seit dem 14.09.-Eintrag
explizit als offen dokumentierte Luecke ("kein Frist-Vorschlag in der
Karte"), jetzt geschlossen: reine Vorschau ueber die bereits bestehende
`PlaceholderDeadlineExtractor`-Funktion, kein neuer Schreibpfad. 4 neue
Tests (beide Renderpfade), volle Suite 2174 passed/1 skipped/0 failed.
Kein Commit.

## Finaler Installer-Rebuild dieser Sitzung (24.09., auf explizite Nutzeranfrage)

Dritter und letzter Rebuild dieser Sitzung, buendelt ALLE fuenf realen
Funde: Session-Timing-Sicherheitsfix, Presidio-NER-Fix,
Erbschaftsteuer-Komplexfall + Mandantentyp-Vielfalt, Entwurf-Editor-
"Vorschläge", Posteingang-"Erkannte Frist"-Vorschau.

**Aktueller Release-Kandidat**: `dist\installer\Lexono_Setup.exe`, SHA-256
`72203A37A00F1B11F191D2BD2299AAFC14C28B4579B5C4931A64A1C616731317`.
Installierte `Lexono.exe` SHA-256 identisch zum Build
(`BCAC10C77783274DA4CE6825E04E4C725555F7488F2879CE7BD7ADB05E8B82B1`).

Umfassender Abschluss-Smoketest gegen die installierte Instanz: alle
fuenf Funde in einem Lauf real gegengeprueft (Auth-Timing 217ms-Abstand-
Neu-Login → 200; Presidio-NER-Fix auf dem echten Erbschaftsteuerbescheid-
Dokument → nicht blockiert; Vorschläge-Zeile sichtbar; Erkannte-Frist-
Vorschau sichtbar; 41 Demo-Mandanten/94 Akten bestaetigt) - **5/5 PASS**.
Test-Artefakte durch den Smoketest selbst entfernt. Volle Herleitung
siehe AGENT_HANDOFFS.md. Kein Commit.
