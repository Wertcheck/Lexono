# OPEN_ISSUES – Technische Schulden & offene Workstreams

Kategorien: CRITICAL / HIGH / MEDIUM / LOW / FUTURE.
Kein Eintrag hier bedeutet automatisch Untätigkeit – Einträge werden aktiv
von den zuständigen Agenten (siehe `agents/`) abgearbeitet oder bewusst
zurückgestellt (mit Begründung).

## CRITICAL — BEHOBEN (12.09., Zero-Excuse-Release-Run)

- **P0-Produktfehler: echter Endanwender landet nach Installation auf der
  Login-Seite OHNE erreichbare Zugangsdaten (real vom Nutzer gemeldet,
  real reproduziert, Root Cause im Code bewiesen)**: `run.py::main()`
  entschied "Ersteinrichtung noetig?" AUSSCHLIESSLICH anhand von
  `env_path.exists()` - `.env` wird aber als ALLERERSTER Schritt von
  `run_setup_wizard()` geschrieben, VOR Migration und Admin-Anlage
  (`app/setup/wizard.py`). Schlaegt einer dieser beiden spaeteren,
  tatsaechlich ladungstragenden Schritte fehl (ein einzelner Subprozess-/
  DB-Fehler genuegt, real reproduziert durch bewusstes Weglassen von
  `ADMIN_EMAIL`), bleibt `.env` bestehen, OHNE dass je ein Admin angelegt
  wurde. Jeder folgende Start wertete das als "Ersteinrichtung bereits
  erfolgt" und oeffnete direkt die Login-Seite - fuer einen echten
  Endanwender eine Sackgasse ohne bekannte Zugangsdaten. Eine bestehende
  Testdatei (`tests/test_run_entrypoint.py::test_main_serve_skips_setup_when_env_already_exists`)
  kodierte dieses fehlerhafte Verhalten faelschlich als "korrekt" - real
  gefundener Beweis, dass der Fehler bereits laenger bestand, nicht neu
  eingefuehrt wurde.
  **Fix (minimal, generisch, hardwareunabhaengig):** neue Funktion
  `run._first_run_setup_required(data_dir)` prueft zusaetzlich, ob
  mindestens ein Benutzer tatsaechlich in der Datenbank existiert (nicht
  nur `.env`-Praesenz); bei fehlendem Benutzer wird `cmd_setup(data_dir,
  force=True)` erneut aufgerufen (`force=True`, da `write_env_file`
  sonst mit `FileExistsError` abbricht - dieses Recovery-Muster war
  bereits im `run_setup_wizard`-Docstring als vorgesehen dokumentiert,
  nur nie tatsaechlich verdrahtet). Kein Hardcoding, keine Test-
  Credentials, keine historischen Zugangsdaten, keine Entwickleraktion
  erforderlich - funktioniert generisch auf jeder Windows-Installation.
  **Real verifiziert** (nicht nur Unit-Test): am tatsaechlich installierten,
  neu gebauten Release Candidate (SHA-256 siehe PROJECT_STATE.md) real
  reproduziert (Setup-Assistent wurde nach fehlgeschlagener
  Admin-Anlage korrekt erneut gestartet, Konsolenmeldung "Unvollstaendige
  Ersteinrichtung erkannt..." statt stillschweigendem Sprung zur
  Login-Seite) UND der volle Folgeablauf (Admin anlegen, Login,
  erzwungener Passwortwechsel, Neustart, erneuter Login, Chat mit echtem
  Presidio/lokalem `qwen3:8b`/direktem Claude-Aufruf, korrekte
  Rekonstruktion) real bestaetigt. 3 neue Regressionstests, 1 bestehender
  Test korrigiert (kodierte zuvor das fehlerhafte Verhalten).

- **P0-Produktfehler, zweiter, unabhaengiger Fund derselben Fehlerklasse:
  `Start.vbs` (der tatsaechliche Startmenue-/Desktop-Verknuepfungs-Mechanismus,
  `installer.iss` Zeile 158/160/181) entschied die Konsolen-Sichtbarkeit
  komplett unabhaengig von `run.py::main()` und ebenfalls anhand von
  `.env`-Praesenz allein - derselbe Denkfehler wie oben, nur in VBScript
  dupliziert.** Real beim Endanwender aufgetreten: ein echter Nutzer
  (`bonitzki@live.de`) hatte den Setup-Assistenten tatsaechlich erfolgreich
  durchlaufen (echter Benutzer real in der DB bestaetigt, `created_at`
  19:28:03), kannte aber sein Passwort nicht mehr (voraussichtlich: das
  einmalig angezeigte Passwort wurde von der sich sofort danach oeffnenden
  nativen Fensterinstanz verdeckt) - **das war KEIN First-Run-Fehler**
  (First Run hatte technisch funktioniert), sondern zeigt den zweiten,
  unabhaengigen Bug: bei einem KUENFTIGEN fehlgeschlagenen Setup-Versuch
  (z. B. Admin-Anlage schlaegt fehl, `.env` existiert trotzdem bereits)
  haette Start.vbs den erneuten, von `run.py::main()` korrekt ausgeloesten
  Setup-Wiederholungsversuch STUMM/UNSICHTBAR gestartet - fuer den Nutzer
  nicht von einem Haenger zu unterscheiden. **Fix:** `app/setup/wizard.py::
  run_setup_wizard()` schreibt jetzt einen `.setup_complete`-Marker ERST
  nach tatsaechlich erfolgreicher Admin-Anlage (nicht gleichzeitig mit
  `.env`); `Start.vbs` prueft jetzt `.setup_complete` statt `.env` (VBScript
  hat keinen SQLite-Treiber, kann also nicht wie `run.py::main()` direkt in
  der DB nachsehen - der Marker ist die naechstbeste, bewusst konservative
  Annaeherung). Real gegen die neu gebaute, installierte `.exe` verifiziert:
  ueber die REALEN, produktiven Subprozess-Aufrufe (`_run_migrate_subprocess`/
  `_run_create_admin_subprocess`, exakt wie `cmd_setup()` sie nutzt) wird der
  Marker nach echtem Erfolg gesetzt; ein echter fehlgeschlagener
  `create-admin`-Aufruf (fehlende `ADMIN_EMAIL`) laesst ihn korrekt fehlen.
  Der real betroffene Nutzer wurde sofort per `reset-admin-password`
  (legitime Account-Wiederherstellung fuer sein EIGENES echtes Konto, NICHT
  als Clean-Room-Nachweis verwendet) wieder zugangsfaehig gemacht - neues
  Passwort ausschliesslich in der Konversation, nie in Datei/Log/Report
  festgehalten. 4 Regressionstests (2 neu in `test_setup_wizard.py`, 1 neu +
  1 korrigiert in `test_start_vbs.py`).
  **Legacy-Artefakt-Analyse (auf ausdruecklichen Nutzerauftrag durchgefuehrt):**
  `kanzlei_ai.exe`, `Start.vbs`, `%ProgramData%\KanzleiAI`, `_internal\`
  waren zum Zeitpunkt dieser Analyse alle Klasse A (produktiv erforderlich).
  **UPDATE 12.09. (spaeter am selben Tag): diese Klassifizierung wurde per
  explizitem, erweitertem Nutzerauftrag AUFGEHOBEN** - vollstaendiger Rename
  durchgefuehrt (`windows/lexono.spec`, Paketname `lexono`, Session-Cookie,
  Backup-/Log-Dateinamen, Thread-Name, `Start.vbs`, verbleibende
  CLI-Hinweistexte in Templates). Siehe DECISIONS.md ("Supersedes...").
  Kein separates `dist\KanzleiAI`-Verzeichnis und keine
  `KanzleiAI_Setup.exe` existieren im Repository oder Build-Output (verifiziert
  per Verzeichnis-Listing) - nur EIN Spec (jetzt `windows/lexono.spec`), EIN
  Installer-Output (`Lexono_Setup.exe`). Kein Zusammenhang zwischen
  Legacy-Benennung und dem First-Run-/Login-Fehler gefunden - die tatsaechliche
  Ursache war ausschliesslich die oben beschriebene `.env`-Praesenz-Logik in
  zwei Dateien.

## MEDIUM — MITIGATED

- **Speicherdruck bei gleichzeitigem Presidio+FastEmbed+geladenem
  Ollama-Modell - Root Cause gefunden, minimaler Fix angewendet (12.09.,
  P1-Speicherdruck-Root-Cause-Untersuchung, real gemessen)**: auf der
  16-GB-Referenzmaschine (CPU-only) fiel der freie Arbeitsspeicher beim
  gleichzeitigen Laden von spaCy/Presidio (+938 MB), dem echten
  `FastEmbedProvider`-Multilingual-Modell (+1,71 GB) UND einem bereits im
  Speicher gehaltenen `qwen3:8b`-Ollama-Modell (separater
  `llama-server`-Prozess, ~5,6 GB) auf **unter 1 GB frei** - dabei
  reproduzierte sich real (zweimal) ein `HTTP 404` beim tatsaechlichen
  `/api/generate`-Aufruf gegen Ollama, obwohl `/api/ps` das Modell als
  geladen auswies; nach Entlastung (Speicher wieder >3 GB frei) verhielt
  sich Ollama wieder normal - der Fehler korrelierte eindeutig mit dem
  Speicherdruckzeitpunkt, nicht mit einem dauerhaften Ollama-Defekt.
  **Root Cause (Code-verifiziert, nicht nur vermutet):** `app/search/
  service.py`s `search_within_matter`/`search_knowledge_base`/
  `search_sources` riefen `self.embedding_provider.embed(query)`
  bedingungslos auf, auch wenn die jeweilige Kandidatenliste (Dokumente/
  freigegebene Wissensbausteine/freigegebene Quellen) leer war - das
  Ergebnis ist in diesem Fall so oder so leer, das reale Laden des
  FastEmbed-Modells (+1,71 GB) war damit fuer eine neue Akte/Kanzlei ohne
  bestehende Wissensbasis reine, unnoetige Ressourcenbindung, nicht
  funktional erforderlich. **Fix (minimal, Verhalten bei nicht-leerer
  Kandidatenliste unveraendert):** `query_vector` wird jetzt erst
  berechnet, wenn tatsaechlich mindestens ein Kandidat vorhanden ist,
  in allen drei Methoden. 4 neue Regressionstests
  (`tests/test_search_service.py`), volle Suite: 1526 bestanden/1
  Skip/0 Fehlschlaege. **Real vor/nach gemessen:** vorher Presidio+
  FastEmbed zusammen ~2,68 GB Prozessspeicher; nachher ein echter
  vollstaendiger `create_draft()`-Aufruf gegen eine leere Wissensbasis
  nur ~1,00 GB (= Presidio-Kosten allein, FastEmbed nicht mehr geladen).
  **Verbleibendes, bewusst nicht angefasstes Risiko:** `qwen3:8b`
  (~5,6 GB als separater Ollama-Prozess) bleibt der groesste Einzelposten,
  wenn Local AI aktiviert ist - unveraendert RISK_ACCEPTED (siehe
  `LEXONO_MASTER_PRODUCT.md` Drift #2, Nutzervorgabe "qwen3:8b NICHT
  einfach ersetzen"). Status hier: **MITIGATED**, nicht VERIFIED RESOLVED
  - eine reale Kanzlei MIT bereits gefuellter Wissensbasis/Quellensammlung
  wird weiterhin das volle FastEmbed-Gewicht tragen (dann aber fuer einen
  echten Suchtreffer, nicht mehr unnoetig). Vor dem naechsten echten
  Piloteinsatz empfohlen: neuen Installer bauen (release-relevante
  Code-Aenderung) und den vollen Clean-Room-Zyklus erneut durchlaufen.

  **Release-Validierung (12.09., spaeter, "Release Candidate Validation
  After Memory-Pressure Fix"-Lauf):** mit echter Laufzeitevidenz (nicht
  nur Code-Inspektion) bestaetigt - leerer Korpus: `_model is None` bleibt
  nach drei realen Aufrufen `True` (kein Laden), Ergebnis korrekt leer,
  keine Exception. Nicht-leerer Korpus (echte synthetische Dokumente/
  Wissensbausteine/Quellen, echtes FastEmbed-Modell, kein Fake):
  `_model` wechselt beim ersten Aufruf zu `False` (= geladen), alle drei
  Suchmethoden liefern echte semantische Treffer (Score bis 0,64) -
  semantische Suche also NICHT versehentlich deaktiviert. Voller
  Drafting-Workflow mit echtem, nicht-leerem Korpus + echtem Ollama
  (`qwen3:8b`, 103,9s) + echter Anthropic-API lief vollstaendig durch
  (Speicher sank dabei real auf ~1,0 GB frei, aber ohne Absturz) - der
  finale Entwurf wurde von der VORBESTEHENDEN, vom Fix unabhaengigen
  Antwort-Qualitaetspruefung korrekt blockiert (fehlender Platzhalter in
  Claudes Antworttext), kein Regressionsfund. Neuer Installer (SHA-256
  `6b36026c...`, siehe PROJECT_STATE.md) gebaut und real clean-room
  installiert; echter HTTP-Chat-Aufruf gegen genau diese `.exe` (nicht
  nur Dev-venv) mit echtem, nicht-leerem PII-Text lief erfolgreich durch
  (Presidio → Pseudonymisierung → lokales `qwen3:8b` → echter Claude →
  korrekte Rekonstruktion, kein Platzhalter-Leak im finalen Render).

## HIGH

- **Antivirus/Windows-Defender kann Teile der installierten Anwendung
  nachträglich entfernen (12.09., real beobachtet, Referenzmaschine
  i5-1145G7) - Klassifikation D, mit neuer Gegenevidenz gegen A
  (12.09., zweite Untersuchung)**: eine frisch installierte
  Lexono-Instanz lief zunächst korrekt (Login/Local AI erfolgreich
  getestet), zeigte aber beim nächsten First-Run-Test plötzlich
  `alembic.util.exc.CommandError: No 'script_location' key found in
  configuration` beim Migrieren. Forensik: `%LocalAppData%\Lexono\_internal`
  enthielt nur noch 127 MB (30 Top-Level-Dateien) statt der echten 1,1 GB
  (60+ Verzeichnisse, u. a. `app/`, `migrations/`, `presidio_analyzer/`,
  `spacy/`, `de_core_news_lg/`, `tesseract/` KOMPLETT fehlend) - ein
  sofortiger Reinstall aus demselben, unveränderten `Lexono_Setup.exe`
  erzeugte reproduzierbar wieder die vollständigen 1,1 GB. Der Installer
  selbst ist damit NICHT die Ursache (Root Cause bewiesen, nicht nur
  vermutet).
  **Neue Untersuchung (zweiter Durchlauf):** `Microsoft-Windows-Windows
  Defender/Operational`-Log vollständig ausgewertet (Retention deckt
  16.10.2025 bis heute ab, 702 Events im Abrufzeitraum, damit auch das
  ursprüngliche Vorfallsfenster) - KEIN einziges Detection-/Quarantäne-/
  Remediation-Event (IDs 1006-1117) und KEIN Controlled-Folder-Access-/
  ASR-Event (IDs 1121-1127) im gesamten Log; ebenso keine passenden
  Application-Log-Einträge zu kanzlei/Lexono/PyInstaller. Damit gibt es
  jetzt aktive Gegenevidenz gegen die Windows-Defender-Standarderkennung
  als Ursache - NICHT mehr nur "unbewiesen", sondern durch Log-Abwesenheit
  aktiv unwahrscheinlicher gemacht. **Klassifikation bleibt D (aktuell
  nicht reproduzierbar) statt A** (kein Nachweis für Defender) **und auch
  nicht B** (keine andere konkrete Ursache bewiesen) - der ursprüngliche
  Mechanismus bleibt technisch ungeklärt, aber das Risiko ist
  niedriger einzuschätzen als zuvor angenommen. Bei erneutem Auftreten:
  gezielt nach Defender-Log-Einträgen IM Vorfallsfenster suchen (Timeline
  T0-T6) sowie Ereignisanzeige/Sicherheitslog auf andere
  Sicherheitssoftware prüfen. Nächster Schritt unverändert empfohlen vor
  Pilotbetrieb: Code-Signing der PyInstaller-Ausgabe (reduziert das
  Risiko unabhängig von der ungeklärten Ursache).

  **Dritter, live waehrend dieser Sitzung reproduzierter Vorfall (12.09.,
  ~19:00-20:10 Uhr):** dieselbe, kurz zuvor real als vollstaendig (1022 MB,
  alle kritischen Verzeichnisse, Hash-verifiziert) bestaetigte Installation
  (`C:\Users\Bonit\AppData\Local\Lexono`, exe-Hash `8775fc6c...`
  unveraendert) schrumpfte binnen ca. einer Stunde auf 95 MB - exakt
  dieselbe `alembic.util.exc.CommandError: No 'script_location' key
  found`-Fehlermeldung wie beim ersten Vorfall. **Neu diesmal:** ein
  klares, selektives Muster erkennbar - vollstaendig fehlend: `app/`,
  `migrations/`, `presidio_analyzer/`, `de_core_news_lg/`, `tesseract/`
  (grosse, kanzleispezifische/unsignierte/ausfuehrbare Inhalte);
  vollstaendig UNVERAENDERT erhalten: alle generischen, signierten
  System-/Drittanbieter-Bibliotheken (`numpy`, `lxml`, `sqlalchemy`,
  `pymupdf`, `VCRUNTIME140.dll`, `.pyd`-Dateien usw.). Erneut KEIN
  Defender-Detection-/Quarantaene-Event im relevanten Zeitfenster
  gefunden (nur harmlose 1150/1151-Scan-Infoeintraege), `Get-MpThreatDetection`
  weiterhin ohne Adminrechte nicht einsehbar. Das selektive Muster ist
  ein staerkerer (aber weiterhin nicht abschliessender) Hinweis auf ein
  heuristik-basiertes Sicherheitsprodukt als auf zufaellige Korruption -
  Klassifikation bleibt **D**, jetzt aber mit qualitativ neuer,
  musterbasierter Evidenz statt nur Groessenverlust. Wurde durch
  Neuinstallation behoben (siehe PROJECT_STATE.md); Code-Signing-Empfehlung
  unveraendert, jetzt mit hoeherer Dringlichkeit.

## GEKLÄRT

- **`local-ai-setup` im echten Clean-Room-Test (Ollama zu Beginn
  nachweislich nicht installiert) wirkte scheinbar "gehängt" (12.09., real
  beobachtet, dann real widerlegt)**: Testskript rief den echten
  `local-ai-setup`-Befehl über `subprocess.run(..., timeout=1800)` gegen
  die installierte `.exe` auf; nach 1800s (30 Min.) warf Python
  `TimeoutExpired`, das Testskript meldete Exit-Code 1. Forensik im
  selben DATA_DIR danach: `ollama.exe`/`ollama app.exe` liefen
  nachweislich weiter (Prozessstart 13:31 Uhr laut `Get-Process`),
  `ollama list` zeigte `qwen3:8b` (5.2 GB) vollständig und mit
  korrektem Digest vorhanden; die App-Logdatei im selben Verzeichnis
  zeigt bei einem regulären Neustart um 14:32:08 bereits
  "Lokale KI bereit (Modell 'qwen3:8b')" - und `LocalAiSetupService`
  persistiert `LOCAL_AI_ENABLED`/`OLLAMA_MODEL` in `.env` laut
  `setup_orchestrator.py` AUSSCHLIESSLICH bei tatsächlichem Erfolg
  (Hardware→Modellwahl→Ollama-Install→Modell-Download→Health-Check
  alle erfolgreich durchlaufen). D. h. der reale Setup-Vorgang ist
  tatsächlich vollständig und korrekt durchgelaufen; nur mein
  Test-Timeout (30 Min.) war für die tatsächliche Download-Dauer auf
  dieser Netzwerkverbindung zu knapp bemessen - kein Lexono-Bug,
  sondern eine zu kurze Testharness-Zeitschranke. NICHT abschliessend
  erklärt (bewusst als offen markiert, nicht spekuliert): Ollamas
  `modified_at` für das Modell zeigt 15:30:43 Uhr, rund eine Stunde
  NACH dem ersten "bereit"-Log-Eintrag (14:32:08) im selben
  Datenverzeichnis - Ursache dieser Differenz nicht durch Log-Evidenz
  belegt, daher hier bewusst nicht spekulativ erklärt.
  **Verbleibender, echter P1-Punkt (kein Blocker):** In
  `setup_orchestrator.py`/dem zugehörigen UI-Flow wurde KEINE
  Fortschrittsanzeige (Prozent/Byte-Fortschritt) während eines langen
  Modell-Downloads gefunden - unklar/ungeprüft, ob ein echter Nutzer
  während eines mehrere-zig-Minuten-Downloads eine erkennbare
  "läuft noch"-Rückmeldung sieht oder der Bildschirm dabei wie
  eingefroren wirkt. Empfehlung vor Pilotbetrieb: UI-Fortschritts-
  anzeige für den Modell-Download ergänzen oder zumindest verifizieren,
  dass ein Wartehinweis sichtbar ist.

- **`getpass.getpass()` in `cmd_setup` kann nicht automatisiert (CI/Skript)
  getestet werden (12.09., real diagnostiziert)** - unter Windows liest
  `getpass.getpass()` ueber `msvcrt.getwch()` direkt aus dem Konsolen-
  Eingabepuffer, NICHT aus `sys.stdin` - jede Form von Stdin-Umleitung
  (Pipe, Datei, .NET-Stream) wird dabei vollstaendig ignoriert, der Aufruf
  blockiert dann dauerhaft. Kein Lexono-Bug: ein echter Benutzer an einer
  echten, sichtbaren Konsole (genau das, was Start.vbs beim allerersten
  Start oeffnet) ist davon nicht betroffen. Betrifft nur zukuenftige
  automatisierte End-to-End-Tests des interaktiven Setup-Assistenten -
  ein Test muesste echte Tastatur-Events in ein echtes, sichtbares
  Konsolenfenster injizieren (z. B. SendKeys), reine Stdin-Umleitung
  reicht dafuer nicht aus.

## GEKLÄRT (vormals "Produktentscheidung erforderlich")

- **Logo-/Akzentfarbe: grün vs. Navy `#101828`** – **ENDGÜLTIG GEKLÄRT,
  AKTUALISIERT (01.09., später, Referenzbild-Redesign).** Zwischenstand
  (Product Completion Cycle, s. u.) ging von einem rein navyfarbenen
  Logo aus, basierend auf einer textuellen CI-Beschreibung ("Primary/
  Logo Tone: #101828"). Eine DANACH vom Nutzer bereitgestellte konkrete
  Bild-Referenz zeigt jedoch eindeutig ein GRÜNES Schild-/Logo-Icon mit
  weißem Kettensymbol UND eine separate navyfarbene "Lexono"-Wortmarke -
  kein Widerspruch, sondern zwei unterschiedliche Elemente (Icon-Farbe
  vs. Text-/Ink-Farbe). Umgesetzt: `logo.svg` jetzt grün (`#16a34a`,
  Commit im Redesign-Batch), Wortmarke bleibt navy/`--ink-900`. Neue,
  von `--seal-green` (bleibt Navy für generelle UI-Elemente) GETRENNTE
  Markenfarbe `--brand-green` für Logo/Sendebutton/aktive Chat-
  Navigation. Die übrigen drei CI-Werte (Canvas `#f8fafc`, Cards
  `#ffffff`, Secondary Text `#64748b`) bleiben unverändert gültig. Die
  vier Schnellaktions-Akzentfarben (`--accent-blue`/`-purple`/`-orange`,
  jetzt auch `--accent-amber`) nutzen bewusst weiterhin NICHT Grün
  (bleibt exklusive Markenfarbe).

- **`PROMPT38_ANALYSIS.md` (Repo-Root)**: dokumentiert eine abgeschlossene
  ANALYSE zu "Multi-Kanzlei-Profile + Cross-Tenant-Tests", explizit
  markiert "Implementierung noch NICHT begonnen. Kein Code geändert."
  Ob Lexono mehrere Kanzleien in einer Instanz unterstützen soll, ist
  eine Produktentscheidung, keine rein technische - nicht ungefragt
  begonnen. Bei Bedarf: `PROMPT38_ANALYSIS.md` zuerst lesen, dann mit dem
  Nutzer klären, ob/wann das noch relevant ist.

## HIGH

- **Installer-Silent-Install-Stall: Root-Cause-Untersuchung (01.09.,
  Reliability & Deployment Hardening Cycle)** — **REPRODUZIERT** (echte,
  eindeutige Messung in dieser Sitzung, nicht nur Vermutung), **URSACHE
  NICHT ZWEIFELSFREI BEWIESEN, ABER GUT GESTUETZTE HYPOTHESE**, **KEIN
  Fix am eigentlichen Mechanismus** (siehe unten, warum).

  **Symptom**: Bei einem frischen `Lexono_Setup.exe /VERYSILENT
  /SUPPRESSMSGBOXES`-Lauf blieb die CPU-Zeit des `Lexono_Setup`-Prozesses
  exakt 60+ Sekunden lang bei konstant 0,17s (7 Messpunkte im 10s-Raster,
  keine Aenderung), OHNE Kindprozesse, BEVOR die eigentliche
  Datei-Extraktion sichtbar begann. Danach lief die Installation normal
  durch (Gesamtdauer 92s: Start 13:25:51, "Installation process
  succeeded" 13:27:08, WebView2-Schritt 13:27:08-13:27:23, Log
  geschlossen 13:27:23 - siehe `%TEMP%\lexono_freshinstall_log.txt`).

  **Reproduktionsschritte** (genau wie durchgefuehrt):
  1. Bestehende Installation deinstallieren (`unins000.exe /VERYSILENT
     /SUPPRESSMSGBOXES`) - lief sauber in 3,1s durch.
  2. `Lexono_Setup.exe /VERYSILENT /SUPPRESSMSGBOXES /LOG=<pfad>` starten.
  3. Alle 10s `Get-Process Lexono_Setup*` (TotalProcessorTime) UND
     Kindprozess-Anzahl protokollieren.
  4. Ergebnis: 60s lang keinerlei CPU-Fortschritt, dann normaler
     Abschluss.

  **Root-Cause-Hypothesen, geordnet nach Evidenzstaerke**:
  1. **Antivirus-/Cloud-Reputations-Scan des frisch gebauten,
     UNSIGNIERTEN ~525-MB-Installer-Executables beim ersten Ausfuehren**
     (gut gestuetzt, nicht bewiesen). Belege: (a) Windows-Defender-
     Echtzeitschutz nachweislich aktiv waehrend des exakten Stall-Fensters
     (Get-WinEvent-Health-Report-Event um 13:26:17, mitten im
     Beobachtungsfenster, RTP-Status: Aktiviert); (b) `Get-AuthenticodeSignature`
     bestaetigt: WEDER `Lexono_Setup.exe` NOCH `kanzlei_ai.exe` sind
     code-signiert (Status: NotSigned) - unsignierte, "low prevalence"
     Executables loesen bei Windows Defender/SmartScreen typischerweise
     zusaetzliche Cloud-Lookups aus; (c) unabhaengig gemessene,
     inkonsistente Netzwerklatenz zu Microsoft-Endpunkten in dieser
     Umgebung (TCP-Connect zu go.microsoft.com: 6,25s, zu zwei anderen
     microsoft.com-Subdomains: 0,3s) - ein cloud-abhaengiger
     Reputations-Check waere genau von solcher Latenz betroffen; (d) KEIN
     Application-/Defender-Operational-Log-Eintrag, der einen konkreten
     Scan-Vorgang fuer genau diese Datei zeigt (Defender protokolliert
     routinemaessige On-Access-Scans nicht standardmaessig, nur Funde) -
     daher NICHT zweifelsfrei bewiesen, nur konsistent mit allen
     verfuegbaren Indizien.
  2. **WebView2-Bootstrapper-Schritt (`waituntilterminated`, kein
     Timeout)** - durch unabhaengige Review-Delegation GEPRUEFT UND ALS
     UNWAHRSCHEINLICHE URSACHE FUER DIESES SYMPTOM EINGESTUFT: Inno
     Setups `[Run]`-Eintraege laufen laut Dokumentation ERST NACH dem
     vollstaendigen `[Files]`-Kopiervorgang, waehrend der beobachtete
     Stall eindeutig VOR jedem sichtbaren Extraktions-Fortschritt und
     OHNE Kindprozess auftrat (der WebView2-Bootstrapper waere als
     Kindprozess sichtbar gewesen). Bleibt trotzdem als eigenstaendiges,
     noch unbehobenes Risiko fuer ein ANDERES Szenario im Blick (siehe
     "Isolierte Bootstrapper-Tests" unten).

  **Isolierte Bootstrapper-Tests** (separate Diagnose, nicht der
  eigentliche Fund): `MicrosoftEdgeWebview2Setup.exe /silent /install`
  fuenfmal direkt (ausserhalb des Gesamtinstallers) ausgefuehrt - immer
  konsistent 7-8s, immer derselbe Exit-Code (`0x80040828`, laut
  Web-Recherche vermutlich ein regulaerer "bereits installiert/nichts zu
  tun"-Fruehausstieg, siehe auch das bekannte, von Microsoft selbst als
  "tracked"/"priority-low" gefuehrte Verhalten, dass der Bootstrapper bei
  `/silent /install` teils VOR Abschluss der eigentlichen Installation
  zurueckkehrt: https://github.com/MicrosoftEdge/WebView2Feedback/issues/1349).
  Kein Hang in diesen 5 isolierten Laeufen - bestaetigt die obige
  Einstufung als unwahrscheinliche Ursache fuer DIESEN Stall.

  **Was NICHT getan wurde (bewusst)**: Windows-Defender-Echtzeitschutz
  wurde NICHT deaktiviert und KEINE Ausschlussregel fuer den
  Build-/Installationsordner angelegt - beides waere eine
  sicherheitsrelevante Systemaenderung, die laut Standardregel nicht ohne
  ausdrueckliche Nutzerfreigabe vorgenommen wird. Eine
  Defender-Ausschlussregel waere der naheliegendste naechste Diagnose-
  /Verifikationsschritt (wuerde bei zutreffender Hypothese den Stall
  zuverlaessig zum Verschwinden bringen) - **empfohlen fuer den naechsten
  Zyklus, MIT Nutzerfreigabe**.

  **Tatsaechlich umgesetzter Fix (kleinerer, aber echter Reliability-Gap,
  gefunden bei derselben Review-Delegation)**: `installer.iss` hatte
  bisher KEIN `AppMutex` gesetzt, obwohl `run.py` selbst einen benannten
  Single-Instance-Mutex fuehrt (`Lexono_SingleInstance_Mutex`,
  `CreateMutexW`). Ohne `AppMutex` erkennt der Installer eine laufende
  Lexono-Instanz nicht - ein Reinstall/Update waehrend die App im
  Hintergrund laeuft (real moeglich, da `Start.vbs` sie unsichtbar
  startet, kein Tray-Icon als Erinnerung) haette laufende .exe-/DLL-
  Dateien mitten im Kopiervorgang sperren und im ungünstigsten Fall ein
  teilweise ueberschriebenes Installationsverzeichnis hinterlassen
  koennen. Behoben: `AppMutex=Lexono_SingleInstance_Mutex` ergaenzt
  (Commit siehe AGENT_HANDOFFS.md). Dies behebt NICHT den oben
  beschriebenen Silent-Install-Stall (anderes Problem), ist aber ein
  echter, unabhaengig vom Stall bestehender Reliability-Fund.

  **Ebenfalls gefunden, NICHT behoben (Produktentscheidung/Aufwand
  erforderlich)**: weder `Lexono_Setup.exe` noch `kanzlei_ai.exe` sind
  code-signiert. Das ist sowohl ein eigenstaendiges Vertrauens-/
  SmartScreen-Problem fuer einen echten Kanzlei-Rollout (unbekannter
  Herausgeber-Warnhinweis bei jeder Erstinstallation) als auch ein
  moeglicher Beitrag zum obigen Stall-Symptom. Erfordert ein echtes
  Code-Signing-Zertifikat (Kauf/Beschaffung bei einer Zertifizierungs-
  stelle) - eine Beschaffungs-/Kostenentscheidung, keine rein technische
  Aenderung, daher NICHT eigenmaechtig umgesetzt. **Empfehlung fuer eine
  Nutzerentscheidung.**

  **Verbleibendes Risiko**: der Silent-Install-Stall kann weiterhin
  auftreten (in 2 von 3 realen Testlaeufen dieser Sitzung TRAT ER NICHT
  auf, in 1 von 3 SCHON - keine 100%ige Reproduktionsrate, konsistent mit
  einer netzwerk-/cache-abhaengigen Ursache). Fuer einen echten
  Kanzlei-Rollout: IT-Administratoren sollten vorgewarnt werden, dass ein
  scheinbar haengender Installationsvorgang (mehrere Minuten ohne
  sichtbaren Fortschritt) nicht zwingend ein Fehler ist und in den bisher
  beobachteten Faellen von selbst abschloss - NICHT vorschnell abbrechen.

- **Dokument-Workspace mit Pseudonymisierungs-Highlighting** (Referenzbild
  2 aus Masterprompt V2) - **01.09. umgesetzt (V, real getestet)**: neue
  Route `GET /dashboard/chat/{conversation_id}/document/{document_id}`
  (`app/web/chat_router.py::chat_document_view`) zeigt den bereits lokal
  extrahierten Dokumenttext (`Document.extracted_text`) mit inline
  hervorgehobenen erkannten Kategorien (`app/chat/document_preview.py`,
  nutzt exakt dieselben Detektoren wie der echte Pseudonymisierungslauf)
  plus rechter Kontextleiste "Erkannte Mandantendaten" plus eingeklappter
  Unterhaltungsliste. Aktenisolation getestet (`ChatService.
  get_attached_document`, Dokument aus fremder Konversation liefert 404-
  Redirect). 5 neue Tests (test_document_preview.py,
  test_chat_service.py, test_web_chat.py), volle Suite grün.
  **Bewusst NICHT umgesetzt**: echtes PDF-Seiten-Rendering mit Zoom/Print/
  Seitennavigation wie im Referenzbild (bräuchte eine PDF.js-artige
  Client-Bibliothek + Koordinaten-Mapping der Presidio-Spans auf
  Pixel-Positionen im PDF - deutlich größerer, eigener Workstream). Die
  aktuelle Lösung zeigt stattdessen den EXTRAHIERTEN TEXT (denselben, den
  auch die KI tatsächlich verarbeitet) mit Text-Span-Highlighting - liefert
  dieselbe Kernfunktion (sehen, was als sensibel erkannt wurde), aber
  nicht die pixelgenaue PDF-Optik. Zuständig für eine spätere PDF-Variante:
  Agent D (Chat/Document Workspace) + Agent B (Frontend).
  Status: V (Textbasierte Variante), OFFEN (echtes PDF-Rendering).

- **Model Evaluation Engine über mehrere Runtimes/Modelle** (Masterprompt
  V2 §15–17): Bisher nur Ollama + 3 Qwen/Llama-Modellvarianten real
  benchmarkt (siehe MODEL_EVALUATION.md). Llama.cpp und weitere
  Modellfamilien (Mistral, Gemma) noch nicht evaluiert; keine
  wiederverwendbare Scoring-Engine mit repräsentativen
  Kanzlei-Testaufgaben vorhanden. Zuständig: Agent F (Local AI/Model
  Engineer).
  **01.09. konkretisiert**: Architektur-Readiness verifiziert (Protocol-
  basiert, `Settings.local_ai_runtime` + `ModelCatalogEntry.runtime`
  bereits als Erweiterungspunkte vorhanden, siehe MODEL_EVALUATION.md) -
  eine echte llama.cpp-Runtime-Integration bleibt ein eigener,
  mehrstündiger Download-/Kompilier-/Messaufwand und wurde bewusst NICHT
  begonnen (unverhältnismäßig).
  **01.09., später: viertes Modell real benchmarkt** - `gemma2:2b`
  (bounded, nur ein weiteres Ollama-Modell, kein neuer Runtime-Aufwand)
  real getestet: langsamer (18,2s warm vs. 10-11s bei qwen2.5:1.5b) UND
  bei einem Pflichtkriterium (Platzhaltererhaltung) unzuverlässig - zwei
  reale Fälle von veränderten/verlorenen Platzhaltern in nur zwei
  Testläufen, siehe MODEL_EVALUATION.md für Details. `qwen2.5:1.5b`
  bleibt datenbasiert bestätigt die beste Wahl - kein Konfigurationswechsel.
  Status: NV (llama.cpp-Runtime), V (Mistral/Gemma-Modellfamilie
  stichprobenhaft evaluiert - gemma2:2b negativ beschieden), Architektur-
  Readiness V (verifiziert).

## MEDIUM

- **Visual QA Loop (§23)**: **TEILWEISE, 01.09. später erweitert.** Echte
  UI-Automatisierung (Klicks + Texteingabe im nativen Fenster, nicht nur
  Screenshots) ermöglichte Navigation zu echten Zuständen (Login → Chat
  mit Unterhaltung → Dokument-Workspace) und deckte dabei einen realen
  Layout-Bug auf (behoben, Commit `b92e1cb`, siehe VISUAL_QA.md).
  1366×768/1920×1080 bleiben in dieser Umgebung NICHT testbar (Bildschirm
  nur 1024×768 - physische Umgebungsgrenze, verifiziert, kein
  Anwendungsfehler). Zuständig: Agent J (Visual QA).
  Status: TEILWEISE, siehe VISUAL_QA.md für Details.

- **ERLEDIGT, hier nur zur Nachvollziehbarkeit erwähnt**: "Fenster-Chrome"
  (frameless statt nativ) und "Statusindikatoren global in der Sidebar"
  standen hier vorher als offen - beides ist seit 01.09. umgesetzt und
  getestet (Fenster-Chrome zusätzlich vom Nutzer real am Schließen-Button
  bestätigt). Siehe `PROJECT_STATE.md` für den aktuellen Stand,
  `SESSION_LOG.md` für den Verlauf.

## LOW

- **UNBESTÄTIGTE Beobachtung: leerer unterer Fensterbereich + fehlende
  Titelleiste bei automatisierten Screenshots (01.09., später)** - bei
  mehreren Screenshot-Versuchen (auch nach frischem Neustart, auch nach
  18s Wartezeit) zeigte sich konsistent ein leerer weißer Bereich im
  unteren Achtel des Fensters plus fehlende Titelleiste. Ausdruecklich
  NICHT als Bug gewertet - widerspricht der direkten Nutzerbestaetigung
  ("x button closes the app") am selben Build, und ein Kontrolltest
  (erweiterte Aufnahme ueber den Fensterrand hinaus) war technisch nicht
  schluessig (weiss auf weiss, siehe VISUAL_QA.md fuer Details). Nur als
  Beobachtungspunkt vermerkt - bei Gelegenheit einmal mit echten Augen
  pruefen, ob Titelleiste und unterer Fensterbereich normal aussehen.

- **`move_window_by`/`resize_window_by` ohne Bildschirm-Clamp**
  (`run.py::_NativeApi`, gefunden bei unabhängiger Sicherheitsdurchsicht
  01.09.): kein oberes Limit und keine Prüfung, ob das Fenster (bzw.
  zumindest die Titelleiste) noch sichtbar bleibt - ein Fenster könnte
  theoretisch vollständig aus dem sichtbaren Bereich gezogen werden, ohne
  offensichtlichen Weg, es zurückzuholen (kein Alt+Leertaste-Menü
  garantiert). Ausdrücklich KEIN Sicherheitsproblem (Review-Ergebnis:
  "acceptable for a fully trusted first-party desktop shell"), nur
  UX-Robustheit. Nicht behoben, da spekulativ (bräuchte
  Bildschirmgrößen-Erkennung für einen sauberen Clamp) und durch reines
  Draggen der eigenen Titelleiste in der Praxis kaum auslösbar.

- **Update 01.09., ~11:20 Uhr**: Der FÜNFTE Rebuild (Dokument-Workspace-
  Schnellaktionen) installierte beim ERSTEN Versuch ohne jeden Stall
  (~60-90s Gesamtdauer, CPU-Monitoring zeigte durchgehende Aktivität statt
  einer flachen Kurve). Bestätigt also NICHT, dass das Problem behoben
  ist (nur ein einzelner erneuter Erfolg, wie auch bei den ersten drei
  Rebuilds der Nacht) - aber auch kein erneutes Auftreten. Weiterhin als
  bekanntes, nicht abschließend geklärtes Risiko für künftige Rebuilds
  vermerkt, keine Ursachenänderung.

- **Silent-Install-Stall eskalierte beim VIERTEN Rebuild derselben Nacht
  zu einem PERSISTENTEN Problem (01.09., ~10:00 Uhr)**: bei den ersten
  drei Rebuilds der Nacht loeste sich ein Haenger zuverlaessig nach 1-2
  Kill+Retry-Versuchen. Beim vierten (letzten) Rebuild der Nacht
  (Bündel: Titelleisten-Padding-Fix + Akzentfarben) haengten sich VIER
  aufeinanderfolgende Versuche auf, davon einer sogar nach 8+ Minuten
  konstant bei ~0% CPU (nicht nur 5 Minuten wie zuvor). Diagnose
  durchgefuehrt statt blind weiter zu versuchen: `Get-MpPreference` zeigt
  `DisableRealtimeMonitoring: False` (Windows-Defender-Echtzeitschutz
  aktiv - weiterhin Hauptverdaechtiger, NICHT deaktiviert, da das eine
  sicherheitsrelevante Systemaenderung waere, die nicht ohne Rueckfrage
  vorgenommen wird). Keine Application-Log-Fehler im relevanten Zeitraum
  gefunden (Prozess haengt, stuerzt aber nicht ab - passt zur
  Scan-Blockade-Theorie, nicht zu einem Absturz). Nach dem vierten
  gescheiterten Versuch bewusst GESTOPPT statt endlos weiterzuversuchen -
  das Installationsverzeichnis wurde als unbeschaedigt verifiziert (der
  vorherige erfolgreiche Build von 07:53 Uhr blieb intakt und lauffaehig,
  wurde fuer den Nutzer gestartet). **Der neueste Installer
  (Titelleisten-Padding-Fix + Akzentfarben) liegt fertig gebaut unter
  `dist/installer/Lexono_Setup.exe` vor, konnte aber in dieser Sitzung
  NICHT mehr erfolgreich real installiert werden - naechster Versuch
  sollte idealerweise mit einer Windows-Defender-Ausnahme fuer den
  Installationsordner beginnen (erfordert Nutzerfreigabe) oder einfach zu
  einem anderen Zeitpunkt erneut versucht werden.**

- **Silent-Install haengt gelegentlich beim ersten Versuch (wiederholt
  beobachtet, 01.09., drei separate Installer-Rebuilds derselben Nacht)**:
  Bei JEDEM der drei Installer-Rebuilds dieser Nacht haengte sich
  mindestens ein `/VERYSILENT`-Lauf bei konstant ~0% CPU auf (sichtbares,
  aber inaktives Setup-Fenster, keine Kindprozesse, kein Fortschritt bei
  der Datei-Extraktion) - meist nach 5-10 Minuten erkennbar. Beim letzten
  Rebuild sogar ZWEI aufeinanderfolgende haengende Versuche vor einem
  erfolgreichen dritten. **Zuverlaessig behobenes Muster**: betroffenen
  `Lexono_Setup`/`Lexono_Setup.tmp`-Prozess beenden und denselben Befehl
  erneut ausfuehren - hat in JEDEM Fall beim naechsten oder uebernaechsten
  Versuch funktioniert (erkennbar am tatsaechlich steigenden CPU-Verbrauch
  des `.tmp`-Prozesses, z. B. 67s echte CPU-Zeit bei einem erfolgreichen
  Versuch vs. konstant ~0,15s bei einem haengenden). Ursache weiterhin
  NICHT geklaert (Kandidaten unveraendert: Windows-Defender-
  Echtzeitpruefung der frisch entpackten ~1,1GB, ein Inno-Setup-eigener
  Zustand) - Disk-Speicherplatz wurde als Ursache ausgeschlossen (~324GB
  frei). Fuer zukuenftige Installer-Laeufe: IMMER CPU-Verbrauch des
  `.tmp`-Prozesses ueber mehrere Minuten vergleichen statt nur auf den
  Log-Dateipfad zu warten (der wird ohnehin erst beim Prozessende
  geschrieben, in dieser Umgebung nie zuverlaessig beobachtet) - flache
  CPU-Kurve ueber 5+ Minuten ist der zuverlaessigste Hinweis auf einen
  echten Haenger.

- ~~App.css enthält einen Kommentar mit „KanzleiAI” (`.chat-panel__header`
  Kommentarblock)~~ – **ERLEDIGT (01.09., später)**, auf „Lexono”
  korrigiert. Verbleibende `KanzleiAI`-Vorkommen in `app/setup/paths.py`,
  `app/web/template_paths.py`, `app/web/monitoring_router.py` sind
  bewusst unverändert (interner `%PROGRAMDATA%`-Pfadname, keine sichtbare
  UI, Änderung würde bestehende Installationen brechen - siehe
  DECISIONS.md).

## FUTURE (erwogen, bewusst nicht umgesetzt)

- **Maximieren per Doppelklick auf die Titelleiste**: erwogen als
  Ergänzung zum Eck-Resize-Griff (der einzige aktuelle Weg, die
  Fenstergröße zu ändern - Griff ist klein, für Nutzer mit
  motorischen Einschränkungen ggf. schwer zu treffen). `webview.Window.
  maximize()`/`.restore()` existieren bereits und sind ein echtes
  Toggle-Paar (`WindowState = Maximized`/`Normal`, in `winforms.py`
  verifiziert) - das Problem ist NICHT die Umsetzung des Togglens
  selbst, sondern die zuverlässige Erkennung des AKTUELLEN Zustands
  (maximiert oder nicht) vor jedem Doppelklick, ohne die native
  Fenster-Objektebene direkt anzufassen. `_NativeApi`s eigener
  Docstring (`run.py`) warnt ausdrücklich vor genau diesem Bereich -
  ein früherer, real aufgetretener Bug (`get_functions`-Rekursion durch
  ein öffentliches `window`-Attribut) führte zu einem kompletten
  Programmhänger beim Fensteraufbau. Nicht umgesetzt, um dieses Risiko
  nicht für ein unaufgefordertes Komfort-Feature einzugehen.

## FUTURE (explizit nicht jetzt zu bauen, Masterprompt §30–32)

- Mehrstufige Multi-Agenten-Unternehmensorganisation (CEO/Product/SWE/QA/
  Security/... als eigenständige dauerhaft laufende Agenten) – architektonisch
  mitdenken, nicht jetzt bauen.
- Öffentliche Landingpage – Repo-Struktur so halten, dass spätere Integration
  sauber möglich ist, aber nicht Teil dieses Auftrags.
- Reale Gateway-Produktivbereitstellung (Hosting/Domain/Zertifikat) –
  separate, explizit zu genehmigende Infrastrukturentscheidung.
