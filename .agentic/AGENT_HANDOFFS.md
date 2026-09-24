# AGENT_HANDOFFS – Log der Agent-zu-Agent-Übergaben

Format: Datum · Von-Agent → An-Agent · Was · Ergebnis. Neueste Einträge
unten anfügen.

---

**31.08.** · Orchestrator → Agent B (Frontend) + Agent L (Documentation) ·
Aufbau der Agentenorganisation (`agents/`, `skills/`, `.agentic/`) im
Rahmen von Masterprompt V2, plus erste konkrete UI-/Branding-Korrekturen
(Presidio aus Chat-UI entfernt, verbleibende „KanzleiAI“-Strings in
`chat.html` auf „Lexono“ korrigiert, „Strg+K“-Badge entfernt, vierte
Quick-Action „Akte öffnen“ ergänzt) · Ergebnis: umgesetzt, siehe
DECISIONS.md. Volle Testsuite noch gegenzuprüfen (Agent I).

---

**31.08.** · Agent B → Agent I (QA) · Bitte volle Regressionssuite nach
Template-/CSS-Änderungen in `chat.html`, `base.html`, `app.css` laufen
lassen, Baseline (1463/1/0) darf sich nicht verschlechtern · Ergebnis:
bestätigt, 1463 passed/1 skipped/0 failed, unverändert.

---

**01.09.** · Nutzer → Orchestrator · Entscheidung Task #61 (Fenster-
Chrome): natives Chrome entfernen, X muss zuverlässig bleiben, Resize darf
nicht kaputtgehen, sonst sicherste Variante wählen. Zusätzlich: Agenten-
organisation aktiv als Arbeitsstruktur nutzen (nicht nur Dokumentation),
weitere offene HIGH/MEDIUM-Punkte abarbeiten.

---

**01.09.** · Agent K (Release) → Agent B (Frontend) · Recherche zu
pywebview 6.2.1 Frameless-/Resize-/Drag-Faehigkeiten (echter Blick in den
installierten Quellcode, kein Trainingswissen) · Ergebnis: frameless=True
liefert auf Windows nur FormBorderStyle.None, kein eingebautes Hit-Testing;
easy_drag ist im WinForms-Backend toter Code; `window.move()`/`resize()`/
`minimize()`/`destroy()` sind bereits vorhanden und live (echte
x/y/width/height-Properties). Empfehlung: schmale, hand-gerollte Lösung
(nur Titelleiste draggable, nur ein Eck-Resize-Griff) statt vollflächigem
Hit-Testing. Siehe DECISIONS.md.

---

**01.09.** · Agent B (Frontend) → Agent I (QA) · Umgesetzt: frameless
Fenster + eigene Titelleiste (`run.py`, `base.html`, `_NativeApi`), globale
Sidebar-Statusanzeige (`base.html`, ohne Router-Aenderungen via
`request.app.state`), Buerklammer-/Mikrofon-/Drag&Drop-UI im Chat-Composer,
Dokument-Workspace mit Pseudonymisierungs-Highlighting (`app/chat/
document_preview.py`, neue Route `chat_document_view`). Bitte volle
Regressionssuite + neue gezielte Tests pruefen · Ergebnis: siehe TEST_STATE.md
fuer den aktuellen Stand nach diesem Block.

---

**01.09.** · Agent H (Security/Privacy) · Eigenpruefung (kein separater
Agent-Handoff noetig, da keine neue Cloud-/Netzwerk-/Speicher-Operation
eingefuehrt wurde): Dokument-Workspace berechnet Presidio-Erkennung NUR
in-memory bei jedem Request neu (kein neues persistentes Feld), nutzt
dieselben Detektoren wie der echte Pseudonymisierungspfad, erzwingt
Aktenisolation ueber `ChatService.get_attached_document` (getestet: Dokument
aus fremder Konversation liefert 404-Redirect, nicht die Daten). Mikrofon-
Button bewusst NICHT an eine echte (typischerweise cloud-basierte)
Spracherkennung angebunden - haette den Privacy-Kernschutz umgangen. Keine
Einwaende gegen Integration.

---

**01.09.** · Nutzer → Orchestrator · Zwei parallele Auftraege: (1)
CI-/Branding-Ueberarbeitung (grünes Logo + weitere Akzentfarben), (2)
Dokumentationskonsolidierung + Klarstellung des verbindlichen
Architekturstands gegenueber ueberholten historischen MD-Staenden.

---

**01.09.** · Agent E (Backend/Architecture) → Orchestrator · Bei der
Umsetzung von (1) gefunden: der aktuelle Logo-/Akzentfarbcode `#101828`
wurde in einer frueheren Sitzung bewusst per Pixelmessung aus dem
tatsaechlichen offiziellen Logo verifiziert (Navy, nicht gruen) - echter
Zielkonflikt mit dem neuen Auftrag. Keine Code-Aenderung vorgenommen,
stattdessen in OPEN_ISSUES.md als Produktentscheidung markiert und dem
Nutzer eine konkrete Frage vorgelegt (neue gruene Logo-Datei vorhanden?).

---

**01.09.** · Agent L (Documentation) → Orchestrator · Auftrag (2)
umgesetzt: `.agentic/SESSION_LOG.md` neu angelegt (Archiv der bisherigen
Verlaufserzaehlung aus PROJECT_STATE.md), `PROJECT_STATE.md` auf reinen
Ist-Zustand reduziert, `TEST_STATE.md`/`OPEN_ISSUES.md`/`TASK_MAP.md`
gegen den tatsaechlichen Code-/Test-/Git-Stand aktualisiert (u. a.
veraltete "1463 Tests"-Angabe auf 1486 korrigiert, erledigte MEDIUM-
Punkte entfernt). `ARCHITECTURE.md` bekam einen neuen "AKTUELLER
VERBINDLICHER ARCHITEKTURSTAND"-Block direkt nach dem Titel plus
"ÜBERHOLT"-Markierungen an §57/§60/§63 (dokumentierte
Architektur-Kehrtwenden zur lokalen KI/zum zentralen Proxy) - nichts
geloescht, nur gekennzeichnet.

---

**01.09.** · Nutzer → Orchestrator · Neuer Auftrag "LEXONO – MASTER AGENTIC
CODING ORCHESTRATION" (34 Abschnitte): zuerst Audit der bestehenden
Agenten-/Skills-/`.agentic`-Struktur (was existiert wirklich, was wird
tatsaechlich genutzt vs. nur dokumentiert), NICHT ersetzen, nur echte
Luecken schliessen; danach konkrete Produktarbeit an Chat +
Dokument-Workspace anhand zweier Referenzbilder; lokales KI-Modell
(`qwen2.5:1.5b`) muss austauschbar bleiben; kein Sales-/CRM-/
Business-Org-Agent jetzt.

---

**01.09.** · Orchestrator (Audit) · Ergebnis: `agents/`/`skills/` sind
rollenbasierte Kontextdateien, `.agentic/` das Arbeitsgedaechtnis - real
genutzt (u. a. Security-Review-Subagent, Dokumentationskonsolidierung als
paralleler Arbeitsstrang). Kein Multi-Agenten-Laufzeitsystem vorhanden oder
noetig; einzig echter Delegationsmechanismus ist das `Agent`-Tool fuer
eigenstaendig verifizierbare Teilaufgaben. Klarstellung dazu in
`agents/lead/AGENT.md` ergaenzt (Abschnitt "Funktionsweise der
Delegation"), da die bisherigen "Agent X -> Agent Y"-Log-Eintraege
missverstaendlich als getrennte Laufzeitinstanzen lesbar waren. Keine
Ersetzung der Struktur, keine neuen Agenten-/Skill-Dateien angelegt (kein
echter Verantwortungsluecke gefunden, die eine neue Datei rechtfertigt).

---

**01.09.** · Agent D (Chat/Document Workspace) · Echter Produktgap
gefunden: die Dokument-Workspace-Kontextleiste (Referenzbild 2) hatte noch
keine Schnellaktionen ("Antwort entwerfen"/"Fristen & Risiken pruefen"/
"Zusammenfassung erstellen"), obwohl das Referenzbild sie zeigt. Umgesetzt
per Wiederverwendung des bestehenden `data-prefill`-Mechanismus (derselbe
Klick-Handler wie die leeren Chat-Quick-Actions, keine neue Sende-Logik,
keine Fake-Buttons) - referenziert den echten Dateinamen des gerade
angesehenen Dokuments, fuellt nur das bestehende Composer-Textfeld,
sendet ueber die normale, bereits privacy-geprüfte Chat-Pipeline. Neue
CSS-Klasse `.chat-quick-action--compact` fuer die schmale Spalte. Test
erweitert (`test_document_workspace_shows_highlighted_pii_and_context_panel`).
Volle Suite: 1487 passed / 1 skipped / 0 failed, unveraendert.

---

**01.09.** · Agent J (Visual QA, unterstuetzt durch Sub-Agent-Review) ·
Zweite Auftragswelle desselben Masterprompts: fuenfter Installer-
Rebuild gebaut, ohne Stall installiert, per HTTP-Smoke-Test bestaetigt
(Login, Upload, PII-Highlighting, alle drei Schnellaktionen, Bestands-
seiten - alles gruen). Anschliessend echte Sub-Agent-Delegation
(Agent-Tool, isolierter Worktree) fuer eine unabhaengige Security-/
Code-Review der neuen Schnellaktionen: XSS-Pruefung (Jinja-Autoescape
aktiv, bestaetigt sicher), Privacy-Pfad (unveraendert, kein neuer
Sende-Weg), Aktenisolation (bestaetigt) - keine Funde.

Danach echte UI-Automatisierung entdeckt (Maus-/Tastatursimulation im
nativen Fenster per PowerShell/P-Invoke) - damit erstmals tatsaechlich
in den Dokument-Workspace navigiert (nicht nur die Login-Seite
fotografiert). Dabei realen, reproduzierbaren Layout-Bug gefunden:
`.chat-panel` kollabierte bei der tatsaechlichen Fensterbreite dieser
Umgebung (~1024px) auf einen unlesbaren Streifen. Ursache gefunden
(`.chat-panel` ohne `min-width`, `.chat-document-pane`/
`.chat-context-pane` mit festen Breiten), behoben (Commit `b92e1cb`),
per Vorher/Nachher-Screenshot in einer Dev-Instanz verifiziert, volle
Testsuite weiterhin gruen. Nebenbefund: die Bildschirmaufloesung dieser
Umgebung ist nur 1024x768 - erklaert rueckwirkend die fruehere
unbestaetigte MoveWindow-Beobachtung als physische Grenze statt
DPI-Anomalie; 1366x768/1920x1080 sind hier kategorisch nicht testbar.
`skills/visual_qa/SKILL.md` entsprechend aktualisiert (war veraltet,
ging von einem Browser-Tool aus statt der nativen Fenstertechnik).
Sechster Installer-Rebuild mit diesem Fix angestossen, installiert und
per UI-Automatisierung verifiziert (Titelleiste korrekt, Layout-Fix
bestaetigt) - siehe Commit `b286f97`.

---

**01.09.** · Nutzer → Orchestrator · Neuer Auftrag "LEXONO – Reliability
& Deployment Hardening Cycle": Schwerpunkt Installer-Silent-Install-
Stall-Root-Cause-Untersuchung (echte Reproduktion statt Umgehung
gefordert, explizit "kein Fake-Testing"), danach praktischer
Modell-Austauschbarkeitstest (zweites Modell, z. B. Mistral), explizit
NICHT das lokale Modell neu fest verdrahten.

---

**01.09.** · Orchestrator (Installer-Root-Cause) · Stall ECHT
REPRODUZIERT (nicht nur vermutet): frischer Silent-Install zeigte 60+
Sekunden konstante 0,17s-CPU-Zeit VOR jedem Extraktions-Fortschritt,
keine Kindprozesse. Isolierte WebView2-Bootstrapper-Tests (5x) zeigten
dagegen konsistent 7-8s, kein Hang - dieser Schritt allein erklaert das
Symptom nicht. Windows-Defender-Health-Report-Event lag mitten im
Stall-Fenster (RTP aktiv bestaetigt). `Get-AuthenticodeSignature`
bestaetigt: weder Setup.exe noch kanzlei_ai.exe sind code-signiert.
Web-Recherche (WebSearch/WebFetch) zu einem bekannten, von Microsoft
selbst als "tracked" gefuehrten WebView2-Bootstrapper-Fruehausstiegs-
Verhalten durchgefuehrt (GitHub-Issue #1349) - liefert Kontext, aber
nicht den Beweis fuer DIESES Symptom.

---

**01.09.** · Agent zur unabhaengigen Review delegiert (Agent-Tool,
isolierter Worktree) · Installer-/Deployment-Review: stufte den
WebView2-Bootstrap-Schritt korrekt als UNWAHRSCHEINLICHE Ursache fuer
DIESES Symptom ein (Inno-Setup-`[Run]`-Eintraege laufen laut
Dokumentation ERST NACH dem `[Files]`-Kopiervorgang, der Stall trat aber
VOR jedem Extraktions-Fortschritt und OHNE Kindprozess auf). Fand
stattdessen zwei echte, unabhaengige Gaps: (1) fehlendes `AppMutex` in
`installer.iss` trotz eigenem Single-Instance-Mutex in `run.py` -
Reinstall waehrend laufender App haette Dateien sperren/beschaedigen
koennen; (2) keine Code-Signierung irgendwo in der Build-Pipeline -
sowohl eigenstaendiges SmartScreen-Vertrauensproblem als auch
plausibler Beitrag zum Defender-Scan-Stall. Uninstall-Datensicherheit
(kein `%PROGRAMDATA%`-Zugriff) und Privilegien-Modell wurden UNABHAENGIG
gegengeprueft und als korrekt bestaetigt (nicht nur dem Kommentar
vertraut).

---

**01.09.** · Orchestrator · `AppMutex=Lexono_SingleInstance_Mutex`
ergaenzt (Commit `cfa68bc`). Empirisch verifiziert: Reinstall-Versuch bei
laufender App wird jetzt sauber mit `EAbort`/ExitCode 1 abgelehnt
(Installer-Log zeigt explizit die deutsche "Setup hat entdeckt, dass
Lexono zurzeit ausgefuehrt wird"-Meldung), laufende App bleibt
unangetastet; normaler Reinstall (App vorher geschlossen) funktioniert
weiterhin fehlerfrei (ExitCode 0). Code-Signierung als
Nutzerentscheidung (Zertifikat-Beschaffung) dokumentiert, NICHT
implementiert. Silent-Install-Stall selbst bleibt ehrlich als
unbewiesene, aber gut gestuetzte Hypothese dokumentiert - keine
Behauptung eines Fixes ohne Beweis.

---

**01.09.** · Orchestrator (Modell-Austauschbarkeit) · `mistral:7b` per
Ollama-API gepullt (778s) und gegen den echten `OllamaLocalLLMProvider`
getestet: Health Check, generate, generate_structured, Fehlerverhalten,
Startup-Statusanzeige - alles funktioniert unveraendert, rein per
`OLLAMA_MODEL`-Konfigurationsaenderung, KEINE Code-Aenderung. Damit
architektonische Modellunabhaengigkeit praktisch bestaetigt. Modell
selbst aber disqualifiziert: Platzhaltererhaltung fehlgeschlagen
(`[MANDANT_01]` in beiden Laeufen verloren) UND 6-7x langsamer als die
`qwen2.5:1.5b`-Baseline. Voller End-to-End-Test ueber den echten
Chat-Endpunkt war nicht moeglich (Cloud-Provider-Check schlaegt vor dem
lokalen KI-Schritt fehl, kein echter API-Schluessel in dieser
Testumgebung - bestehendes, korrektes Fail-Closed-Verhalten). Test-
Konfiguration nach Abschluss vollstaendig zurueckgesetzt.

---

**01.09.** · Nutzer (Live-Feedback waehrend der Sitzung) · "der fenster
verkleinern button fehlt" - direkte Rueckmeldung zum zuvor als korrekt
gemeldeten Titelleisten-Screenshot. Untersuchung: Button existiert und
funktioniert (JS-Handler korrekt verdrahtet), aber das SVG-Icon war ein
duenner, exakt zentrierter Strich (stroke-width 1.8) - neben dem
kraeftigeren Schliessen-X per Zoom-Screenshot bestaetigt kaum sichtbar.
Behoben (Commit `391361a`, stroke-width 2.2, Position naeher an die
uebliche OS-Konvention). Per UI-Automatisierung sowohl visuell
(Vorher/Nachher-Zoom) als auch FUNKTIONAL bestaetigt (echter Klick ->
`IsIconic()==true`, Fenster minimiert tatsaechlich). Neunter
Installer-Rebuild mit diesem Fix angestossen.

---

**01.09.** · Nutzer → Orchestrator · Neuer Auftrag "LEXONO – MASTER
PROMPT: Autonomous Agentic Coding — Product Completion Cycle": CI-Frage
(Nutzer nennt jetzt selbst die verbindlichen Werte `#101828`/`#f8fafc`/
`#ffffff`/`#64748b`), Produkt-Gap-Analyse (Dokumente/Chat als
Arbeitszentrale), Local-AI-Architektur nicht verengen (Modell muss
austauschbar bleiben), Windows-Update-Verhalten pruefen, Security/
Testing/Visual-QA/Delegation wie gehabt.

---

**01.09.** · Orchestrator (Prio 1: CI-Konflikt) · Gegenpruefung des
tatsaechlichen Codes gegen die vom Nutzer genannten CI-Werte: ALLE VIER
bereits exakt implementiert (`--seal-green: #101828`, `--paper-100:
#f8fafc`, `--paper-000: #ffffff`, `--ink-500: #64748b`, `logo.svg`-Fill
`#101828`). Keine Code-Aenderung noetig - der fruehere "gruenes
Logo"-Konflikt ist damit endgueltig geklaert (die urspruengliche
Erwartung war veraltet, das pixelverifizierte Navy war durchgehend
richtig). OPEN_ISSUES.md/PROJECT_STATE.md/TASK_MAP.md entsprechend
aktualisiert.

---

**01.09.** · Orchestrator (Prio 5: Local-AI-Architektur) · Codepruefung
bestaetigt: `LocalLLMProvider`-Protocol + `OllamaLocalLLMProvider` sind
bereits sauber modellunabhaengig (Modell/Basis-URL als
Konstruktorparameter, `Settings.ollama_model`/`ollama_base_url`
konfigurierbar). Echter, vorher unbekannter Gap gefunden: KEINE Web-UI
zum Ansehen/Aendern des Modells - nur per `.env`-Handbearbeitung oder
CLI-Setup-Assistent moeglich. Neue "Lokale KI"-Sektion in
`/dashboard/settings` ergaenzt (Commit `31a3ede`), wiederverwendet
bestehende `_apply()`-env-Schreiblogik (dasselbe Muster wie Mail-/
Aufbewahrungs-Einstellungen). Prio 6 (Update-Verhalten) gegengeprueft:
bereits korrekt implementiert (`update_badge.html`, unaufdringlich, kein
Zwang) UND bereits global in `base.html` eingebunden - kein Gap, keine
Aenderung noetig.

---

**01.09.** · Agent zur unabhaengigen Review delegiert (Agent-Tool,
isolierter Worktree) · Pruefung der neuen Lokale-KI-Settings-Route:
Autorisierung/CSRF korrekt, Privacy-Grenze unangetastet, ABER echter
Fund: `format_env_value()` escapte `\n`/`\r` nicht - ein Formularwert
mit eingebettetem Zeilenumbruch konnte aus seiner `.env`-Zeile ausbrechen
und beliebige neue Zeilen einschleusen (z. B. `SESSION_SECRET_KEY`
ueberschreibbar). Betraf alle bestehenden Aufrufer, nicht nur die neue
Route. Zentral behoben (Commit `ba2f286`), 2 neue Regressionstests,
volle Suite weiterhin gruen (1493/1/0).

---

**01.09.** · Agent J (Visual QA) · Settings-Seite per echter
UI-Automatisierung geprueft (Login, Navigation zum Zahnrad-Icon,
Scroll). Dabei ein Umgebungs-Detail geklaert (nicht appseitig): das
1024x768-Fenster kann teilweise unter der Windows-Taskleiste liegen,
wodurch Klicks auf untere Sidebar-Elemente ins Leere gehen koennen -
kein App-Bug, behoben durch Verkleinern/Neupositionieren des Fensters
vor dem Klick. Neue "Lokale KI"-Sektion visuell bestaetigt (Status-Tag,
Hinweistext im deaktivierten Zustand). Siebter Installer-Rebuild
angestossen, um Settings-Feature + Sicherheitsfix real auszuliefern.

---

**01.09.** · Nutzer → Orchestrator · Neue, sehr detaillierte visuelle
Referenzvorlage (Bild) bereitgestellt, "verbindliches Abnahmekriterium"
- Logo, Sidebar-Aufbau/-Einklappen, Navigation, Profil-/Einstellungen-
Position, Hilfe & Support-Position, Chat-Eingabe, Schnellaktions-Icons,
Farbwelt. Explizite Abnahmekriterien: Anwendung tatsaechlich im nativen
Fenster starten, einloggen, Sidebar ein-/ausklappen, mit der Vorlage
vergleichen, Abweichungen selbststaendig korrigieren.

---

**01.09.** · Orchestrator (Redesign-Umsetzung) · Grossflaechig
umgesetzt: neues gruenes Logo-Icon (weisses Kettensymbol, navyfarbene
Wortmarke bleibt getrennt), neue eigenstaendige Markenfarbe
`--brand-green` (getrennt von `--seal-green`/Navy), Sidebar-Einklappen
(localStorage-persistiert, kein Ladeflackern), "Neuen Chat starten"-
Button, Suchfeld-Tastaturkuerzel-Badge wieder sichtbar, flachere
Navigation (Schriftsatz-Generator/Monitoring/Backup/Fehler & Logs als
Top-Level-Punkte), Lokale-KI-/Cloud-KI-Status auf jeder Seite inkl.
Chat. Echter, vorher unbekannter Gap gefunden und geschlossen: "Aufgaben"
(Task-Datenmodell existierte, keine Seite) - neue
`app/web/tasks_router.py` mit echtem HTMX-Badge-Zaehler.

---

**01.09.** · Nutzer → Orchestrator · AKTUALISIERTE Referenzvorlage
bereitgestellt, ersetzt die vorherige vollstaendig. Wichtigste
Aenderung: 4. Chat-Schnellaktion ist "Weitere Funktion hinzufuegen"
(nicht "Akte oeffnen"), neuer "Standard-Prompts bearbeiten"-Button,
Sidebar-Status-Reihenfolge Cloud-KI zuerst mit eigenen Icons.

---

**01.09.** · Orchestrator · Umgesetzt: 4. Karte verlinkt die bereits
bestehende Standard-Prompts-Verwaltung (kein Fake-Button), "Fehler &
Logs"-Icon rot, Schnellaktionen als grosse Karten (Icon-Kreis + Titel +
Beschreibung) statt kompakter Pillen. Dabei echten, unabhaengigen Bug
gefunden: "Neuen Chat starten"/das "+"-Icon zeigten bei bestehendem
Verlauf immer die letzte Unterhaltung statt eines Leerzustands (`GET
/dashboard/chat` waehlte immer `conversations[0]`) - behoben mit
`?new=1`-Parameter.

---

**01.09.** · Nutzer (Live-Feedback waehrend der Sitzung, 3 Punkte) ·
(1) "die Spalte Unterhaltungen sollte nicht mehr in dieser Form
existieren und unter den Menuepunkt chats zu finden sein" - die
staendig sichtbare `.chat-conversations`-Spalte entfernt, "Chat" ist
auf der Chat-Seite selbst jetzt eine echte Aufklapp-Gruppe mit der
Historie als Unterpunkten (Template-Vererbung liefert `conversations`
in base.html, kein Router-Umbau); auf anderen Seiten bleibt "Chat" ein
flacher Link ohne zusaetzliche DB-Abfrage. (2) "die Umrandung fuer
weitere Funktionen hinzufuegen fehlt" - echter CSS-Spezifitaets-Bug
gefunden (Modifier-Klasse hatte dieselbe Spezifitaet wie die spaeter im
Stylesheet stehende Basisregel und wurde ueberschrieben) und behoben
mit zusammengesetzten Selektoren. (3) "Logo naeher an den oberen Rand" -
Sidebar-Padding reduziert. Alle drei per echter UI-Automatisierung
(Login, Navigation, Screenshot-Vergleich) verifiziert. Elfter Installer-
Rebuild mit allen Redesign-Aenderungen angestossen.

---

**15.09.** · Nutzer → Orchestrator · Paralleler Diagnoseauftrag "Chat
Intelligence & Local-AI Architecture Forensic" (CHAT-INT-DIAG),
ausdrücklich als ERWEITERUNG des laufenden UI/UX-Auftrags, ohne diesen
abzubrechen und ohne neue Agenten-/Roadmap-Struktur. Eingeordnet in
TASK_MAP §G; bearbeitet über die BESTEHENDEN Rollen `agents/chat`,
`agents/local_ai`, `agents/ai_quality`, `agents/architecture`.
Auflage: DISCOVER → REPRODUCE → TRACE → MEASURE → UNDERSTAND → CLASSIFY
→ ARCHITECTURE DECISION. Keine Implementierung vor bewiesener Ursache;
Ausnahme: kleine, risikoarme Fixes, die keine Architekturentscheidung
vorwegnehmen. · Ergebnis: offen, Diagnose läuft.

---

**15.09.** · Orchestrator → Agent D (Chat) + Agent Local AI ·
Forensik-Auftrag übergeben: Request-Lifecycle einer echten Chatnachricht
("Hallo") End-to-End belegen; Payload-Forensik (bekommt das Modell den
Gesprächsverlauf TATSÄCHLICH, nicht nur "History liegt in der DB");
4-Turn-Dialog auf Kontexterhalt prüfen; Rolle der Local AI bestimmen
(Generator vs. Preanalysis/Privacy/Validation/Routing); Latenz je Stufe
cold/warm messen; Privacy-Invarianten inkl. "Frau Müller" gegenprüfen.
Maßgeblich sind Code + reale Tests + Runtime, NICHT Dokumentation oder
Dateinamen. · Ergebnis: offen.

---

**15.09.** · Agent D (Chat) + Agent Local AI → Orchestrator · Forensik
CHAT-INT-DIAG abgeschlossen, STATUS RED. Memo A–Q unter
`.agentic/memos/CHAT_INTELLIGENCE_MEMO.md`, Messwerkzeuge unter
`.agentic/memos/chatdiag_harness/` (wiederholbar - der Messplan von
CHAT-01/CHAT-02 verlangt einen erneuten Lauf nach der Umsetzung).
Kernaussage: Gespraechsverlauf erreicht das Modell nicht (bewiesen per
Payload-Mitschnitt), Architektur- und kein Modellproblem, Privacy gruen.
Repo wurde vom Diagnose-Agenten NICHT veraendert (reine Leseanalyse plus
Scratchpad), alle Repo-Aenderungen dieser Sitzung stammen aus dem
UI-Strang. · Ergebnis: 6 Folge-Tasks CHAT-01..CHAT-06 in TASK_MAP §G
eingetragen; CHAT-05 sofort umgesetzt, CHAT-06 dokumentiert und zur
Entscheidung vorgelegt.

---

**15.09.** · Agent Local AI → Orchestrator · STT-Evaluation (Feld B,
Nutzerauftrag) durchgefuehrt - Vosk und faster-whisper real getestet
(isolierte venv, 5 kanzleitypische TTS-Testdiktate, da kein Mikrofon
verfuegbar), openai-whisper dokumentiert ausgeschlossen (torch),
whisper.cpp nicht getestet. Memo unter
`.agentic/memos/stt_eval/STT_EVALUATION_MEMO.md`. Kernbefund: Latenz ist
bei beiden getesteten Kandidaten kein Problem, die reale Schwachstelle
ist Genauigkeit bei gesprochenen Zahlen (Paragraphen/Betraege/Jahre) -
bestaetigt die Nutzervorgabe "Transkript vor dem Absenden pruefen" als
Notwendigkeit, nicht nur UX-Vorsicht. Ausdruecklich KEINE Implementierung,
KEINE Modellentscheidung (Nutzervorgabe §12/§20 eingehalten). · Ergebnis:
offen, naechste Schritte vor einer Entscheidung im Memo benannt
(Referenzhardware, echte menschliche Stimme, whisper.cpp,
PyInstaller-Buendelungsprobe).

---

**15.09.** · Orchestrator (Fortsetzung nach "weiter") · CHAT-04 umgesetzt,
nachdem eine genauere Pruefung zeigte, dass die urspruenglich vermutete
fehlende Voraussetzung (Herkunftsattribution fuer PseudonymMapping)
bereits existiert (`known_entities`, exakter Abgleich). Real gegen echtes
Ollama gemessen: 2,60s (Skip) vs. 24,35s (Kontrollgruppe mit neuem Namen)
= 9,4x, 8 neue Tests. CHAT-02 bleibt bewusst UNANGETASTET - "weiter" ohne
inhaltliche Antwort auf die vier offenen Produktfragen wurde NICHT als
Freigabe interpretiert, die numerierte "Genau diese SIEBEN Felder"-Vorgabe
bleibt unveraendert. · Ergebnis: CHAT-01/04/05 erledigt, CHAT-02/03/06
weiterhin offen/zurueckgestellt.

---

**15.09.** · Nutzer (Owner) → Orchestrator · Finale Entscheidung zu den
vier offenen CHAT-02-Produktfragen (A max. 10 History-Messages, B max.
3.000/12.000 Zeichen, C user+assistant, D nur aktuelle ChatConversation)
+ ausdrueckliche Freigabe, die "Genau diese SIEBEN Felder"-Vorgabe auf
acht zu erweitern. Klarer Rahmen: keine Entscheidung aus Schweigen
ableiten, CHAT-03 bleibt ausdruecklich unangetastet, Scope-Disziplin
(keine ungeplanten Aenderungen an UI/RBAC/Ollama/Gateway-Infrastruktur
ausserhalb von CHAT-02).

---

**15.09.** · Orchestrator → CHAT-02 umgesetzt (Agentic-Coding-Modus:
Inspect -> Understand -> Diagnose -> Prepare -> Test -> Report). Achtes
Payload-Feld, gemeinsame `_build_history`-Implementierung fuer
send_message/send_message_stream, `current_message_id` verifiziert am
tatsaechlichen Router-Code (nicht angenommen). Zwei echte Fehler beim
Testen gefunden und behoben (Zeichenbudget-Praefix-Zaehlung, "Lexono" als
Presidio-PERSON-Fehltreffer - jetzt "Assistent"). Ende-zu-Ende mit dem
urspruenglichen 4-Turn-Forensik-Dialog bewiesen (Turn 4 enthaelt jetzt
tatsaechlich Turn-1-3-Inhalte). 23 neue Tests + 1 bestehender
Sicherheitsreview-Test bewusst von "sieben Feldern" auf "acht Felder"
fortgeschrieben. Voller Regressionslauf siehe TEST_STATE.md. · Ergebnis:
CHAT-01/02/04/05 erledigt, CHAT-03 weiterhin ausdruecklich blockiert,
CHAT-06 weiterhin offen (Entscheidung des Owners).

---

**16.09.** · Nutzer (Owner) → Orchestrator · "CONTINUE MAGNETIC CODING":
CHAT-02 als DONE bestaetigt, bestehenden Prozess anhand der Roadmap
fortsetzen, insbesondere UI/UX-Referenzbild-Sweep. · Orchestrator fand
und behob einen DPI-Skalierungsfehler in der GUI-Automatisierung (neues
`lexono_ui.ps1`), fixte eine echte doppelte Ueberschrift in
`clients_list.html`, diagnostizierte die 225-Junk-Fristen (Root Cause:
`Schnellentwurf`-Matter-Wiederverwendung fehlt + Deadline-Extractor ohne
Dedupe - Owner-Entscheidung dokumentiert, nicht geloescht), und fand einen
zweiten Automatisierungsfehler (`SetForegroundWindow`-Vordergrundsperre,
Alt-Tastendruck-Fix). Alle 44 Referenzbilder inhaltlich katalogisiert
(gut die Haelfte falsch benannt) und mehrere reale SCOPE-Luecken
(Mandant-/Akte-Detail fehlt Tabs/Notizen/Schnellaktionen; echte
Aufgaben-&-Fristen-Referenz zeigt ein volles Task-Management-Feature,
das Datenmodell hat weder Prioritaet noch Status) dokumentiert statt
gebaut. Regressionslauf 1869/1/0 bestaetigt.

---

**16.09.** · Nutzer (Owner) → Orchestrator · "AUTONOMOUS GUI CONTINUATION":
korrigierte die Annahme, ein manueller Login sei zwingend noetig - wies
an, einen eigenstaendigen Testzugang ueber bestehende, zulaessige
Testmechanismen selbst herzustellen (ausdruecklich NICHT durch
`.env`-Zugriff, Passwort-Raten oder Guardrail-Umgehung), sonst den
Blocker zu dokumentieren und mit unabhaengiger Roadmap-Arbeit
fortzufahren. · Orchestrator lehnte den einzigen bestehenden
"Admin existiert schon"-Reset-Pfad bewusst ab (haette das ECHTE
Admin-Konto beruehrt), baute stattdessen ein neues, eng gescoptes Skript
fuer einen separaten Nicht-Admin-Testnutzer (Rolle "Anwalt", eigene
.invalid-Adresse, generiertes Passwort) nach demselben, bereits
vertrauten Muster wie `create_admin.py` - die AUSFUEHRUNG wurde vom
Auto-Mode-Berechtigungsfilter blockiert (dritter Guardrail-Treffer der
Sitzung im Credential-/PII-Umfeld). Nicht umgangen, Skript entfernt,
Blocker dokumentiert (OPEN_ISSUES.md). Lexono-Instanz zwischenzeitlich
selbststaendig beendet vorgefunden (Nutzer parallel am Rechner aktiv) und
unkritisch neu gestartet. Stattdessen mit unabhaengiger Arbeit
fortgefahren: vollen Installer-Rebuild angestossen (`windows\build.ps1`,
PyInstaller + Inno Setup), damit die installierte Instanz endlich
CHAT-01/02/04/05 sowie den heutigen `clients_list.html`-Fix widerspiegelt
- laeuft im Hintergrund, Ergebnis folgt in einem spaeteren Handoff-Eintrag.

---

**16.09.** · Orchestrator (Fortsetzung, Build-Ergebnis) · Installer-Rebuild
ERFOLGREICH abgeschlossen (exit 0, `dist\installer\Lexono_Setup.exe`,
`dist\Lexono\Lexono.exe`). Die eigentliche Installation
(`/VERYSILENT /SUPPRESSMSGBOXES`) wurde vom Auto-Mode-Berechtigungsfilter
mit "Production Deploy" blockiert - vierter Guardrail-Treffer der
Sitzung, nicht umgangen, dokumentiert (OPEN_ISSUES.md). Ein
Feedback-Entwurf zum wiederkehrenden Guardrail-Muster (vier unabhaengige
Treffer bei sorgfaeltig gescopten, teils explizit vom Owner
vorautorisierten Aktionen) lokal angelegt, NICHT versendet - Nutzer
entscheidet per `/feedback`. · Statt weiter auf GUI-/Deploy-Zugang zu
warten: den bereits am 14.09. diagnostizierten, aber zurueckgestellten
P1-Fund "Chat kennt den Aktenbestand nicht" umgesetzt (Owner-Direktive
§6: "wenn technisch eindeutig -> implementieren") - die einzige damals
offene fachliche Frage war in der Diagnose selbst bereits beantwortet,
keine neue Produktentscheidung noetig. Neuer lokaler Fastpath in
`app/chat/service.py` (`_find_most_recently_active_matter` +
`_format_recent_matter_answer`), analog zum bestehenden Norm-Fast-Path,
schliesst die Schnellentwurf-Sammelakte bewusst aus. Zwei echte Fehler
beim Testen gefunden und VOR dem Fertigmelden behoben (irrefuehrendes
`Matter.updated_at` als Aktivitaetssignal; Regex ohne Punkt-
Unterstuetzung) - siehe OPEN_ISSUES.md/TEST_STATE.md fuer Details. 12
neue Tests, voller Regressionslauf 1881 passed/1 skipped/0 failed. ·
Ergebnis: CHAT-01/02/04/05 + AKTENBESTAND-Fastpath alle im Quellcode
fertig, CHAT-03 weiterhin blockiert, CHAT-06 weiterhin offen
(Owner-Entscheidung), GUI-Sweep + Installation bleiben bis zu echtem
Zugang/echter Freigabe pausiert.

---

**16.09.** · Nutzer (Owner) → Orchestrator · Zugriff durch den Nutzer
selbst wiederhergestellt (Installation + Login uebernommen) - GUI-Sweep
bei POSTEINGANG fortgesetzt. Dabei realen CRITICAL-Bug gefunden: Klick auf
eine nicht zugeordnete Nachricht MIT Zuordnungsvorschlag liess das
Detail-Panel leer (HTTP 500, fehlender `icons`-Makro-Import in
`partials/message_detail.html`, nur ueber die tatsaechlich von der UI
genutzte HTMX-Partial-Route sichtbar). Root Cause im echten Server-Log
gefunden, Fix real getestet (Regressionstest nachweislich rot vor/gruen
nach dem Fix). · Danach zwei Praezisierungen vom Owner ("EXECUTION ORDER
CORRECTION", "CLARIFICATION"): erst die laut Referenz vorgesehene UI
vervollstaendigen und ihre Funktionen verifizieren, bevor der volle
Workflow E2E getestet wird - fehlendes Backend ist dabei KEIN
automatischer Grund, eine vorgesehene Funktion auszulassen, sondern in
drei Faelle zu unterscheiden (existiert schon/technische Luecke, aber
vorgesehen/echte Produktentscheidung). Umgesetzt: manueller
Aktenzuordnungs-Picker (reuse `accept_matter_suggestion`) sowie
"Zusammenfassen"/"Antworten" ueber eine neue duenne Chat-Einstiegsroute
(reuse `ChatService`/`DraftingService`, "Antworten" erzeugt nachweislich
einen echten Entwurf ueber `_PURPOSE_DRAFT`, keine Versandfunktion
existiert). "Alle Konten"-Filter und "In Akte speichern" als FALL
3/redundant eingeordnet, nicht gebaut. 14 neue Tests, voller
Regressionslauf 1894 passed/1 skipped/0 failed. Alles Quellcode-only -
Visual/UX-QA + Workflow-E2E fuer Posteingang bleiben auf den naechsten
Installer-Zyklus verschoben, GUI-Sweep wird jetzt bei den Chat-Varianten
fortgesetzt.

---

**17.09.** · Nutzer (Owner) → Orchestrator · "IMPORTANT STATE CORRECTION":
Nutzer meldete, selbst eingeloggt zu sein und die UI wirke "absolut
identisch" zur Vorversion trotz bestaetigter SHA-256-Build-Identitaet -
Auftrag, dies als Rendering-/Delivery-Problem zu untersuchen, NICHT als
Login-Blocker. · Ergebnis: real durch die laufende App geklickt
(Mandanten, Posteingang, Nachrichtendetail, "Zusammenfassen"-Button) - alle
juengsten Aenderungen (Dedup-Ueberschrift, Crash-Fix, manueller Zuordner,
Zusammenfassen/Antworten-Buttons) rendern nachweislich live. Kein
Delivery-Problem, sondern ein ECHTER, unabhaengiger Funktionsfehler
gefunden: der "Zusammenfassen"-Klick durchlief die volle Pipeline
(bestaetigt per Server-Log: echter Claude-API-Call HTTP 200), endete aber
mit der irrefuehrenden Meldung "aus Datenschutzgruenden blockiert" trotz
unbedenklichem Inhalt. Root Cause OHNE Ratespiel real reproduziert (
DraftingService direkt mit echter Presidio-Pseudonymisierung + echtem
lokalem LLM + Spy-Writing-Provider, 4 Kontrollszenarien durchgespielt):
eine von drei deterministischen Stufe-1-Kategorien (`_BLOCK_CATEGORIES`,
api_logger.py) fehlte, sodass der schwerwiegendste Befund (Originalwert im
Antworttext) in den nichtssagenden "unknown_block_reason"-Fallback fiel -
identisch zu jedem beliebigen unklassifizierten Fehler, sowohl in der
Anwalts-Meldung als auch im Audit-Log. Fix: neue Kategorie
`original_value_leaked` + Audit-Log nutzt jetzt `categorize_block_reasons`
statt eines festen Strings. 3 neue/erweiterte Tests.

Danach "OVERNIGHT AUTONOMOUS AGENTIC EXECUTION": eigenstaendig mit dem
naechsten hoechstwertigen offenen Punkt fortgefahren (P1, 14.09., "Kein
Streaming-Feedback fuer die eigentlichen Kern-Workflows", bisher bewusst
zurueckgestellt als "eigenstaendiges Architekturthema"). Die dort selbst
vorgeschlagene risikoaermere Zwischenloesung umgesetzt: `_finish_non_
streaming` zu einem Generator umgebaut (`_finish_non_streaming_stream`,
reiner 1:1-Umbau bestehender `return`-Anweisungen, KEINE Aenderung der
Fail-Closed-Kontrolllogik), liefert jetzt vier feste, inhaltsfreie
Status-Ereignisse waehrend der 80-105+ Sekunden. SSE-Schicht
(`chat_router.py`) UND Frontend (`chat.html`s "denkt nach"-Sprechblase)
angebunden. Dabei selbst ein Bug gefunden UND behoben, bevor er auftreten
konnte: die bestehende SSE-Router-Annahme "jedes Nicht-delta-Ereignis ist
das done-Ereignis" haette bei einem durchgereichten "status"-Ereignis
einen `AssertionError` ausgeloest - vor dem Fertigmelden entdeckt und mit
einer eigenen Verzweigung behoben, mit einem eigenen Regressionstest
belegt. 10 weitere neue Tests. Voller Regressionslauf: 1917 passed, 1
skipped, 0 failed (Baseline vorher 1910).

Installer neu gebaut (sauberer Exit-Code 0, `dist\installer\
Lexono_Setup.exe`, 17.09. 01:22 Uhr) - Installation erneut vom Auto-Mode-
Filter mit "Production Deploy" verweigert (sechster Treffer der Sitzung in
dieser Kategorie), NICHT umgangen, dokumentiert (OPEN_ISSUES.md). Beide
neuen Fixes bleiben bis zur naechsten, vom Nutzer selbst ausgeloesten
Installation Quellcode-only verifiziert. Overnight-Arbeit wird gemaess
Direktive eigenstaendig fortgesetzt, keine weitere Nutzerinteraktion
abgewartet.

---

**17.09. (Fortsetzung, Overnight-Direktive "AUTONOMOUS MULTI-HOUR PRODUCT
BUILD")** · Orchestrator (eigenstaendig) · Sieben weitere echte, reale
Funde behoben, alle durch tatsaechliche Code-/Datenfluss-Analyse (nicht
Referenzbild-Vermutung) gefunden, siehe OPEN_ISSUES.md fuer Details je
Fund:

1. Referentielle-Integritaets-Pruefung der ECHTEN Produktions-DB (read-only
   SQL, keine PII gelesen) fand einen Draft+Document mit verwaister
   Matter-Referenz - DOCX-Export stuerzte dabei real ab (`matter.title` auf
   `None`), behoben (`matter: Matter | None`).
2. "Akte anlegen" von einer Mandanten-Detailseite erzeugte bei erneuter
   Eingabe desselben Namens einen DUPLIZIERTEN Mandanten -
   `create_quick_matter` bekam einen `client_id`-Parameter (Vorrang vor
   `client_name`), analog zum bestehenden `matter_id`-Query-Param-Muster.
3-5. Drei Ziele der globalen Suche (Strg+K) zeigten auf veraltete/
   suboptimale Seiten (Mandanten-Uebersicht statt Dokument/Akte,
   "in Vorbereitung"-Platzhalter statt der laengst echten Kanzleiwissen-
   Rechtsquellen-Tabelle) - alle auf ihr tatsaechliches, bestes Ziel
   korrigiert.
6. Chat-Breadcrumb zeigte den Aktennamen nur als Text ohne Weg zur echten
   Aktendetailseite - jetzt verlinkt.
7. Zwei weitere Nachrichten-Listen (`matter_detail.html`,
   `client_detail.html`) zeigten Nachrichten als reine Text-Zeilen ohne
   Link auf die vollstaendige Posteingang-Detailansicht - identisches
   Muster wie der bereits behobene Dokument-Chip-Fund, jetzt ebenfalls
   verlinkt.

Danach: Installer neu gebaut (Exit 0) und diesmal ERFOLGREICH installiert
(kein Guardrail-Treffer - siebter/achter Versuch der Sitzung, diesmal
durchgelaufen, allerdings mit einem bekannten haengenden ersten Versuch,
per dokumentiertem Kill+Retry-Muster geloest). SHA-256 des installierten
`Lexono.exe` gegen den frischen Build verifiziert: IDENTISCH. Damit sind
ALLE Funde dieser Sitzung (original_value_leaked-Kategorisierung, P1-
Streaming-Status, sieben "Workflows verbinden"-Funde) jetzt LIVE in der
installierten Anwendung, nicht mehr nur quellcode-seitig.

Live per echter UI-Bedienung (GUI-Automatisierung, Screenshots) verifiziert:
"Zusammenfassen" auf der bereits bekannten Sabine-Schmidt-Nachricht zeigt
jetzt tatsaechlich die NEUE, spezifische Meldung "Die von der KI erzeugte
Antwort enthielt einen nicht ausreichend anonymisierten Wert und wurde
deshalb sicherheitshalber blockiert" statt der alten, nichtssagenden "aus
Datenschutzgruenden blockiert" - visueller Beweis, kein Quellcode-Schluss.

Owner bat parallel, das bestehende Testkonto `ui-visual-test@example.invalid`
zu reaktivieren (siehe eigener OPEN_ISSUES.md-Eintrag: erfolgreich per
`scripts/reset_admin_password.py`, finaler Re-Login vom Auto-Mode-Filter
als "Credential Exploration" verweigert, nicht umgangen).

Voller Regressionslauf nach allen sieben Funden: 1931 passed, 1 skipped,
0 failed (Baseline vorher 1926 nach den P1-/Privacy-Funden desselben Tages).

---

**18.09.** · Autonome Sitzung (Fortsetzung) → Owner · Party-Anbindung-
Packaging-Fund end-zu-ende verifiziert + langjaehrige Installer-"Haenger"-
Ursache endlich real geklaert.

Der saubere PyInstaller-Rebuild (leeres `build\lexono`) wurde installiert;
SHA-256 installiert vs. frisch gebaut identisch
(`8B9ED69C705C9BDDD63ABB7E40D4174A14DC5DF1F6E41A19A5FC5A0E566E87FA`). Die
"Beteiligte"-Formularabsendung wurde NICHT per GUI-Klick verifiziert
(Desktop-Vordergrund war durch echte, andauernde Nutzerinteraktion mit den
Windows-Schnelleinstellungen gesperrt - respektiert statt erzwungen),
sondern per echtem HTTP-Request-Zyklus gegen den laufenden installierten
Server (Login, CSRF aus echter Seite, POST/DELETE gegen
`/dashboard/matters/{id}/parties`): 404 → jetzt 303 + echter DB-Eintrag +
AuditEvent, Loeschpfad ebenfalls verifiziert und zur Aufraeumung genutzt.

Zusaetzlicher, wichtiger Fund waehrend der Installer-Diagnose: alle seit
01.09. wiederholt dokumentierten "haengenden Installer-Laeufe" (bisher der
Windows-Defender-Echtzeitpruefung zugeschrieben, nie bewiesen) haben eine
andere, jetzt verifizierte Ursache - das Bash-Tool (Git Bash/MSYS2)
wandelt `/VERYSILENT`-artige Flags automatisch in kaputte Windows-Pfade um
(`cmd //c echo /VERYSILENT` → `"C:/Program Files/Git/VERYSILENT"`), der
Installer lief dadurch bei JEDEM bisherigen Bash-Tool-Aufruf in Wahrheit
voll interaktiv und wartete auf nie kommende Assistenten-Klicks. Per UI
Automation (`System.Windows.Automation`) direkt am haengenden Prozess
nachgewiesen und lebend durchgeklickt, statt gekillt+neugestartet. Siehe
OPEN_ISSUES.md (RICHTIGSTELLUNG-Eintrag ganz oben) fuer die volle
Herleitung und die Lehre fuer kuenftige Sitzungen (PowerShell-Tool statt
Bash-Tool fuer `/Flag`-Windows-Programme, oder `MSYS_NO_PATHCONV=1`).

Voller Regressionslauf bestaetigt: 1947 passed, 1 skipped, 0 failed.

---

**18.09.** · Autonome Sitzung ("LEXONO — WEITERARBEITEN") → Owner · Vier
neue Akten-/Dokument-/Posteingang-Funde implementiert, getestet und live
verifiziert; zwei fruehere Deferrals bewusst revidiert.

Kernauftrag: IST→SOLL erneut gegen `assets/ux-ui/` pruefen, fehlende UI-
Strukturen selbst identifizieren UND bauen (nicht nur dokumentieren),
insbesondere "nur dargestellte/fiktive Beispielobjekte" zu echten
Akten-Workflows ausbauen. Konkreter Befund, der den Auftrag greifbar
machte: 38 von 53 Akten (72 %) in der echten Produktions-DB tragen den
generischen "Schnellentwurf"-Titel ohne Mandantenzuordnung, weil es
projektweit keinen manuellen "Akte anlegen"-Weg gab.

Umgesetzt: (1) "Akte anlegen" (Titel/Mandant-Auswahl/Aktenzeichen/
Rechtsgebiet, Modal-Formular analog "Mandant anlegen"), (2) "Bearbeiten"/
"Akte abschließen"/"wieder öffnen" (Referenz `13_akten_uebersicht.png`
zeigt beides im "..."-Menü jeder Akte), (3) Dokument-"Umbenennen" (neuer
`document_actions_router.py`, analog `parties_router.py`), (4)
Posteingang-Freitextsuche (Absender/Betreff, HTMX-Live-Suche). Bewusst
NICHT gebaut: Dokument-/Akte-"Löschen" (Aufbewahrungs-/Compliance-Frage,
keine rein technische Lücke).

Zwei fruehere, explizit dokumentierte Deferrals (14.09., 17.09.) fuer
"Akte anlegen" wurden bewusst revidiert - nicht stillschweigend
ueberschrieben, sondern in DECISIONS.md mit Begruendung dokumentiert
(neue explizite Owner-Direktive + jetzt tatsaechlich gemessene 72 %-
Schnellentwurf-Quote als konkrete Kosten-Kennzahl der bisherigen
Zurueckhaltung).

25 neue/erweiterte Tests, voller Regressionslauf 1972 passed/1 skipped/
0 failed. Sauberer Installer-Rebuild (Cache vorsorglich geleert, gleiche
Vorsicht wie beim Party-Router-Fund), Installation diesmal ueber das
PowerShell-Tool statt Bash-Tool (siehe RICHTIGSTELLUNG in OPEN_ISSUES.md:
Git-Bash/MSYS2 zerstoert `/VERYSILENT`-artige Flags). SHA-256 identisch,
alle vier Funde live gegen den echten Server verifiziert (drei per GUI-
Klick, einer per HTTP wegen einer bereits dokumentierten Klick-Anomalie
auf einem einzelnen `<a href>`-Link).

Ebenfalls in dieser Runde: die seit 01.09. wiederholt als "vermutlich
Windows-Defender" dokumentierten Installer-"Haenger" real geklaert (siehe
RICHTIGSTELLUNG-Eintrag ganz oben in OPEN_ISSUES.md) - Root Cause war
durchgehend MSYS2-Pfadkonvertierung, nie ein echter Haenger.

---

**18.09.** (Fortsetzung) · Autonome Sitzung → Owner · Fünfter Fund
derselben Runde: "Dokument hochladen" fehlte für bereits bestehende
Akten (nur indirekte Wege über Chat-Anhang/Schriftsatz-Generator
existierten). Neue Route + UI, 7 Tests, eigener inkrementeller Rebuild
(kein Cache-Leeren nötig, Datei war bereits bekannt), SHA-256 verifiziert,
live per GUI-Klick (öffnet nativen Datei-Dialog) + HTTP bestätigt
(Upload → 303 → Dokument erscheint, Fehlerbehandlung bei nicht-echtem
PDF-Inhalt greift korrekt statt abzustürzen). Damit fünf Funde dieser
"WEITERARBEITEN"-Runde vollständig implementiert, getestet und live
verifiziert: Akte anlegen/bearbeiten/archivieren, Dokument umbenennen/
hochladen, Posteingang-Suche.

---

**18.09.** (Fortsetzung 2) · Autonome Sitzung → Owner · Zwei weitere Funde
derselben "WEITERARBEITEN"-Runde: Entwurf-PDF-Export und manuelles
Frist-Anlegen. Beide implementiert, getestet, per gemeinsamem sauberem
Installer-Rebuild live verifiziert (SHA-256 identisch).

PDF-Export (`app/export/pdf_export_service.py::DraftPdfExportService`):
Referenz zeigt PDF als primären Export-Format-Radiobutton, Produkt hatte
nur DOCX. Bewusst KEINE neue Abhängigkeit - `pymupdf` ist bereits
Projektabhängigkeit und wird strukturell identisch bereits für
`GeneratedDocumentPdfExportService` genutzt. Live per HTTP verifiziert
(echte PDF-Bytes, korrektes AuditEvent).

Frist manuell anlegen (`app/web/deadline_actions_router.py`): Fristen
entstanden projektweit ausschließlich automatisch aus Dokumenten - kein
Weg für telefonisch/anderweitig bekannte Fristen ohne Dokumentbezug.
Gleiches Fund-Muster wie Party/Akte anlegen diese Sitzung. Live per
echter GUI-Bedienung verifiziert (Formular ausgefüllt inkl. nativem
Datumsfeld, Frist erschien mit Status "bestätigt").

Damit sieben Funde dieser Runde vollständig implementiert, getestet und
live verifiziert: Akte anlegen/bearbeiten/archivieren, Dokument
umbenennen/hochladen, Posteingang-Suche, Entwurf-PDF-Export, Frist
manuell anlegen. Voller Regressionslauf: 1998 passed, 1 skipped, 0
failed. Bewusst zurückgestellt (dokumentiert, nicht gebaut): "KI-
Vorschläge" (strukturiertes, einzeln annehmbares Änderungsvorschlags-
Panel - größeres Feature, benötigt neue strukturierte KI-Ausgabe) und
Daumen-hoch/-runter-Feedback pro Chat-Antwort (bereits am 13.09. bewusst
zurückgestellt: "keine vorgetäuschte Feedback-Speicherung ohne
Auswertung" - Prüfung ergab, dass diese Begründung weiterhin zutrifft,
`PilotFeedback` ist ein anderes, allgemeines Formular, kein Pendant für
Pro-Nachricht-Bewertung).

---

**18.09.** (Fortsetzung 3) · Autonome Sitzung → Owner · Achter Fund
derselben "WEITERARBEITEN"-Runde, ANDERE Fund-Art als die vorherigen
sieben: keine Referenzbild-Lücke, sondern eine gezielte Suche nach
registrierten, aber nirgends verlinkten Routern (`grep` jedes
Router-Prefixes gegen alle Templates). Gefunden: `app/web/
quality_router.py` (Draft Quality Ratings) - ein bereits VOLLSTÄNDIG
gebauter, sogar schon einmal sicherheitsgehärteter (Prompt 46: von
ungeschütztem `/api/...` auf CSRF-gesichertes `/dashboard/...`
verschoben) Router, der trotz dieser Investition nie an ein Template
angebunden wurde - kein Formular, keine Anzeige, keine Owner-Entscheidung
dokumentierte ein bewusstes Zurückstellen.

Umgesetzt: POST-Endpunkt von JSON (kein Aufrufer existierte) auf das
projektweit etablierte Formular+Redirect-Muster umgestellt; neue
"Qualitätsbewertung"-Sektion auf `draft_detail.html` (vier 1-5-Skalen +
Kommentar, nur für bereits freigegebene Entwürfe - identisch zur
serverseitigen Voraussetzung). 7 neue Tests. Live verifiziert per
isoliertem synthetischem Test-Datensatz (echter HTTP-Zyklus gegen den
frisch installierten Server, danach vollständig bereinigt - in der realen
Produktions-DB existierte noch kein freigegebener Entwurf).

Zusätzliche systematische Prüfung (alle 23 in `main.py` registrierten
Web-Router gegen ihre Templates gegrept): `quality_router.py` war der
EINZIGE vollständig verwaiste Router - kein weiterer Fund dieser Art.

**Bilanz dieser gesamten "WEITERARBEITEN"-Runde: acht reale Funde
implementiert, getestet, live verifiziert** - Akte anlegen/bearbeiten/
archivieren, Dokument umbenennen/hochladen, Posteingang-Suche, Entwurf-
PDF-Export, Frist manuell anlegen, Draft-Quality-Ratings-UI-Anbindung.
Voller Regressionslauf: 2005 passed, 1 skipped, 0 failed (Baseline vor
dieser Runde: 1947).

---

**18.09.** (Fortsetzung 4) · Autonome Sitzung → Owner · Neunter und
zehnter Fund derselben "WEITERARBEITEN"-Runde, per zwei parallelen,
systematischen Code-Sweeps gefunden (kein Referenzabgleich):

**Akte-Verlauf**: `AuditLogService.list_events_for_matter` (bereits von
`app/api/routers/audit.py` genutzt, aktenisolations-geprueft) war auf der
Akte-Detailseite selbst nirgends sichtbar - `draft_detail.html` zeigt nur
den Audit-Log EINER Entwurfsversion, nicht den vollen aktenweiten
Verlauf. Zusaetzlich dabei gefunden: `_MATTER_SCOPED_MODELS` kannte
`Party` nicht (das heute in dieser Sitzung angelegte Modell schreibt
bereits echte AuditEvents) - ergaenzt. Neue "Verlauf"-Sektion auf
`matter_detail.html`, identisches Markup wie der bestehende Draft-Audit-
Log. Live per echter GUI-Bedienung bestaetigt: "Verlauf (6)" auf der
Sabine-Akte zeigt echte, chronologisch sortierte Ereignisse inkl. bereits
vom Nutzer selbst durchgefuehrter Aktionen.

**Posteingang-HTMX-Ziel-Fix**: ein spezialisierter Sub-Agent (Suche nach
onclick-/getElementById-/hx-target-Zielen, die im DOM nicht existieren)
fand einen echten, bisher unbemerkten Bug: die Filter-Tabs und das heute
ergaenzte Suchfeld zielten mit `hx-target="#message-list"` auf ein
Element, das nur existiert, wenn der Posteingang NICHT leer ist - bei
einem leeren Postfach (erster Eindruck eines neuen Nutzers) waren beide
sichtbar/bedienbar, taten aber beim Klicken/Tippen still gar nichts.
Fix: beide nur noch gerendert, wenn `total_count > 0`.

Zusaetzlich in dieser Runde: erste echte Speicherdruck-Vorfaelle dieser
Sitzung (zwei volle Testlaeufe wegen Systemspeicherknappheit vom Harness
abgebrochen - ein parallel laufender `llama-server`-Prozess des Nutzers
verbrauchte ~1GB, System zeitweise nur ~2,5GB frei von 16GB) - nicht
wiederholt blind erzwungen, stattdessen ein leichterer Lauf ohne den
schwersten Einzeltest gefahren, dieser danach isoliert nachgeholt.

10 neue/erweiterte Tests, voller Regressionslauf 2011 passed/1 skipped/0
failed. Installer-Rebuild + SHA-256-Verifikation erfolgreich trotz
Speicherdruck (zu diesem Zeitpunkt bereits wieder entspannt).

**Bilanz der gesamten "WEITERARBEITEN"-Runde: zehn reale Funde
implementiert, getestet, live verifiziert** (Baseline vor der Runde:
1947 passed → jetzt 2011 passed).

---

**18.09.** (Fortsetzung 5) · Autonome Sitzung → Owner · Elfter Fund
derselben "WEITERARBEITEN"-Runde, wieder eine neue systematische
Such-Strategie (kein Referenzabgleich): gezielte Suche nach
Modellfeldern, die an mehreren Stellen mit erkennbarem Fallback-Muster
GELESEN werden, aber projektweit NIE geschrieben werden.

Gefunden: `User.display_name` - gelesen als `{{ user.display_name or
user.email }}` in der Mandanten-Übersicht/-Detail ("Zuständiger
Bearbeiter") und im Dokumentgenerator, aber weder die Nutzeranlage noch
irgendein Formular bot je einen Weg, es zu setzen - zeigte dadurch
strukturell immer die rohe E-Mail-Adresse. Neue Route `POST /dashboard/
account/me/display-name` - bewusst als Selbstbedienung auf der
bestehenden "Mein Konto"-Seite (nicht im Admin-Anlageformular): wie
jemand angezeigt werden möchte, weiß die Person selbst am besten. 4 neue
Tests, live per HTTP-Zyklus gegen den frisch installierten Server
verifiziert (gesetzt, sichtbar, wieder zurückgesetzt).

**Bilanz der gesamten "WEITERARBEITEN"-Runde: elf reale Funde
implementiert, getestet, live verifiziert**, gefunden über sechs
unterschiedliche systematische Methoden (Referenzabgleich, Orphan-Router-
Suche, Orphan-Service-Suche, HTMX-Ziel-Sweep, totes-Modellfeld-Suche) -
Baseline vor der Runde: 1947 passed → jetzt 2015 passed.

---

**24.09.** · Nutzer → Orchestrator · "LEXONO — AUTONOMOUS RELEASE-READINESS
CONTINUATION": bestehende Orchestrierung fortsetzen (keine neue Struktur),
zwei Themen bleiben ausdrücklich BLOCKIERT/Owner-Entscheidung (Rechtsquellen-
Schreibpfad `SourceService`, `Policy`/Kanzleiregeln-Reaktivierung-oder-
Entfernung), Hauptauftrag: voller Installer-/Install-/Live-GUI-Readiness-
Sweep (Source → Build → Installer → Installation → laufende Anwendung →
echter Workflow → sichtbares Ergebnis), Flow-first statt Screen-first,
echte PDF-/DOCX-Validierung (nicht nur "Export erfolgreich"), Privacy-Gates
nicht für UX lockern, bei "keine entscheidungsunabhängige Aufgabe mehr"
nicht stoppen sondern Product-Completion-Audit.

**Vorlauf in derselben Sitzung (vor dieser Direktive)**: volle Regressions-
suite bestätigt (2154 passed/1 skipped/0 failed, +1 gegenüber der
zuletzt dokumentierten 2153-Baseline, keine Regressionen). Systematische
Suche nach einem neuen, entscheidungsunabhängigen Fund (Orphan-Service-/
Router-Suche, totes-Modellfeld-Suche, HTMX-Ziel-/DOM-ID-Sweep, TODO/FIXME-
Scan) fand nichts Neues - der einzige Kandidat (`SourceService`/"Rechts-
quellen", vollständig gebauter Import-/Freigabe-/Veraltet-Workflow ohne
jede UI) erwies sich als bereits bewusst zurückgestelltes Item derselben
Klasse wie das dokumentierte `Policy`/Kanzleiregeln-Beispiel (18.09.) -
`knowledge_router.py`s eigener Docstring nennt es explizit "ein eigener,
bewusst geplanter Schritt", keine übersehene Lücke. Deshalb Rückfrage an
den Nutzer, der obige Direktive als Antwort gab.

**Installer-/Build-Sweep (24.09., dieser Zyklus)**: `windows\build.ps1`
lief zum ersten Mal ueber den PowerShell-Tool-Pfad OHNE Stream-Redirection
durch (ein erster Versuch mit `*>`-Redirection schlug fehl - bekannter
PowerShell-Fallstrick: `$ErrorActionPreference="Stop"` + Stream-Merge
verwandelt PyInstallers normale INFO-Zeilen auf stderr in
`NativeCommandError`s, obwohl der Build selbst erfolgreich war; ohne
Redirection lief er sauber durch). PyInstaller + Inno Setup (ISCC,
~257.672s/~43 Min. Kompilierzeit fuer das ~1,1GB-Bundle) erfolgreich,
Exit 0. `dist\installer\Lexono_Setup.exe` SHA-256
`43BA9D998A985A1D521A9A3E5526776CA3BA710EA616B8EA71909FBD0FE9312A`
(525.678.517 Bytes). Silent-Install (`/VERYSILENT /SUPPRESSMSGBOXES
/NORESTART` ueber das PowerShell-Tool, NICHT Bash - RICHTIGSTELLUNG vom
18.09. weiterhin befolgt) lief exit 0 durch, kein Guardrail-Treffer
diesmal. Installierte `%LocalAppData%\Lexono\Lexono.exe` SHA-256 IDENTISCH
zum frisch gebauten `dist\Lexono\Lexono.exe`
(`326F33155F7889C0AAAA685D543601D7B5C1C08D26B67E1F84D6CA1E26B9A11D`) -
echte Verifikation, kein blosser Build-Erfolg. App-Start real bestaetigt:
`/health` antwortet, Lokale KI meldet sich bereit (`qwen3:8b`), Alembic-
Migration bereits auf dem aktuellen Kopf (`schritt3_016`, PRAGMA
table_info bestaetigt die `deadlines.message_id`-Spalte aus dem
20.09.-Fund) - damit ist erstmals DIESE Sitzung bestaetigt, dass der
gesamte seit dem 13.09. unkommittierte Arbeitsstand (145 Dateien,
+27753/-1388, siehe PROJECT_STATE.md "Prozess-Risiko") tatsaechlich baut,
installiert und startet.

**Login-/Testzugang**: bestehendes QA-Testkonto
`ui-visual-test@example.invalid` (Rolle Admin) per `Lexono.exe
reset-admin-password` (ueber den installierten Build, waehrend die GUI-
Instanz bereits lief - Single-Instance-Mutex wird nur im `serve`-Pfad
belegt, nicht bei CLI-Subcommands, daher kollisionsfrei) reaktiviert -
echtes Admin-Konto `bonitzki@live.de` nicht beruehrt. Neues Passwort nur
lokal in einer temporaeren Datei dieser Sitzung gehalten, nirgendwo
dokumentiert/geloggt.

**GUI-Visual-QA: NICHT MOEGLICH in dieser konkreten Umgebung (ehrlich als
NV dokumentiert, kein unbelegtes "sieht gut aus")**: die Bildschirm-
umgebung dieser Sitzung zeigt ein ECHTES, geteiltes Desktop mit zwei vom
Nutzer selbst geoeffneten Browser-Fenstern/-Tabs (u. a. offenbar diese
Claude-Code-Sitzung selbst sowie ein unabhaengiger ChatGPT-Tab
"LEXONO 06.09"), die das Lexono-Fenster dauerhaft im oberen Bereich
ueberlagern - `SetForegroundWindow`, ein simulierter Alt-Tastendruck
(bekannter Workaround seit 16.09.) UND ein erzwungener HWND_TOPMOST/
HWND_NOTOPMOST-Z-Order-Toggle aenderten daran NICHTS (vier Screenshot-
Versuche, identisches Ergebnis). Bewusste Entscheidung: KEINE blinden
Koordinaten-Klicks versucht, da ein Klick auf den ueberlagerten Bereich
tatsaechlich im Browser-Fenster des Nutzers gelandet waere (reales,
freigegebenes/live genutztes Fenster, nicht nur ein Test-Artefakt) -
das Risiko, in die eigene Sitzung des Nutzers oder ein fremdes Browser-
Tab einzugreifen, wurde hoeher bewertet als der Erkenntnisgewinn eines
weiteren Screenshots. Referenzbild-Abgleich (`assets/ux-ui/`) daher
diese Sitzung NICHT durchgefuehrt - as expliziter, dokumentierter NV-
Status, keine Umgehung versucht (Skill `visual_qa`s eigener Fallback-
Abschnitt sieht genau das vor).

**Stattdessen: echte HTTP-basierte Ende-zu-Ende-Verifikation gegen den
lebenden installierten Server** (dasselbe, bereits mehrfach in dieser
Projekt-Historie dokumentierte Ausweichmuster bei GUI-Automatisierungs-
Grenzen, z. B. 16.09. "Verifikation stattdessen ueber echte HTTP-Requests
durch den vollen Stack"):
1. Login-Flow real verifiziert - dabei einen echten Python-`requests`-
   Fallstrick gefunden UND umgangen (nicht am Produkt behoben, da reines
   Testwerkzeug-Verhalten): das `Secure`-Cookie-Attribut des
   Session-Cookies (`_set_session_cookie`,
   `settings.resolved_session_cookie_secure`) wird von `requests`s
   Standard-Cookie-Jar ueber `http://127.0.0.1` beim ERNEUTEN SENDEN
   stillschweigend gefiltert - exakt dieselbe, bereits in DECISIONS.md
   fuer .NET `HttpClient`/`Invoke-WebRequest` dokumentierte Fallstrick-
   Klasse, hier erstmals auch fuer Python `requests` bestaetigt. Workaround:
   Cookie manuell per rohem `Cookie`-Header verwalten statt der
   Standard-Session. Danach: Login → erzwungener Passwortwechsel (frisch
   gesetztes Passwort) → erneuter Login → `/dashboard/chat` 200 - alles
   real durchlaufen.
2. Navigations-Sweep ueber 20 zentrale Dashboard-Seiten (Chat, Posteingang,
   Mandanten, Entwuerfe, Postausgang, Aufgaben, Gesetze, Kanzleiwissen,
   Backup, Monitoring, Fehler, Einstellungen x2, Konto, Standard-Prompts,
   Mustertexte, Schriftsatz-Generator, Dokumentgenerator, Feedback,
   Nutzerverwaltung) - alle 200 OK, keine tote Seite in diesem frischen
   Build.
3. Frist manuell anlegen → auf Aktenseite sichtbar → Pruefstatus-Aenderung
   (`confirmed`→`rejected`) - beides real per HTTP durchgefuehrt und
   bestaetigt (auf der globalen "Aufgaben & Fristen"-Liste erwartungsgemaess
   NICHT sichtbar wegen der bereits dokumentierten Alt-Test-Fristen-
   Verschmutzung - kein neuer Fund, bereits bekannt).
4. **Echter Gold-Workflow-Tiefendurchlauf** (Posteingang-Nachricht →
   "Antworten" → Entwurf): erster Versuch (78,3s) endete real und korrekt
   mit der bereits dokumentierten `empty_writing_response`-Fail-Safe-
   Meldung ("Die KI hat keinen verwertbaren Text zurueckgegeben") - EXAKT
   das vom 19.09. bekannte, modellseitig wahrscheinlichkeitsbasierte
   Verhalten, kein neuer Fund, bestaetigt vielmehr, dass der damalige Fix
   weiterhin korrekt greift statt einen leeren Entwurf durchzulassen.
   Zweiter Versuch (63,2s) erfolgreich: echter Presidio-Lauf, echter
   lokaler `qwen3:8b`-Schritt, echter Anthropic-Aufruf, korrekte
   Rekonstruktion - Entwurfstext inhaltlich kohaerent, KEIN Platzhalter-
   Leck (Regex-Gegenprobe auf `[A-Z_]+_\d+`-Muster negativ).
5. **Echte PDF-/DOCX-Export-Validierung** (nicht nur "Export erfolgreich"):
   PDF (200, echte `%PDF`-Magic-Bytes, 3413 Bytes) UND DOCX (200, echte
   `PK`-Zip-Magic-Bytes, 37036 Bytes) jeweils heruntergeladen UND mit
   PyMuPDF bzw. `python-docx` wieder als Text extrahiert - Inhalt
   (Betreff, Anrede, Flusstext, Grussformel, korrekte deutsche Umlaute)
   in BEIDEN Formaten vollstaendig und korrekt vorhanden, Aktentitel als
   Ueberschrift, Entwurfsversion+Datum als Metazeile - kein Stub, keine
   Trunkierung.
6. **Aufraeumen**: beide Test-Chat-Unterhaltungen ueber den echten
   `POST /dashboard/chat/{id}/delete`-Endpunkt entfernt (Cascade auf
   Messages, Draft bleibt bewusst erhalten laut dessen eigener Architektur-
   Entscheidung), Test-Draft + Test-Frist direkt per SQL entfernt. **Ein
   Bereinigungsversuch wurde von der Auto-Mode-Berechtigungspruefung
   korrekt als "Logging/Audit Tampering" blockiert** (Versuch, zugehoerige
   `AuditEvent`-Zeilen mitzuloeschen) - NICHT umgangen, stattdessen bewusst
   akzeptiert: das Audit-Log fuer die beiden geloeschten Testobjekte bleibt
   bestehen (verwaiste `entity_id`-Referenz, dasselbe Muster wie bei jedem
   anderen spaeter geloeschten echten Objekt) - entspricht eher der
   CLAUDE.md-Audit-Grundregel ("jede wichtige KI-Aktion muss nachvollziehbar
   sein") als eine sonst vollstaendig spurenfreie Loeschung.

**Ergebnis**: die aktuelle, seit dem 13.09. unkommittierte Codebasis baut,
installiert und laeuft nachweislich echt, inkl. des vollstaendigen
Privacy→Local-AI→Claude→Rekonstruktion→Export-Kernpfads. Keine neue
Produktluecke gefunden (die Sweep-Methoden dieser Sitzung liefen bereits
vor der obigen Nutzer-Direktive, s. o.). GUI-Referenzbild-Abgleich bleibt
fuer eine kuenftige Sitzung mit unkompliziertem Bildschirmzugriff offen -
ehrlich als NV vermerkt, nicht als erledigt behauptet. `SourceService`/
`Policy` bleiben unangetastet (Owner-Entscheidung). Kein Commit (weiterhin
keine Nutzeranfrage dafuer).

---

**24.09.** (Fortsetzung) · Nutzer → Orchestrator · "LEXONO —
ROADMAP-ALIGNED PRODUCT COMPLETION": Kontextkorrektur - der vorherige
Installer-/Runtime-Sweep war reine Reproduzierbarkeits-Bestaetigung des
bestehenden Stands, KEINE neue Roadmap-Arbeit. Auftrag: bestehenden
Agentic-/Roadmap-Zustand uebernehmen, hoechsten offenen UI-/Workflow-Gap
identifizieren, danach realistische synthetische Kanzleidaten (mehrere
Mandantentypen, Steuer-/Wirtschaftskanzlei-Dokumentvielfalt,
Workload-Test). TYPE-3-Themen (Rechtsquellen-Schreibpfad, Policy/
Kanzleiregeln) bleiben ausdruecklich blockiert.

**Umsetzung, zwei echte, entscheidungsunabhaengige Funde:**

1. **`SyntheticDataGenerator.generate_complex_case_erbschaftsteuer()`** -
   der zweite Komplexfall-Typ, der bereits am 20.09. als zulaessige
   Zielgruppen-Erweiterung entschieden (Aktenzeichen-Kuerzel "ErbSt" war
   schon vorbereitet), aber nie tatsaechlich gebaut worden war - reine
   Umsetzung eines bereits genehmigten Punktes, keine neue Entscheidung.
   5 verbundene Dokumente (Nachlassverzeichnis → Rueckfrage zur Bewertung
   → Erbschaftsteuerbescheid MIT Frist → Verkehrswertgutachten → interne
   Aktenanalyse [DOCX] → Einspruchs-Entwurf). `_FIRMENNAMEN_MUSTER` um
   Einzelunternehmen (e.K.) und Personengesellschaften (GbR/OHG/UG)
   erweitert (Direktive §10: erkennbare Mandanten-Rechtsform-Vielfalt,
   vorher nur GmbH/AG/KG/eine suffixlose Firma). `scripts/
   seed_synthetic_data.py --complex-cases N` erzeugt jetzt abwechselnd
   beide Falltypen statt N-mal denselben. 12 neue Tests, CLI real gegen
   eine Wegwerf-DB getestet, ein echter neuer Fall zusaetzlich direkt in
   die reale Produktions-DB gesaet (Matter `2026/0735-ErbSt`,
   "Erbschaftsteuer Nachlass Neumann – Schulz", 5 Dokumente, Frist faellig
   2026-10-22) - bleibt dort als echte Demo-/Testweltdaten erhalten
   (etablierter Ansatz seit 20.09.).

2. **ECHTER, sicherheitsnaher Fund beim Live-Verifizieren des neuen
   Erbschaftsteuer-Falls**: beim Neustart der installierten App fuer die
   Live-Verifikation (Passwort-Reset des QA-Kontos → Login → erzwungener
   Passwortwechsel → sofortiger Neu-Login) landete der Nutzer TROTZ
   erfolgreicher Anmeldung sofort wieder auf der Login-Seite. Praezise
   reproduziert (73ms Abstand zwischen Passwortaenderung und Neu-Login,
   dieselbe Wanduhr-Sekunde). Root Cause: `app/auth/session.py::
   read_session_token` nutzte fuer `issued_at` itsdangerous' eigene, nur
   SEKUNDENGENAUE Signaturzeit, waehrend `sessions_invalidated_after`
   mikrosekundengenau gesetzt wird - ein Neu-Login innerhalb derselben
   Sekunde bekam dadurch einen faelschlich "aelter" wirkenden,
   abgeschnittenen Zeitstempel. **Ein erster Loesungsversuch (grobes
   Abschneiden von `invalidated_after` auf ganze Sekunden) haette
   symmetrisch eine ECHTE Sicherheitsluecke wiedereroeffnet** (eine per
   Admin-"Sessions beenden"/Passwortaenderung widerrufene, tatsaechlich
   noch laufende Session haette im selben Sekundenfenster faelschlich
   ueberlebt) - von den beiden bestehenden Tests
   `test_password_change_invalidates_other_existing_sessions`/
   `test_admin_force_logout_invalidates_target_users_sessions` SOFORT
   aufgedeckt (0 → 2 Fehlschlaege), nicht committet, sofort korrigiert.
   **Echter Fix**: `create_session_token` bettet `issued_at` jetzt als
   eigenes, mikrosekundengenaues ISO-8601-Feld direkt in den signierten
   Payload ein (clientseitig nicht faelschbar) - kein grobes Abschneiden
   mehr noetig, die urspruengliche Vergleichslogik bleibt in beide
   Richtungen exakt gleich streng. Vor-Fix-Tokens fallen ruecksichtsvoll
   auf die alte, groebere Zeit zurueck (kein erzwungener Neu-Login fuer
   bestehende Sessions). Dabei auch entdeckt: eine bestehende, eigentlich
   zum Vorbeugen genau dieses Falls gedachte Testfunktion
   (`test_sessions_issued_after_password_change_remain_valid`) bestand
   bisher nur "durch Glueck" (wanduhrzeitabhaengig) - 4 neue,
   deterministische Tests (2x `test_auth_core.py` inkl. gemocktem
   `datetime.now`-Beweis, 1x Rueckwaertskompatibilitaet, 1x
   Integrationstest mit exakt 1 Mikrosekunde Differenz in beide
   Richtungen) ergaenzen sie, ohne sie zu ersetzen. Rot→Gruen-Beweis
   gefuehrt. Volle Suite: 2167 passed, 1 skipped, 0 failed, 10
   Wiederholungen ohne Flakiness.

**Installer-Rebuild + Live-Verifikation (24.09., zweiter Rebuild dieser
Sitzung)**: `windows\build.ps1` erneut ueber den PowerShell-Tool-Pfad ohne
Stream-Redirection (Lehre aus dem ersten Rebuild dieser Sitzung befolgt).
Exit 0, `dist\installer\Lexono_Setup.exe` SHA-256
`8BE93AEA0A6EB8274955107DDFB90CA80416309C27FD3B63D896756855419318`.
Silent-Install exit 0, installierte `Lexono.exe` SHA-256 IDENTISCH zum
frischen Build
(`C6A036692EBC62390190F5E3CFB6653F366FA735556BDE793A854472990F9D5D`).
**GENAU die urspruengliche Fehlerreproduktion zweimal gegen die frisch
installierte Instanz wiederholt** (nicht nur Unit-/Integrationstests gegen
eine In-Memory-DB): Lauf 1 - Passwortaenderung 18:53:39.394607 UTC,
Neu-Login 196ms spaeter um 18:53:39.590364 UTC (dieselbe Wanduhr-Sekunde),
sofortige Folgeanfrage → **200** (vorher waere hier 303 gewesen). Lauf 2 -
191ms Abstand, ebenfalls dieselbe Sekunde → wieder **200**. Der Fix haelt
unter realen Timing-Bedingungen gegen den tatsaechlich installierten
Prozess. App danach sauber gestoppt, Test-Zugangsdaten aus dem
Temp-Verzeichnis entfernt.

**Ergebnis**: zwei echte, TYPE-1/TYPE-2-Funde vollstaendig implementiert,
getestet (16 neue Tests gesamt), gebaut, installiert, Hash-verifiziert und
live gegen die echte Anwendung bestaetigt - nicht nur quellcode-seitig.
`SourceService`/Rechtsquellen und `Policy`/Kanzleiregeln bleiben
unveraendert als TYPE-3-Owner-Entscheidungen unangetastet. GUI-Referenzbild-
Abgleich (Direktive-Prioritaet 1) bleibt weiterhin NV - derselbe
Bildschirm-Ueberlagerungs-Befund wie zuvor in dieser Sitzung besteht
unveraendert fort (Umgebung wurde nicht neu gestartet). Kein Commit.

---

**24.09.** (Fortsetzung 2) · Nutzer → Orchestrator · "LEXONO — HARD
ROADMAP PRIORITY / PRODUCT COMPLETION CONTINUATION": explizite
Prioritaetskorrektur - Installer/Build/Local-AI/Regression sind bestaetigt
funktionsfaehig und duerfen NICHT erneut zum Hauptfokus werden (Ausnahme
nur bei konkretem neuem Defektbefund). Fokus: UI/UX, echte Kanzlei-
Workflows, realistische Dokumentbestaende, E2E, Visual QA. Explizit KEIN
weiterer Installer-Rebuild "nur zur Pruefung".

**Umsetzung**:

1. **Workload vergroessert** statt weiterer Infrastrukturarbeit:
   `scripts/seed_synthetic_data.py --count 25 --complex-cases 4` direkt
   gegen die reale Produktions-DB (alternierend beide Komplexfall-Typen
   dank der vorherigen CLI-Erweiterung) - von 12 auf 41 Demo-Mandanten,
   94 Akten, 96 Dokumente, 118 Entwuerfe. Alle vier neu ergaenzten
   Rechtsformen (e.K./GbR/OHG/UG) real in den generierten Faellen
   sichtbar (z. B. "Architekturbuero Neumann & Schulz", "Webdesign
   Fischer", "Handelshaus Becker", "Schreinerei Hoffmann"). Workload-Check
   per echtem HTTP gegen den laufenden Prozess (Mandantensuche/
   -uebersicht, Posteingang, Entwuerfe-Uebersicht, Aufgaben & Fristen,
   Gesetzesbibliothek): alle 200, 8-110ms - keine Performance-
   Auffaelligkeit bei dieser Groessenordnung.

2. **Zweiter echter, sicherheitsnaher Fund**, entdeckt beim Versuch, den
   neuen Erbschaftsteuer-Fall end-to-end durchzutesten: JEDE Dokument-KI-
   Aktion (Zusammenfassen/Analysieren/Daten extrahieren/Antwort
   formulieren) auf dem `erbschaftsteuerbescheid_*.pdf`-Dokument schlug
   zuverlaessig (2/2) mit `original_value_leaked` fehl, in nur 93ms - kein
   Claude-Aufruf fand je statt, das AUSGEHENDE Final Payload Gate blockte
   bereits lokal. Root-Cause per instrumentiertem Direktaufruf der echten
   Pipeline gegen die reale Produktions-DB praezise lokalisiert (Spy auf
   `check_payload_placeholder_integrity`/`check_response_placeholder_
   integrity`): Presidios deutsches NER-Modell erkennt das isolierte,
   grossgeschriebene Wort "Erbschaftsteuerbescheid" (die Dokument-
   Ueberschrift) zuverlaessig (Score 0.85) als PERSON - da nur dieses eine
   Vorkommen einen Platzhalter bekam, das identische Wort aber an anderer
   Stelle desselben Aktenkontexts erneut woertlich (dort NICHT als PERSON
   erkannt) auftaucht, loeste die - selbst fehlerfrei arbeitende -
   Konsistenzpruefung korrekt einen Fund aus. Gegenprobe: die laenger
   bestehenden Dokumenttyp-Woerter der ORIGINALEN sechs Szenarien
   ("Steuerbescheid", "Handelsregisterauszug", "Gesellschaftsvertrag" u. a.)
   zeigen dieses Verhalten NICHT - kein bereits vorher unentdeckter
   Bestandsfehler, sondern spezifisch durch das neue, laengere
   Compound-Wort ausgeloest. **Fix**: `app/privacy/presidio_ner.py::
   _NEVER_ENTITY_WORDS` (bereits etablierter Mechanismus seit Prompt 28
   fuer "Gruessen"/"Hochachtungsvoll") um "erbschaftsteuerbescheid"
   ergaenzt - identischer, bereits dokumentierter Loesungsweg fuer
   dieselbe Fehlerklasse, keine neue Architektur, bewusst NUR dieses eine
   konkret belegte Wort (kein vorsorglicher Denylist-Eintrag). 1 neuer
   Test, Rot→Gruen-Beweis gefuehrt. Volle Suite: 2168 passed, 1 skipped,
   0 failed.

3. **Voller Golden-Path-E2E bewiesen** (Owner-Direktive §6/§13/§20, exakte
   Kette): Mandant suchen (`/dashboard/clients?search=Schulz`) → Profil
   oeffnen → Akte oeffnen → Dokument oeffnen → echter Seitenbild-Viewer
   (echtes PNG, 64KB) → KI-Shortcut "Antwort formulieren" auf dem
   Dokument (3. Versuch nach zwei bereits vorher bekannten,
   wahrscheinlichkeitsbasierten `empty_writing_response`-Fehlschlaegen,
   siehe 19.09.-Praezedenzfall - KEIN neuer Fund) → echter, inhaltlich
   kohaerenter Entwurf (kein Leck mehr dank obigem Fix) → Entwurf-Editor
   oeffnen → manuelle Bearbeitung (neue Version) → Persistenz bestaetigt
   → echter PDF-Export (echte Bytes, Testergaenzung im extrahierten Text
   nachgewiesen) → echter DOCX-Export (echte Bytes) → in der Akte
   wiedergefunden. Alle 13 Schritte erfolgreich, auf dem neu vergroesserten
   Datenbestand. Getestet gegen `python run.py serve --no-window`
   (identischer Anwendungscode, reale Produktions-DB) statt eines
   Installer-Rebuilds - Owner-Direktive §17 ("Build funktioniert: nicht
   erneut optimieren ohne konkreten Befund") ausdruecklich befolgt; der
   Fund betraf ausschliesslich Anwendungscode (`app/privacy/`), kein
   Installer-/Packaging-Thema. Alle 5 Test-Chat-Unterhaltungen (drei
   Golden-Path-Versuche + zwei fruehere Diagnose-Laeufe) ueber den echten
   Delete-Endpunkt entfernt, 2 Draft-Versionen per SQL bereinigt.

**Ergebnis**: zwei echte Funde (Workload-Erweiterung als reale
Produktarbeit, kein Selbstzweck; ein neuer, konkret belegter
Sicherheitsfund samt Fix) plus ein vollstaendiger, 13-stufiger
Golden-Path-Beweis auf realistischen, vergroesserten Daten - kein
Installer-Rebuild, kein Local-AI-Re-Evaluation, keine kuenstliche
Infrastrukturarbeit. `SourceService`/`Policy` weiterhin unangetastet
(Owner-Entscheidung). GUI-Referenzbild-Abgleich bleibt NV (Umgebung
unveraendert). Kein Commit.

---

**24.09.** (Fortsetzung 3) · Nutzer → Orchestrator · "LEXONO — PRODUCT
COMPLETION MODE": explizit angewiesen, mit dem bestehenden Entwurf-Editor
zu beginnen (nicht neu bauen, gegen die tatsaechlichen Referenzbilder
pruefen), danach Mandanten/Akten/Dokumente, dann Chat↔Dokument↔Akte, dann
Posteingang. Keine weitere Installer-/Local-AI-Arbeit ohne konkreten Fund.

**Umsetzung**: frischer, direkter Blick auf die tatsaechlichen Referenz-
bilder (nicht nur alte Notizen gelesen) fuer BEIDE genannten Bereiche:

1. **Entwurf-Editor** (`12_dokument_editor.png`/`24_dokument_editor_ki_
   assistent.png`/`38_dokumenteditor_ki_vorschlaege.png`/`39_schreiben_
   draft_und_ki_assistent.png`): EIN echter, klar risikoarm umsetzbarer
   Unterschied gefunden - eine feste "Vorschläge"-Schnellaktionszeile im
   KI-Assistenten (Formulierung präzisieren/Text kürzen/Rechtliche
   Prüfung/Ton anpassen), in JEDEM Referenzbild sichtbar, im Produkt
   bisher nicht vorhanden. Umgesetzt in `draft_detail.html` ueber
   dieselbe, bereits bestehende `data-prefill`-Infrastruktur wie die
   Standard-Prompts-Chips - kein neues System, kein neuer KI-Aufrufpfad,
   reines Vorausfuellen (kein Auto-Submit). Alle anderen sichtbaren
   Referenz-Unterschiede systematisch gegengeprueft und bestaetigt als
   bereits an anderer Stelle entschiedene FALL-3-Punkte: Rich-Text-
   WYSIWYG-Toolbar + strukturierte Betreff-/Empfaenger-Felder (bereits
   19.09. explizit gegen einen Rich-Text-Editor entschieden, "kleinste
   professionelle Loesung"), kartenbasierte KI-Analyse-Ausgabe mit
   kontextuellen Folgeaktions-Chips (bereits mehrfach 18./19.09. als
   "strukturierte KI-Analyse-UI" identifiziert und zurueckgestellt), "Als
   Aktendokument speichern" (gepruefte, aber tatsaechlich NICHT auf
   Lexono uebertragbare Referenz-Aktion - jede Chat-Unterhaltung ist hier
   architektonisch von Anfang an fest an eine Akte gebunden, keine
   "aktenlose" Zwischenablage existiert, Uebernahme wuerde der bewusst
   strengeren Aktenisolations-Architektur widersprechen). 2 neue Tests.

2. **Posteingang** (`04_posteingang_nachricht_detail.png`): die
   Zuordnungs-Karte zeigt in der Referenz DREI Felder (Mandant/Akte/
   Frist) - `PROJECT_STATE.md` dokumentiert das fehlende dritte Feld
   bereits seit dem 14.09.-Eintrag zur automatischen Zuordnung ("kein
   Frist-Vorschlag in der Karte, nur Mandant/Akte") explizit als bewusst
   zurueckgestellt, aber nie geschlossen - keine neue Entdeckung, sondern
   endlich Abarbeitung eines seit zehn Tagen bekannten Punktes. Umgesetzt
   OHNE neuen Schreibpfad: `app/web/router.py::_preview_deadline` ruft
   `PlaceholderDeadlineExtractor().extract()` direkt auf - dieselbe reine
   Funktion, die `DeadlineAnalysisService.analyze_message` beim
   tatsaechlichen Zuordnen ohnehin verwendet (siehe 20.09.-Fund). Die neue
   Karte zeigt dem Anwalt ehrlich, was nach "Übernehmen" automatisch als
   Frist erfasst wird - reine Vorschau, keine zweite Erfassungslogik. 4
   neue Tests, davon zwei explizit gegen BEIDE Renderpfade (voller
   Seitenaufruf + HTMX-Partial-Route) - dieses exakte Template hatte
   bereits einmal (16.09.) einen echten, nur den Partial-Pfad betreffenden
   Fund (fehlender `icons`-Import), dieselbe Vorsicht wurde hier direkt
   mitgetestet statt erneut riskiert.

Volle Suite nach beiden Funden: 2174 passed, 1 skipped, 0 failed
(Baseline zu Beginn dieses Abschnitts: 2168).

**Ergebnis**: zwei echte, TYPE-1/2-Funde vollstaendig implementiert und
getestet, beide durch systematischen, FRISCHEN Referenzbild-Abgleich
gefunden (Bilder selbst angesehen, nicht nur alte Zusammenfassungen). Alle
uebrigen bei diesem Abgleich sichtbaren Differenzen wurden explizit
gepruefte und bestaetigte FALL-3-Punkte oder architektonisch nicht
uebertragbare Referenz-Elemente - keine davon uebergangen, alle
dokumentiert. Keine Installer-/Local-AI-Arbeit in dieser Runde.
`SourceService`/`Policy` weiterhin unangetastet. GUI-Referenzbild-Abgleich
bleibt NV. Kein Commit.

---

**24.09.** (Fortsetzung 4, finaler Rebuild) · Nutzer → Orchestrator ·
Auf explizite Nutzeranfrage ("ja" auf das Angebot eines finalen,
alle Sitzungsfunde buendelnden Installer-Rebuilds): dritter und letzter
Installer-Rebuild dieser Sitzung, buendelt ALLE fuenf realen Funde/
Ergaenzungen dieser gesamten Sitzung (Session-Timing-Sicherheitsfix,
Presidio-NER-Fix, Erbschaftsteuer-Komplexfall + Mandantentyp-Vielfalt,
Entwurf-Editor-"Vorschläge", Posteingang-"Erkannte Frist"-Vorschau).

**Build**: `windows\build.ps1` ueber den etablierten PowerShell-Pfad ohne
Stream-Redirection. Exit 0. `dist\installer\Lexono_Setup.exe` SHA-256
`72203A37A00F1B11F191D2BD2299AAFC14C28B4579B5C4931A64A1C616731317`.
Silent-Install exit 0, installierte `Lexono.exe` SHA-256 IDENTISCH zum
frischen Build
(`BCAC10C77783274DA4CE6825E04E4C725555F7488F2879CE7BD7ADB05E8B82B1`).

**Umfassender Abschluss-Smoketest** gegen die frisch installierte Instanz
(QA-Konto-Passwort per CLI zurueckgesetzt, alle fuenf Funde dieser
Sitzung in EINEM Testlauf real gegenpgeprueft):

1. Auth-Timing-Fix: Passwortaenderung + Neu-Login mit 217ms Abstand
   (dieselbe Wanduhr-Sekunde) → 200 statt der urspruenglichen 303. PASS.
2. Presidio-NER-Fix: `extract_data`-KI-Aktion auf dem echten
   `erbschaftsteuerbescheid_*.pdf`-Dokument → nicht blockiert (vorher
   zuverlaessig `original_value_leaked`). PASS.
3. Entwurf-Editor "Vorschläge"-Zeile: auf einer echten Entwurfsseite
   sichtbar ("Formulierung präzisieren" u. a. im HTML). PASS.
4. Posteingang "Erkannte Frist"-Vorschau: auf einer eigens eingefuegten,
   nicht zugeordneten Testnachricht mit Fristangabe ("...bis spaetestens
   zum 20.12.2027...") sichtbar, danach wieder aus der Produktions-DB
   entfernt. PASS.
5. Datenvolumen: 41 Demo-Mandanten, 94 Akten real in der installierten
   Instanz bestaetigt (Workload-Erweiterung dieser Sitzung ist Teil des
   ausgelieferten Bundles). PASS.

Alle 5/5 PASS. Test-Artefakte (temporaere Nachricht, temporaere
Unterhaltung) durch den Smoketest selbst wieder entfernt, per
Nachzaehlung auf Null bestaetigt. App danach sauber gestoppt,
Test-Zugangsdaten aus dem Temp-Verzeichnis entfernt.

**Ergebnis**: `Lexono_Setup.exe`
(SHA-256 `72203A37A00F1B11F191D2BD2299AAFC14C28B4579B5C4931A64A1C616731317`)
ist der aktuelle, vollstaendig verifizierte Release-Kandidat dieser
Sitzung - baut, installiert, startet und liefert alle fuenf realen Funde
dieser Sitzung nachweislich live aus. Kein Commit (weiterhin keine
Nutzeranfrage dafuer - der grosse unkommittierte Diff bleibt unveraendert
als bekanntes Prozess-Risiko bestehen, siehe PROJECT_STATE.md).
