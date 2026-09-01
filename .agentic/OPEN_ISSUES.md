# OPEN_ISSUES – Technische Schulden & offene Workstreams

Kategorien: CRITICAL / HIGH / MEDIUM / LOW / FUTURE.
Kein Eintrag hier bedeutet automatisch Untätigkeit – Einträge werden aktiv
von den zuständigen Agenten (siehe `agents/`) abgearbeitet oder bewusst
zurückgestellt (mit Begründung).

## CRITICAL

_Keine offenen CRITICAL-Punkte (zuletzt geprüft 01.09.)._

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
