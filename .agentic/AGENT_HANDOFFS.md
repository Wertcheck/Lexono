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
