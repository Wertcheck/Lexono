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
- **`.env`-Key-Injection über eingebettete Zeilenumbrüche**: **BEHOBEN**
  (01.09., Fund einer unabhängigen Subagent-Review der neuen
  Lokale-KI-Settings-Route, Commit `ba2f286`) - `format_env_value()`
  escapte `\`/`"`, nicht aber `\n`/`\r`; ein Formularwert mit
  eingebettetem Zeilenumbruch konnte aus seiner eigenen `KEY="..."`-Zeile
  ausbrechen und beliebige neue `.env`-Zeilen einschleusen (u. a.
  `SESSION_SECRET_KEY` überschreibbar). Betraf ALLE Aufrufer von
  `update_env_values` (Mail-/Aufbewahrungs-/jetzt Lokale-KI-Settings),
  nicht nur die neue Route - zentral behoben, 2 neue Regressionstests.
- Multi-Kanzlei-/Cross-Tenant-Unterstützung (`PROMPT38_ANALYSIS.md`):
  **OFFEN, PRODUKTENTSCHEIDUNG ERFORDERLICH** - nicht ungefragt begonnen.
- **Admin kann das Passwort eines NICHT-Admin-Nutzers nicht
  zurücksetzen (19.09., gefunden beim Login-Referenzbild-Abgleich)**:
  **BEHOBEN (19.09.)** - diese Zeile war veraltet (die Behebung war
  bereits erfolgt, aber hier nicht nachgetragen; 20.09. beim naechsten
  Arbeitsschritt bemerkt und korrigiert). `UserService.reset_password`
  (app/auth/service.py) + `POST /dashboard/admin/users/{id}/reset-
  password` (users_router.py, `require_role("admin")`) + Button in
  `admin_users.html`. 3 neue Tests, Live-QA gegen die installierte
  Instanz verifiziert. Details siehe OPEN_ISSUES.md ("Admin kann das
  Passwort eines NICHT-Admin-Nutzers projektweit nicht zuruecksetzen...
  BEHOBEN").
- **Session-Widerruf verglich Zeitstempel unterschiedlicher Praezision
  (24.09., real per Live-E2E-Test reproduziert)**: **BEHOBEN** - Neu-Login
  innerhalb derselben Wanduhr-Sekunde wie eine vorangegangene
  Passwortaenderung/Admin-Sperre wurde faelschlich sofort wieder
  abgemeldet (itsdangerous-Signaturzeit nur sekundengenau vs.
  mikrosekundengenaues `sessions_invalidated_after`). Fix in
  `app/auth/session.py` (eigenes mikrosekundengenaues `issued_at`-Feld im
  signierten Payload). Details + verworfener unsicherer erster
  Loesungsversuch siehe DECISIONS.md.

## B – Produktfunktion

- Chat, Dokument-Upload, Schriftsatz-Generator, bestehende Tools:
  **ERLEDIGT** (Vorsessions + heute integriert, nicht dupliziert).
- Feedback-/Kategorisierungssystem (`app/pilot_feedback/`): **ERLEDIGT**
  (bereits vorhanden, gegen Zielarchitektur geprüft, ausreichend für
  Pilotphase).
- Kanzleiwissen: Quellen/Textbausteine manuell erfassen
  ("Rechtsprechung"/"Interne Dokumente"/"Fachwissen"): **ERLEDIGT**
  (26.09., Owner-Direktive "AUTONOMOUS PRODUCT GAP AUDIT → PRIORITIZE →
  EXECUTE"). Backend (`SourceService`/`KnowledgeItemService`) war
  bereits fertig/getestet, hatte aber keine Web-Route - jetzt sechs neue
  kuratorengeschuetzte POST-Routen (Erfassen/Freigeben/Veraltet-
  markieren/Deaktivieren) in `knowledge_router.py`. Siehe
  PROJECT_STATE.md/DECISIONS.md.

## C – Chat / UX

- Chat als Startseite, Composer, Enter/Shift+Enter: **ERLEDIGT**
  (Vorsessions).
- Büroklammer-Icon, Drag & Drop, Mikrofon-UI (bewusst nicht
  cloud-angebunden): **ERLEDIGT** (heute Nacht).
- KI-Ladezustand (Puls-Sprechblase): **ERLEDIGT** (heute Nacht,
  Commit `f55925b`).
- Eigene Fenster-Titelleiste (Task #61): **ÜBERHOLT (12.09.)** - per
  explizitem Nutzerauftrag ("Master Agentic Execution Prompt") wieder auf
  natives OS-Fenster-Chrome (inkl. nativer abgerundeter Ecken via DWM)
  umgestellt; die eigene Titelleiste existiert nicht mehr. Real verifiziert
  über echte Win32/DWM-API-Abfragen gegen den laufenden Prozess. Siehe
  `LEXONO_MASTER_PRODUCT.md` §19 ("Drift #1") - dieser Eintrag blieb
  bewusst als historischer Stand stehen, beschreibt aber NICHT mehr die
  aktuelle Architektur. `ARCHITECTURE.md`s Kanonik-Block ist auf diesem
  Punkt ebenfalls noch veraltet und noch nicht korrigiert.
  **UPDATE (19.09.)**: die ECHTE native Titelleiste zeigte trotz des
  Rueckbaus weiterhin hartcodiert den Text "Lexono"
  (`webview.create_window("Lexono", ...)` in `run.py`) - zusammen mit der
  eigenen Marke in Sidebar/Login-Karte eine doppelte Darstellung (Owner-
  Fund, per neuem Login-Referenzbild bestaetigt). Behoben: Fenstertitel
  jetzt leer (`""`), Taskleisten-/Alt+Tab-Icon unveraendert (kommt aus der
  .exe-Ressource). Siehe OPEN_ISSUES.md.

## D – Dokumentworkflow

- Dokument-Workspace mit Pseudonymisierungs-Highlighting: **ERLEDIGT**
  (textbasiert, nicht PDF-Seiten-Rendering - bewusste Entscheidung,
  siehe OPEN_ISSUES.md).
- **Schnellaktionen im Dokument-Workspace** ("Antwort entwerfen"/"Fristen &
  Risiken prüfen"/"Zusammenfassung erstellen", Referenzbild 2): **ERLEDIGT**
  (01.09., später) - Wiederverwendung des bestehenden Prefill-Mechanismus,
  keine neue Sende-/Analyse-Logik.
- OCR-Status-Anzeige, Fehlerzustände: **ERLEDIGT** (Vorsessions).
- **Posteingang-Fristenerkennung aus dem Nachrichtentext selbst
  (20.09.)**: **ERLEDIGT** - `DeadlineAnalysisService.analyze_message`
  (neu) erkennt Fristen jetzt auch direkt im E-Mail-Text, nicht nur in
  Dokumentanhängen; zusätzlich werden bereits verarbeitete Anhänge, die
  vor ihrer Aktenzuordnung übersprungen wurden, bei der Zuordnung
  nachträglich analysiert. Neue nullable `Deadline.message_id`-Spalte
  (Migration `schritt3_016`). Details siehe OPEN_ISSUES.md/DECISIONS.md.

## E – UI / Visual

- **Posteingang strukturell/visuell an Referenz angeglichen** (25.09.,
  Owner-Direktive "POSTEINGANG PRODUCT COMPLETION"): **ERLEDIGT** -
  Avatar+Anhang-Icon in der Liste, verbindliche Detail-Reihenfolge
  (Text vor Anhaengen vor Aktionen vor Zuordnung), Anhang-Karten mit
  echter Dateigroesse+Download, neue Akte-/Sortier-Filterleiste, Icon-
  Seitenkopf, `.split{min-height:0}`-Scroll-Root-Cause-Fix. Voller
  Referenz-Workflow (Posteingang->Nachricht->Anhang->Dokumentvorschau)
  in der echten `Lexono.exe` bestaetigt. "Neue E-Mail"/"Alle Konten"/
  Absendertyp-Badges bewusst nicht gebaut (Decision Blocker/kein
  Backend), siehe OPEN_ISSUES.md/PROJECT_STATE.md.
- **Posteingang Final UI/UX, zweite Referenz-Runde** (25.09., Owner-
  Direktive "POSTEINGANG FINAL UI/UX PRODUCT-COMPLETION"): **P0-P4
  ERLEDIGT** - kein "← Zurück" mehr, kompakter Header, vier echte
  Filter-Dropdowns (Mandant NEU, Zeitraum NEU, Akte, Sortierung) auf
  einer gemeinsamen `hx-include`-Filter-Form, weiter verdichtete Liste,
  **automatische Erstauswahl der ersten Nachricht beim initialen Laden**
  (kein Leerzustand mehr). Anhang-Typ-Label-Bug ("DATEI" statt "PDF")
  waehrend eigener QA gefunden+behoben. Global-Sidebar bewusst
  unangetastet gelassen (siehe DECISIONS.md). Real in der installierten
  `Lexono.exe` verifiziert (Default-Auswahl + Auswahlwechsel + Struktur).
  **P5 (visuelle Feinabstimmung) und die Aufloesungen 1366×768/1920×1080
  bleiben fuer eine weitere Runde offen**, siehe OPEN_ISSUES.md.
- **Posteingang / Strict Reference Implementation** (26.09., Owner-
  Direktive "POSTEINGANG / STRICT REFERENCE IMPLEMENTATION - FINAL UI/UX
  CORRECTION ROUND", direkter Screenshot-Vergleich): **P0-P4 ERLEDIGT** -
  Liste/Detail von ~33/67 auf echte 50/50-Flex-Aufteilung korrigiert
  (P0-Hauptfund); Logo/globale Suche/Kopfzeilen-Icons EINMAL zentral in
  eine neue `.global-header`-Zeile verschoben statt seiten-lokal
  gepatcht; Sidebar-Suche + "Neuen Chat starten" entfernt (redundant zum
  Chat-eigenen "+"-Button); `GlobalSearchService` um eine echte
  E-Mail-Kategorie erweitert, damit der neue Suchtext "In E-Mails, ..."
  nicht faelschlich eine nicht existierende Funktion behauptet; rechter
  Detailbereich weiter kompaktiert + `max-width` gegen zu lange
  Textzeilen bei breiten Fenstern. Alle vier Auflösungen (1536×1024/
  1366×768/1920×1080/1280×720) real per Edge-Headless-Screenshot einer
  echten Serverantwort geprueft (kein Simulations-Vorbehalt mehr noetig -
  fruehere "kein physischer Zugriff"-Annahme war ein DPI-Messfehler).
  Real in neu gebauter/installierter `Lexono.exe` verifiziert
  (Struktur + Nachrichtenauswahl-Wechsel). **P5 (letzte visuelle
  Feinabstimmung) bleibt bewusst offen** - Kern-Layoutfehler (P0-P4) sind
  behoben, siehe OPEN_ISSUES.md fuer die verbleibenden, bewusst
  akzeptierten kosmetischen Abweichungen.
- **Posteingang Final Polish** (26.09., Owner-Direktive "POSTEINGANG
  FINAL POLISH - STRICT REFERENCE MATCH + VISUAL DENSITY + REAL
  WORKFLOW"): **ERLEDIGT, Posteingang-Runde damit abgeschlossen** -
  Filterzeile+Suche zu einer gemeinsamen Zeile zusammengefuehrt, volles
  Sortier-Dropdown durch kompakten Icon-Button ersetzt (dieselbe
  bestehende `sort`-Logik), mehrere Header-/Detail-Raender weiter
  reduziert. Bei der Referenzaufloesung 1536×1024 ist die komplette
  Detailstruktur (inkl. "Manuell einer Akte zuordnen") jetzt ohne
  Scrollen sichtbar. Echter Nebenfund: die native App ist nicht
  per-monitor-DPI-aware, effektiver Viewport bleibt ~1280×720 selbst bei
  physisch maximiertem 1920×1080-Fenster - bewusst NICHT in dieser Runde
  behoben (App-Shell-/Packaging-Scope, siehe OPEN_ISSUES.md fuer eine
  Empfehlung an eine kuenftige Direktive). 2 neue Tests, volle Suite
  gruen (2195/1/0). Installer neu gebaut, installiert, real verifiziert.
  **Naechster Fokus laut Direktive**: Dokumenteditor / reale
  KI-Aktionen / Schriftsatz-Workflow, nicht weitere Posteingang-Politur.
- **Kanzleiwissen Final Product Implementation** (26.09., Owner-Direktive
  "KANZLEIWISSEN FINAL PRODUCT IMPLEMENTATION / REFERENCE-DRIVEN UI +
  REAL LOCAL KNOWLEDGE MANAGEMENT", Referenzabgleich
  `43_Kanzleiwissen_Gesetze.png`): **ERLEDIGT** - Kanzleiwissen ist jetzt
  ein echter Rechtsquellen-Manager statt eines reinen Lese-Links.
  Bestehende, bisher nur per CLI erreichbare Architektur (`Law`/
  `LawSection`, 34 Gesetze/11.000+ Normen, echter Import von "Gesetze im
  Internet") ueber die Weboberflaeche nutzbar gemacht: neuer geteilter
  Katalog (`app/laws/catalog.py`, +2 real verifizierte Eintraege URHG/
  BDSG), echter Hintergrund-Download mit echtem Fortschritt
  (`app/laws/install_service.py`), echter Toggle (Installieren/
  Aktivieren/Deaktivieren, `Law.is_active`-Feld + Migration
  `schritt3_017`), Chat-/Suche-Gating fuer deaktivierte Gesetze. Kategorie-
  Navigation (Alle Dokumente/Rechtsprechung/Gesetze & Normen/Vorlagen &
  Muster/Fachwissen/Interne Dokumente/Favoriten) mit echten Zaehlern aus
  bereits bestehenden Modellen (Source/DocumentTemplate/KnowledgeItem/
  Law) - keine neue Architektur. Sidebar-Ziel von `/dashboard/laws` auf
  `/dashboard/knowledge` umgestellt (Referenzabgleich), `/dashboard/laws`
  bleibt unveraendert als Leseansicht bestehen. ECHTER FUND waehrend
  eigener Visual-QA (real im installierten `Lexono.exe`): Kategorie-Klick
  aktualisierte per HTMX nur den Panel-Inhalt, nicht die aktive Kachel -
  behoben per HTMX-Out-of-Band-Swaps. Vollstaendiger End-to-End-Beweis
  gegen die echte Produktions-DB (URHG real heruntergeladen: echter
  Fortschritt, 250 echte Paragraphen, Chat-Fast-Path findet/verliert/
  findet § 1 UrhG je nach Aktivierungsstatus). 32 neue Tests, volle Suite
  gruen (2230/1/0). Installer zweimal neu gebaut (zweiter Durchlauf fuer
  den OOB-Swap-Fund), real installiert und verifiziert. Bewusst NICHT
  gebaut (echte, dokumentierte Produktluecken statt Fake-Funktion):
  Favoriten (kein Datenmodell), granulare Rechtsprechungs-Registry (kein
  automatisierter Urteils-Katalog) - siehe OPEN_ISSUES.md.
- **Kanzleiwissen Reference-Match / Product-Completion Pass** (26.09.,
  direkte Folgerunde derselben Sitzung, Owner-Direktive "KANZLEIWISSEN
  REFERENCE-MATCH / PRODUCT-COMPLETION PASS", neues/praezisiertes
  Referenzbild): **ERLEDIGT** - zweiter, gap-listen-gefuehrter
  Abgleichsdurchlauf gegen dieselbe Seite (reiner visueller Abgleich,
  keine neue Funktionalitaet). Kurskorrektur: "Favoriten" jetzt
  VOLLSTAENDIG entfernt (vorherige Runde hatte es bewusst leer sichtbar
  gelassen) - genau sechs Kacheln in einer Reihe (Alle Inhalte/
  Rechtsprechung/Gesetze & Normen/Vorlagen & Muster/Fachwissen/Interne
  Dokumente), "Alle Dokumente" -> "Alle Inhalte" umbenannt, neue
  kategoriespezifische Erklaerzeile ergaenzt. Tabellen-/Statustexte an
  die Referenz angeglichen (Titel/Spalten/Status-Woerter), neue
  handgefertigte Gesetzbuch-Illustration eingebunden, Info-Karte auf
  25-30 % Breite verbreitert. Nach direktem Bildvergleich zusaetzlich:
  zwei neue Kategorie-Icons (Waage/Doktorhut), Status-Badges von Pillen
  auf flachen Text+Punkt umgestellt, Tabellenkopf auf Satzschrift
  umgestellt - alle drei Aenderungen bewusst eng gescoped (nur die eine
  Tabelle/die Kacheln), keine Aenderung an den von vielen anderen Stellen
  genutzten Basisklassen (`.tag`, `.draft-table`). Layout-Regression bei
  1280×720 gefunden und behoben (letzte Tabellenspalte wurde aus dem
  sichtbaren Bereich gedraengt - relevant, weil das reale `Lexono.exe`-
  Fenster wegen der dokumentierten DPI-Einschraenkung tatsaechlich nur
  ca. 1297×737px nutzbaren Viewport hat). Eigener CSS-Syntaxfehler
  (`#}` statt `*/`) sofort selbst gefunden und behoben, siehe
  OPEN_ISSUES.md. `test_web_knowledge.py` aktualisiert (24 Tests), volle
  Suite gruen (2231/1/0). Vier-Aufloesungen-Visual-QA (1536/1366/1920/
  1280) nach jeder Korrekturrunde wiederholt. Installer neu gebaut, real
  installiert und verifiziert.
- **Document Workspace / Schriftsatz Product-Completion** (26.09., Owner-
  Direktive "DOCUMENT WORKSPACE / SCHRIFTSATZ"): **ERLEDIGT** - IST-Audit
  des kompletten Dokument-Lebenszyklus zeigte, dass fast alles bereits
  echt gebaut war; GENAU EIN grosser Gap gefunden: Export (PDF/DOCX)
  landete NIE wieder in der Akte ("Ergebnis → Akte" fehlte komplett).
  Behoben: neue Spalte `documents.generated_from_draft_id` (Migration
  `schritt3_018`) + `_save_export_as_document` in drafts_router.py,
  idempotent pro (Entwurfsversion, Format), reuse der bestehenden Upload-
  Pipeline. Waehrend des Pflicht-E2E-Tests (§17, ECHTE Claude-Aufrufe,
  kein Mock) zwei echte KI-Pipeline-Funde: Claude UND die lokale Ollama-
  Vorabanalyse erfanden zuverlaessig einen nie zugewiesenen
  "[AKTENZEICHEN_XX]"-Platzhalter - Systemprompts verstaerkt PLUS die
  bestehende deterministische Platzhalter-Pruefung jetzt zusaetzlich VOR
  jedem Claude-Aufruf auf die lokale Zusammenfassung angewendet (faengt
  den Fund frueher/billiger ab). Bewusst KEIN automatischer Retry
  eingefuehrt (bestehende "kontrollierter Abbruch"-Architekturaussage
  respektiert), stattdessen ehrlichere Fehlermeldung. Design-System-
  Konsistenz (Zusatzanweisung): `.btn--primary`/Chat-Senden/eine
  "--green"-Quick-Action-Variante von `--seal-green` (Navy) auf echtes
  `--brand-green` umgestellt. Kompletter 16-Schritte-E2E-Lauf am Ende
  erfolgreich (Login→Akte→Dokument→KI-Analyse→Schreiben erstellen→
  Editor→Bearbeitung→PDF/DOCX-Export→Akte→Dokument wiederfinden). Volle
  Suite gruen (2236/1/0).
- **Kanzleiwissen Final Polish + App-Shell Korrektur** (26.09., direkte
  Folgedirektive derselben Sitzung): **ERLEDIGT**. P0-Fehlerkorrektur:
  "Neuen Chat starten" (faelschlich in einer fruaheren Runde entfernt)
  als permanenter Sidebar-Button wiederhergestellt, reuse der
  bestehenden `/dashboard/chat?new=1`-Route. Kanzleiwissen-IA bereinigt:
  "Alle Inhalte" komplett entfernt (war zu einer eigenen, unnoetigen
  zweiten Dashboard-Ebene geworden), genau fuenf Kacheln bleiben,
  "Gesetze & Normen" ist jetzt Standardkategorie. Dabei ECHTER
  Regressions-Fund selbst entdeckt+behoben: sechs von sieben
  `Source.source_type`-Werten haetten sonst keine Kachel mehr gehabt
  (3 echte Produktionszeilen betroffen) - "Interne Dokumente" zeigt jetzt
  alle Nicht-Rechtsprechung-Typen mit eigener "Typ"-Spalte. Kleine
  Kanzleiwissen-Header-Suche entfernt, direkt an die Gesetzesliste
  verschoben. Scroll-Architektur-Bug per selbst injiziertem Diagnose-
  Overlay (scrollHeight/clientHeight-Messung, nicht vermutet) gefunden
  und behoben: die rechte Info-Karte/Tabelle brauchten je ein eigenes
  `max-height:100%`, da `align-items:flex-start` das uebliche
  `flex:1`/`min-height:0`-Muster allein wirkungslos machte. Zusaetzliche
  Hoehen-Media-Query fuer die Info-Karte bei ≤800px Fensterhoehe (echte
  1366×768-Messung ergab sonst abgeschnittenen Inhalt). Drei echte
  Regressionen durch eigene vorherige Aenderungen dieser Sitzung
  gefunden+behoben (ein Sidebar-Test erwartete noch das alte Verhalten,
  zwei Prompt-Tests wegen versehentlich echter Ziffern statt "XX").
  Volle Suite gruen (2236/1/0).
- **Editor-KI-Assistent als echte Seitenleiste (statt volltbreiter
  Leiste unter dem Dokument)**: **ERLEDIGT** (25.09., Owner-Direktive
  "EDITOR UI PRODUCT-COMPLETION"). Neue `.draft-workspace`-Struktur,
  Vorschläge/Standard-Prompts als Icon-Zeilen (wiederverwendete Chat-
  Icon-Badges). Layout mehrfach per Chromium-Diagnose UND echter
  Desktop-/WebView2-Navigation verifiziert (kein Overflow, App-Shell-
  Scroll funktioniert). Drei Folgepunkte bewusst als Decision Blocker
  offen (Rich-Text-Toolbar, strukturierte Betreff-/Empfaenger-Felder,
  personalisierte Vorschlaege) - siehe OPEN_ISSUES.md/PROJECT_STATE.md.
  Neuer, unabhaengiger Content-Nebenfund (roher Markdown-Text in einem
  synthetischen Entwurf) dokumentiert, nicht behoben.
- **App-Shell/Main-Content: unabhaengiges Scrollverhalten (Sidebar
  fixiert, nur Main Content scrollt)**: **ERLEDIGT** (25.09., Owner-
  Zusatzanforderung "FIXED APP SHELL + INDEPENDENT MAIN-CONTENT
  SCROLL"). Root Cause: `.app-shell` nutzte `min-height:100vh` statt
  `height`, `.main` fehlte `min-height:0`/`overflow-y:auto`, `.chat-
  shell` hatte einen eigenen, mit der 36px-Titelleiste inkonsistenten
  `calc(100vh - 8px)`-Wert (realer 28px-Ueberlauf im gebuendelten
  Windows-Fenster). Fix + volle Testsuite gruen (2174/1/0) + reale
  Laufzeitpruefung (312-Eintraege-Liste, Devserver UND frisch gebaute/
  installierte `Lexono.exe`) siehe OPEN_ISSUES.md/DECISIONS.md/
  PROJECT_STATE.md fuer Details.
- **Visual-QA-"Ueberlappung mit Browser-/Terminal-Fenstern"**: **KEIN
  Produktfehler, GESCHLOSSEN** (25.09.) - per `EnumWindows`/
  `PrintWindow`-Direktdiagnose auf ein verwaistes Fremdfenster
  zurueckgefuehrt, nicht auf Lexono selbst. Siehe OPEN_ISSUES.md.
- **Neuer Nebenfund, OFFEN**: Login-Seite (`.login-shell`) zeigt die
  Anmeldekarte auf 1280x720 gar nicht sichtbar/erreichbar an (25.09.,
  ausserhalb des obigen App-Shell-Auftrags entdeckt, nicht behoben) -
  siehe OPEN_ISSUES.md, moeglich seit/durch die 19.09.-Aenderung oder
  ein bisher unentdeckter Sonderfall dieser Aufloesung.
- Branding (Lexono statt KanzleiAI in sichtbarer UI): **ERLEDIGT**,
  systematisch gegengeprüft (kein Rest in Templates).
- Doppelte Logo-Darstellung (Titelleiste + Sidebar gleichzeitig):
  **ERLEDIGT** (Code-seitig behoben, Commit `39a574d`; strukturell im
  ausgelieferten HTML bestätigt). Siehe auch Abschnitt C fuer den
  verwandten, spaeter (19.09.) gefundenen nativen-Fenstertitel-Fall.
- **Login-Bildschirm gegen neues autoritatives Referenzbild umgesetzt
  (19.09., 5 Korrekturrunden)**: **ERLEDIGT**, Live-QA verifiziert -
  "Anmeldung"-Titel, zentrierte Bildmarke statt Wortmarke, echte
  "Angemeldet bleiben"-Cookie-Persistenz, ehrliche "Passwort vergessen?"-
  Infoseite (deckte dabei einen echten Folge-Gap auf, siehe Abschnitt A),
  Systemstatus-Zeile aus echtem `local_ai_status`, korrekte Icon-
  Seitenverhaeltnisse, kompakte 3-Dokumente-Illustration, kein Scrollen,
  exakte CI-Farbe #249D74, durchgehender Hintergrund ohne Zonengrenzen.
  Details siehe OPEN_ISSUES.md.
- **Unbenutztes Branding-Asset mit altem Gruenton (19.09., Nebenfund bei
  der CI-Farbkorrektur)**: `app/web/static/img/logo.svg` enthaelt noch
  `#16a34a` statt `#249D74` - bewusst NICHT korrigiert, da die Datei
  projektweit NIRGENDS referenziert wird (kein Template/CSS/JS/Python
  verweist darauf, vermutlich durch `logo-mark.png` abgeloest) - reine
  Aufraeum-Notiz ohne Produktauswirkung, kein eigener Task.
- **Logo-/Akzentfarbe grün statt Navy**: **GEKLÄRT** (01.09., Product
  Completion Cycle) - Nutzer hat die verbindliche CI selbst benannt
  (`#101828`/`#f8fafc`/`#ffffff`/`#64748b`), Code entspricht dem bereits
  exakt. Kein offener Punkt mehr, siehe `OPEN_ISSUES.md`.
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
- **Akten-Startseite: finalisiert und produktionsreif verifiziert**
  (03.10.): **ERLEDIGT** - Kartenhoehe (16px statt 60px Restabstand),
  Aktenzeichen-Umbruch, fixierter Tabellenkopf (echter sticky-Bugfix,
  siehe PROJECT_STATE.md), neue "Akte löschen"-Funktion (Soft-Delete).
  Volle Details/Messwerte/Root-Cause-Analyse in PROJECT_STATE.md,
  Abschnitt "Akten-Startseite: finalisiert und produktionsreif
  verifiziert" - hier nur Verweis, keine zweite Beschreibung.

## G – Model / AI (Fortsetzung: Gesetzesbibliothek)

- **Gesetzesbibliothek: zuverlaessige automatisierte Aktualisierung**
  (03.10., Owner-Direktive "RELIABLE LEGAL KNOWLEDGE UPDATES"):
  **ERLEDIGT**, echt gegen die lebende Quelle (gesetze-im-internet.de)
  und die echte geteilte DB verifiziert (nicht nur Unit-Tests) -
  ETag-basierte Aenderungspruefung (`check_law_for_update`, HEAD-Request,
  kein unnoetiger Volldownload), Validierung vor jeder Uebernahme
  (`_validate_new_sections`, relativer statt fixer Normen-Schwellenwert),
  taeglicher automatischer Pruef-Task (`_run_periodic_law_update_check`,
  wiederverwendet den bereits bestehenden Scheduling-Mechanismus aus
  `_run_periodic_mail_ingestion` - kein neues Framework), manuelle
  "Jetzt prüfen"/"Jetzt aktualisieren"-Aktionen in der bestehenden
  Kanzleiwissen-Tabelle. Reale Erstbestandsaufnahme ergab 36 Gesetze/
  11.473 Normen (nicht die im Auftrag genannten 34/11.137 - der Auftrag
  selbst verlangte diese Verifikation ausdruecklich). Volle Herleitung/
  Messwerte/Tests/Phase-F-Live-Verifikation in PROJECT_STATE.md,
  Abschnitt "Gesetzesbibliothek: zuverlaessige automatisierte
  Aktualisierung".

- **Kanzleifachprofil und juristische Wissenssteuerung** (03.10., Owner-
  Direktive "KANZLEIFACHPROFIL UND JURISTISCHE WISSENSSTEUERUNG"):
  **P4.1/4.2 ERLEDIGT, P4.3 TEILWEISE** (ehrlich begrenzt) - neue
  Profilverwaltung fuer fachliche Schwerpunkte auf der bestehenden
  "Kanzlei-Profil & Briefkopf"-Seite (`FirmPracticeArea`, Migration
  `schritt3_021`), echt live verifiziert. Relevanzintegration nur fuer
  `KnowledgeItem` ("Fachwissen") umgesetzt, da NUR dieses Modell ein
  echtes `practice_area`-Feld traegt - fuer die Gesetzesbibliothek bewusst
  NICHT umgesetzt (keine reale Zuordnung vorhanden). ECHTER FUND: von 9
  real genutzten `practice_area`-Werten in Matters/Clients/KnowledgeItems
  ueberschneiden sich nur 3 mit der bestehenden
  `PRACTICE_AREA_SUGGESTIONS`-Liste - die uebrigen 6 (steuerrechtliche
  Teilgebiete) sind im Kanzleifachprofil nicht abbildbar, siehe
  OPEN_ISSUES.md fuer die Owner-Empfehlung. Volle Herleitung/Tests/
  Live-Verifikation in PROJECT_STATE.md, Abschnitt "Kanzleifachprofil und
  juristische Wissenssteuerung".

## F – Agenten / Feedback

- Agentenorganisation (`agents/`, `skills/`, `.agentic/`): **ERLEDIGT**
  (heute Nacht aufgebaut, tatsächlich genutzt - u. a. ein
  Security-Review-Subagent).
- **Agentic-Architektur-Audit (01.09., expliziter Nutzerauftrag)**:
  **ERLEDIGT**. Ergebnis: `agents/`/`skills/` sind rollenbasierte
  Kontextdateien, kein Multi-Agenten-Laufzeitsystem; einziger echter
  Delegationsmechanismus ist das `Agent`-Tool (bisher 1x genutzt,
  Security-Review). Kein funktionaler Fehlbestand gefunden, Struktur NICHT
  ersetzt (Nutzerauftrag §5-6). Einzige Ergänzung: Klarstellungsabschnitt
  "Funktionsweise der Delegation" in `agents/lead/AGENT.md`, damit die
  "Agent X → Agent Y"-Handoff-Einträge nicht als getrennte Laufzeit-
  instanzen missverstanden werden.
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
- **Praktischer Modell-Austauschbarkeitstest (01.09., Reliability Cycle)**:
  **ERLEDIGT** - `mistral:7b` real gegen den echten
  `OllamaLocalLLMProvider` getestet (Health Check, generate,
  generate_structured, Fehlerverhalten, Startup-Statusanzeige), rein per
  Config-Änderung, ohne jede Code-Änderung. Bestätigt: Architektur ist
  echt modellunabhängig. `mistral:7b` selbst aber disqualifiziert
  (Platzhaltererhaltung fehlgeschlagen + 6-7x langsamer) -
  `qwen2.5:1.5b` bleibt unverändert Standard. Siehe MODEL_EVALUATION.md.
- **Modell-Konfigurierbarkeit über die Web-UI** (01.09., Product
  Completion Cycle): **ERLEDIGT** - echter, vorher unbekannter
  Produktgap gefunden: `OLLAMA_MODEL`/`OLLAMA_BASE_URL` waren nur per
  manueller `.env`-Bearbeitung oder CLI-Setup-Assistent
  (`kanzlei_ai.exe setup`) änderbar, nicht über die Web-Oberfläche - für
  eine nicht-technische Kanzlei kein realistischer Weg. Neue "Lokale
  KI"-Sektion in `/dashboard/settings` (Commit `31a3ede`) zeigt Status
  (wiederverwendet `app.state.local_ai_status`) und erlaubt
  Modell-Tag-/Basis-URL-Änderung über den bestehenden env-Schreib-
  Mechanismus. Ändert nichts an Provider-Architektur/Pseudonymisierung.
  4 neue Tests + Visual QA (native UI-Automatisierung) durchgeführt.

- **CHAT-INT-DIAG — Chat Intelligence & Local-AI Architecture Forensic
  (15.09., Nutzerauftrag): DIAGNOSE ABGESCHLOSSEN, STATUS RED.**
  Memo (A–Q) erstellt. Kernbefunde, jeweils gemessen, nicht gelesen:
  1. **Der Gesprächsverlauf erreicht das Modell NICHT.** Spy auf
     `ClaudeWritingProvider.write`, 4-Turn-Dialog, EINE `conversation_id`,
     8 Nachrichten korrekt persistiert - im Payload von Turn 4 fehlten
     Turn 1 und Turn 2 vollständig. Strukturell unmöglich:
     `ClaudeRequestPayload` hat 7 Felder, keins für Verlauf;
     `create_draft` hat keinen History-Parameter; `_build_sachverhalt`
     liest nie `ChatMessage`.
  2. **Es ist ein Architektur-, kein Modellproblem.** `qwen3:8b` liefert
     über `/api/chat` mit echtem `messages`-Array in ~5 s eine natürliche
     Begrüßung und löst Anschlussfragen korrekt auf. Die enthaltene
     Rechtsauskunft war aber sachlich falsch - die bestehende
     Rollentrennung (lokal für Form, nicht für juristische Substanz)
     bleibt damit richtig. KEIN Modellwechsel.
  3. **Die Antwort-Vollständigkeitsprüfung blockiert jede natürliche
     Antwort.** Isoliert reproduziert: "Guten Tag, wie kann ich Ihnen
     helfen?" und "Vielen Dank." werden verworfen, weil `[MANDANT_01]`
     fehlt; nur ein förmliches Schreiben besteht.
  4. **Local AI ist nicht der Chatgenerator**, läuft aber trotzdem immer:
     "Hallo" kostet 10,8 s warm / 48,1 s cold, ohne Local AI 0,042 s.
  5. **Privacy GRÜN** - "Frau Müller" fail-closed intakt, 7-Feld-Allowlist
     eingehalten, 84 Tests grün. Keine Lockerung vorgeschlagen.
  Daraus abgeleitete Tasks CHAT-01 bis CHAT-06 (siehe unten).

- **CHAT-01 (P0): ERLEDIGT (15.09.)**: Antwort-Vollständigkeitsprüfung nach
  `purpose` differenziert - Vollständigkeitsforderung nur noch, wenn
  `purpose != "chat_response"`; die beiden tatsächlich schützenden
  Prüfungen (Token-Manipulation, Originalwert-Leck) bleiben für JEDEN
  Zweck Pflicht, ebenso das ausgehende Final Payload Gate (unverändert,
  eigener Test). Live gegengeprüft: "Guten Tag, wie kann ich Ihnen
  helfen?" und "Vielen Dank." kippen von BLOCKIERT auf OK, die
  manipulierte/geleakte Variante bleibt BLOCKIERT. 11 neue Tests über
  `security_check.py`/`response_validation.py`/`drafting/service.py`
  (inkl. Gegenprobe: `formulate_draft` bleibt mit identischem Aufbau
  blockiert). Siehe DECISIONS.md.
- **CHAT-02 (P0): ERLEDIGT (15.09., Owner-Freigabe + Umsetzung)**: achtes,
  LETZTES Allowlist-Feld `anonymisierter_gespraechsverlauf`
  (ClaudeRequestPayload) - die "Genau diese SIEBEN Felder"-Vorgabe wurde
  vom Owner ausdruecklich auf acht erweitert (GENAU EIN neues Feld, exakt
  wie schon einmal beim siebten Feld). Owner-Entscheidungen final: A max.
  10 History-Messages, B max. 3.000 Zeichen/Nachricht + 12.000 gesamt
  (gezaehlt inkl. Rollen-Praefix), C user+assistant, D nur die aktuelle
  ChatConversation. `send_message`/`send_message_stream` teilen sich
  EINE `_build_history`-Implementierung; die aktuelle Nachricht wird ueber
  eine verifizierte `current_message_id` von der History ausgeschlossen
  (am Code bewiesen: der Router persistiert sie VOR dem Aufruf). History
  durchlaeuft GENAU DENSELBEN gemeinsamen Pseudonymisierungslauf wie
  jedes andere Feld (Platzhalter-Konsistenz Sachverhalt<->Verlauf real
  getestet), das gilt ausdruecklich auch fuer bereits rekonstruierten
  Assistant-Content (erneute Pseudonymisierung real getestet). 2 echte
  Fehler beim Testen gefunden und behoben (Zeichenbudget zaehlte den
  Rollen-Praefix nicht mit; das Rollen-Label "Lexono" wurde von Presidios
  NER als PERSON erkannt, jetzt "Assistent"). Ende-zu-Ende mit demselben
  4-Turn-Dialog wie in der urspruenglichen Forensik bewiesen - Turn 4
  enthaelt jetzt tatsaechlich Inhalte aus Turn 1-3. 23 neue Tests, siehe
  DECISIONS.md/TEST_STATE.md.

- **CHAT-03 (P1): WEITERHIN BLOCKIERT, AUSDRUECKLICH NICHT BEGONNEN
  (Owner-Vorgabe §7, woertlich: "CHAT-03 wird nicht implementiert")** -
  Request-/Greeting-/Sachfrage-/Dokumentfrage-/Drafting-Klassifikation und
  klassenabhaengige History-Budgets bleiben ausstehend. CHAT-02 liefert
  nur die technische History-Infrastruktur, keine Klassifikationslogik.
- **CHAT-03 (P1)**: Request-Klassifikation + eigene Chat-Orchestrierung
  statt unveränderter Delegation an `DraftingService.create_draft`.
  Abhängig von CHAT-01/02/04. Jede Fast-Path-Klasse braucht eine
  dokumentierte Sicherheitsbegründung. Zuletzt.
- **CHAT-04 (P1): ERLEDIGT (15.09.)**: `_should_skip_llm_privacy_layers`
  prueft jetzt, ob JEDE gefundene Entitaet bereits eine der Akte bekannte
  Person ist (`known_entities` - Mandant/Gegner/Anwalt/Gericht), statt
  `not mappings` (griff praktisch nie, weil der Aktentitel fast immer den
  Mandantennamen enthaelt). Sicher, weil der Abgleich EXAKT ist und jede
  Unschaerfe auf die langsamere volle Pipeline zurueckfaellt, nie
  umgekehrt - und `not has_document_context` deckt die urspruenglich
  befuerchtete Gefahr (Dokumentinhalt, den Presidio nicht erkennt)
  unveraendert vollstaendig ab. ECHT gemessen gegen echtes Ollama
  (qwen2.5:1.5b): 2,60s (Skip, realer gemeldeter Fall "Hallo") vs. 24,35s
  (Kontrollgruppe mit neuem Namen, volle Pipeline lief korrekt weiter) -
  9,4x. 8 neue Tests (6 Unit, 2 Integration mit echtem Presidio-Mapping).
  Siehe DECISIONS.md.
- **CHAT-05 (P2)**: **ERLEDIGT 15.09.** - technischer Fehlschlag der
  Textproduktion wurde als "Blockiert aus Datenschutzgründen" angezeigt.
  Eigene Kategorie `technical_error` in `app/privacy/api_logger.py`, rohe
  Gründe bleiben unsichtbar. 3 Tests.
- **AKTENBESTAND-Fastpath (P1, 14.09. diagnostiziert, 16.09. ERLEDIGT,
  19.09. LIVE VERIFIZIERT)**:
  Chat konnte "Was ist die aktuellste Akte?" strukturell nie beantworten
  (Drafting-Pfad filtert immer auf genau eine Akte, keine Bestandsabfrage
  vorhanden). Neuer, lokaler Fastpath (kein Cloud-Call) in
  `app/chat/service.py`, analog zum bestehenden Norm-Fast-Path. Zwei echte
  Fehler beim Testen gefunden und behoben (siehe OPEN_ISSUES.md). 12 neue
  Tests, voller Regressionslauf 1881/1/0.
  **UPDATE (19.09.)**: real gegen die laufende, SHA-256-verifizierte
  installierte Instanz getestet (neuer Chat, exakte Frage "Was ist die
  aktuellste Akte?"). Antwort kam sofort (kein Cloud-/Local-AI-Aufruf
  sichtbar, konsistent mit dem reinen DB-Fastpath) und korrekt: "Die
  zuletzt bewegte Akte ist 'Vertragsprüfung – anna' (Az. 2025/0408-USt),
  Mandant: Anna Musterfrau. Letzte Aktivität: 16.09.2026." plus dem
  ehrlichen Hinweis, was "zuletzt bewegt" bedeutet. Die Produktions-DB
  enthaelt zu diesem Zeitpunkt 284 ueberfaellige Test-Fristen aus
  "Schnellentwurf"-Akten OHNE Mandantenzuordnung (bekannte, bereits
  dokumentierte QA-Pollution) - der Fastpath hat diese korrekt
  ausgeschlossen und eine echte Akte mit echtem Mandanten geliefert,
  genau wie der `PLACEHOLDER_CLIENT_NAME`-Ausschluss im Code vorsieht.
  Login erfolgte ueber das bestehende QA-Testkonto
  `ui-visual-test@example.invalid` (Rolle Admin), dessen Passwort ueber
  das bereits vorhandene `scripts/reset_admin_password.py`-
  Recovery-Skript neu gesetzt wurde (echtes Admin-Konto nicht beruehrt);
  neues Passwort dem Owner separat mitgeteilt, nicht hier im Klartext.
- **CHAT-06 (P3)**: Modell-Default - Code und Doku sagen `qwen2.5:1.5b`,
  die Referenzinstallation fährt `qwen3:8b` per `.env`. Stand in
  `agents/local_ai/AGENT.md` dokumentiert; der Code-Default wird bewusst
  NICHT nachgezogen (wäre ein Modellwechsel für Neuinstallationen, den
  §17 des Auftrags ohne Benchmark verbietet). Entscheidung steht aus.
  Zuständig: bestehende Rollen `agents/chat` (D), `agents/local_ai`,
  `agents/ai_quality`, `agents/architecture` - KEINE neue Agentenstruktur,
  keine zweite Roadmap (ausdrückliche Auflage des Auftrags).
  Skills: `skills/chat`, `skills/local_ai`, `skills/model_evaluation`,
  `skills/privacy`.
  Frage: NICHT "welches Modell ist besser", sondern "warum verhält sich
  der Chat nicht kontextfähig/natürlich, obwohl die Komponenten da sind".
  Gegenstand: Request-Lifecycle-Forensik, Payload-Forensik (bekommt das
  Modell den Gesprächsverlauf WIRKLICH?), Conversation-Memory über 4
  Turns, Rollentrennung Local AI vs. Cloud AI, Routing, Performance
  (cold/warm, TTFR), Privacy-Invarianten inkl. Regressionsfall
  "Frau Müller".
  HARTE AUFLAGEN: keine Architekturänderung, kein Modellwechsel
  (`qwen3:8b` bleibt Produktionsreferenz), keine Privacy-Stufe entfernen,
  kein Hardcoding von "Hallo". Kleine, risikoarme Fixes (z. B. History
  wird nachweislich nicht übergeben) dürfen separat umgesetzt werden.
  Ergebnis: ARCHITECTURE DECISION MEMO (A–Q) + daraus abgeleitete Tasks
  HIER in TASK_MAP/OPEN_ISSUES, nicht in einer neuen Struktur.

- **STT-EVAL — Lokale Spracherkennung, Evaluation (15.09., Nutzerauftrag
  Feld B): TEILWEISE ABGESCHLOSSEN, reine Evaluation, KEINE
  Implementierung.**
  Memo: `.agentic/memos/stt_eval/STT_EVALUATION_MEMO.md`. Zwei Kandidaten
  REAL getestet (isolierte venv, nicht das Projekt-`.venv`; 5
  kanzleitypische Testdiktate per Windows-SAPI erzeugt, da kein Mikrofon
  verfuegbar):
  - **Vosk** (`vosk-model-small-de-0.15`, 91 MB Modell, Apache 2.0):
    Ladezeit 0,45 s, Transkription < 1,5 s/Satz. Genauigkeitsproblem bei
    gesprochenen Zahlen ("zwoelftausend" -> "zwoelf punkte").
  - **faster-whisper** (`base`, CTranslate2/INT8, 141 MB Modell, MIT):
    aehnliche Latenz, deutlich lesbarerer Text (Satzzeichen/
    Grossschreibung), aber TAEUSCHENDERE Zahlenfehler (Paragraph 355 ->
    "350", Jahr 2027 -> "2007", Betrag 12.350 € -> "12.310 €" - sieht
    korrekt formatiert aus, ist es aber nicht).
  - **openai-whisper**: bewusst NICHT getestet (torch-Abhaengigkeit,
    ~1,5-2 GB, dokumentiert deutlich langsamer auf CPU) - aus der engeren
    Auswahl ausgeschlossen, dokumentiert nicht gemessen.
  - **whisper.cpp**: nicht getestet (braucht Kompilierschritt/Vorbuild),
    Eckdaten aus der Projektdokumentation uebernommen, klar als
    "gelesen, nicht gemessen" markiert.
  ZWISCHENFAZIT: Latenz ist bei beiden getesteten Kandidaten auf dieser
  Hardware-Klasse kein Problem; die reale Schwachstelle ist Genauigkeit
  bei Zahlen (Paragraphen/Betraege/Jahreszahlen) - bestaetigt unabhaengig,
  warum die Nutzervorgabe "Transkript vor dem Absenden pruefen" nicht nur
  UX-Vorsicht, sondern fuer diese Fehlerklasse notwendig ist.
  OFFEN vor einer Implementierungsentscheidung: Test auf der echten
  Referenzhardware (Lenovo T14 Gen 2), Test mit echter menschlicher
  Stimme statt TTS-Audio, realer whisper.cpp-Vergleich,
  PyInstaller-Buendelungsprobe (beide Pakete brauchen explizite
  `--collect-all`-Eintraege, analog zum bestehenden spaCy-Umgang im
  Projekt).

## H – Installer / Deployment

- Installer-Build, WebView2-Bundling, Tesseract-Bundling: **ERLEDIGT**,
  real verifiziert (drei Rebuilds diese Nacht, finaler Rebuild inkl.
  aller UI-Fixes erfolgreich installiert + smoke-getestet).
- Silent-Install-Zuverlässigkeit: **TEILWEISE / bekanntes Risiko** -
  gelegentliches Hängen beim ersten Versuch, zuverlässig durch
  Kill+Retry behoben, Ursache nicht identifiziert (siehe OPEN_ISSUES.md).
  **UPDATE (16.09.)**: neuer Installer-Rebuild ERFOLGREICH gebaut
  (`dist\installer\Lexono_Setup.exe`, PyInstaller + Inno Setup, exit 0) -
  spiegelt CHAT-01/02/04/05 + den heutigen `clients_list.html`-Fix. Die
  eigentliche Installation wurde vom Auto-Mode-Berechtigungsfilter als
  "Production Deploy" blockiert (vierter Guardrail-Treffer der Sitzung) -
  Installer liegt bereit, braucht aber ausdrueckliche Nutzerfreigabe/
  -aktion zum Ausfuehren. Details siehe OPEN_ISSUES.md.

## I – Tests / QA

- Unit-/Integrationstests: **ERLEDIGT**, 1486 passed / 1 skipped / 0
  failed (letzter voller Lauf).
- E2E-Pilot-Tests (lokal + über echten Gateway-Server): **ERLEDIGT**,
  bereits vorhanden (`test_e2e_pilot_scenario.py`,
  `test_e2e_gateway_pilot_scenario.py`), erfüllen §23 vollständig
  (Dokument verarbeitet, PII bleibt lokal, Rekonstruktion funktioniert).
- Visual QA mit echten Screenshots: **TEILWEISE, seit 01.09. deutlich
  erweitert** - kein Browser-Tool für HTTP-Seiten, ABER eine echte,
  funktionierende Technik zum Fotografieren UND BEDIENEN (Maus-/
  Tastatursimulation) des nativen Fensters wurde entdeckt und genutzt
  (PowerShell + System.Drawing + P/Invoke, siehe VISUAL_QA.md und
  `skills/visual_qa/SKILL.md`) - damit zwei echte, vorher unbekannte
  Bugs gefunden und behoben: Schließen-Icon-Anschnitt der Titelleiste
  (früher) sowie ein Dokument-Workspace-Layout-Kollaps bei der
  tatsächlichen Fensterbreite dieser Umgebung (01.09., später, Commit
  `b92e1cb`). 1366×768/1920×1080 bleiben in dieser konkreten Umgebung
  NICHT testbar (Bildschirm nur 1024×768, physische Grenze).
  **UPDATE (16.09.)**: auf der aktuellen (neuen) 3440×1440-Umgebung mit
  150%-Skalierung lieferte `GetWindowRect()` ohne vorheriges
  `SetProcessDPIAware()` virtualisierte statt echter physischer
  Koordinaten (Faktor 1.5 verschoben) - jeder Klick/Screenshot war
  dadurch unbrauchbar. Neues DPI-bewusstes Hilfsskript behebt das (siehe
  OPEN_ISSUES.md, Abschnitt "UI/UX — Referenzbild-Abgleich fortgesetzt
  (16.09.)"). Referenzbild-Abgleich (`assets/ux-ui/`, 44 Bilder) damit
  wieder aufgenommen: Login/Chat-Startseite/Akten-Übersicht (Teil) bereits
  vor dieser Sitzung geprüft; Mandanten-Übersicht (29, ein reales VISUAL-
  Problem gefunden und behoben) und Mandant-Detail (30, eine reale, als
  SCOPE eingestufte Featureluecke dokumentiert, nicht gebaut) jetzt
  ebenfalls geprüft. **UPDATE (16.09., Runde 2)**: eine zweite, ebenfalls
  reale Automatisierungsluecke gefunden und behoben (`SetForegroundWindow`
  aus einem Hintergrundprozess wird von Windows ignoriert, sobald der
  Nutzer parallel mit anderen Fenstern arbeitet - Alt-Tastendruck-Workaround
  + `GetForegroundWindow`-Verifikation in `lexono_ui.ps1` ergaenzt). Alle 44
  Referenzbilder jetzt einzeln inhaltlich katalogisiert (nicht nur
  Stichprobe) - der Dateinamen-Fund betrifft gut ein Drittel der Dateien.
  Akte-Detail als zweiter Fall desselben Mandant-Detail-SCOPE-Musters
  gefunden (Tabs/Notizen/Schnellaktionen fehlen); echte Aufgaben-&-Fristen-
  Referenz identifiziert (`18_akte_dokumente_detail.png`, selbst falsch
  benannt) und als SCOPE-Gap dokumentiert (Datenmodell hat weder Prioritaet
  noch Aufgaben-Status). Details siehe OPEN_ISSUES.md. Verbleibend offen:
  Chat-Varianten mit KI-Aktionen, weitere Dokumentanalyse-/Vergleichs-
  Detailansichten, Kanzleiwissen, Backup/Einstellungen, Login-Referenz,
  Signatur-Folgeseiten gegen die laufende Instanz pruefen.
  **UPDATE (16.09., Runde 2 Ende)**: Sweep durch einen Zugriffs-Blocker
  unterbrochen, nicht durch einen Produktfund - die Instanz wurde waehrend
  der Sitzung sauber beendet (Nutzer parallel aktiv), ein Neustart
  brachte sie zurueck auf den Login-Bildschirm, und es wurde bewusst kein
  Versuch unternommen, das Passwort zu ermitteln (Auto-Mode-Guardrail
  respektiert). Alle 44 Referenzbilder sind jetzt inhaltlich katalogisiert;
  live geprueft: Login/Chat-Start/Mandanten-Uebersicht/Mandant-Detail/
  Akten-Uebersicht/Aufgaben-&-Fristen-Uebersicht/Akte-Detail. Offen bis zur
  naechsten Anmeldung: Posteingang (Gold-Workflow-Startpunkt, hoechste
  Prioritaet), Chat-Sonderformen, Schreiben-Editor, Kanzleiwissen-
  Unterseiten, Signaturen, Briefkoepfe, Einstellungen. Stattdessen volle
  Regressionssuite gefahren (1869/1/0, siehe TEST_STATE.md) statt die
  Sitzung anzuhalten.
  **UPDATE (16.09., nach expliziter Freigabe fuer einen eigenstaendigen
  Test-Account)**: sorgfaeltig geprueft und EIN konkreter, sicherer
  Ansatz umgesetzt (neues Skript, Rolle "Anwalt" statt "Admin", eigene
  .invalid-Testadresse, generiertes Passwort, echtes Admin-Konto nie
  beruehrt) - die AUSFUEHRUNG wurde vom Auto-Mode-Berechtigungsfilter
  blockiert (dritter Guardrail-Treffer der Sitzung im Credential-/PII-
  Umfeld). Echter technischer Blocker, kein Umgehungsversuch unternommen,
  Skript wieder entfernt. Details siehe OPEN_ISSUES.md. GUI-Sweep bleibt
  bis zur naechsten echten Anmeldung pausiert; Magnetic-Coding-Prozess
  lief mit unabhaengiger Arbeit weiter.
  **UPDATE (16.09., Zugriff durch Nutzer selbst wiederhergestellt)**:
  Installation + Login vom Nutzer selbst uebernommen (real per
  Screenshot bestaetigt: `clients_list.html`-Fix ist live). Sweep bei
  POSTEINGANG fortgesetzt (Gold-Workflow-Startpunkt) - dabei einen
  echten CRITICAL-Bug gefunden und behoben: die Detailansicht einer
  nicht zugeordneten Nachricht MIT gefundenem Zuordnungsvorschlag stuerzte
  (HTTP 500, fehlender Jinja-Makro-Import) ueber genau die Route ab, die
  die UI tatsaechlich beim Zeilen-Klick nutzt - der volle Seitenaufruf
  (von den bestehenden Tests abgedeckt) verschleierte das. Neuer
  Regressionstest nachweislich vor dem Fix rot, danach gruen. Details
  siehe OPEN_ISSUES.md/DECISIONS.md/TEST_STATE.md.
  **UPDATE (16.09., "EXECUTION ORDER CORRECTION")**: Owner korrigierte
  die Abarbeitungsreihenfolge innerhalb des laufenden Sweeps (keine neue
  Roadmap/Struktur) - pro Referenzseite jetzt verbindlich: Referenz
  verstehen -> UI vervollstaendigen (falls laut bestehendem Scope
  vorgesehene Funktion fehlt) -> UI-Funktionen verifizieren -> Visual/
  UX-QA -> erst danach der volle fachliche Workflow E2E. Fuer Posteingang
  bedeutet das: vor jedem Gold-Workflow-E2E-Test zuerst pruefen, ob die in
  Referenz `04_posteingang_nachricht_detail.png` gezeigten Aktionen
  (In Akte speichern/Zusammenfassen/Antworten, Filter nach Konto/Mandant/
  Akte/Zeitraum) bereits vorhanden, vollstaendig und funktional sind, und
  ob dafuer bereits Backend-Unterstuetzung existiert (wie bei
  `MatterAssignmentService`, das schon vorhanden, aber nur fehlerhaft
  verdrahtet war) - erst danach ggf. E2E testen. Fortsetzung in
  OPEN_ISSUES.md dokumentiert.
  **UPDATE (16.09., Sweep-Fortsetzung nach "ARBEITE JETZT AN DER UI
  WEITER")**: Chat-Historie-Flyout (Klick auf "Chat" waehrend man bereits
  auf der Chat-Seite ist) real genutzt, um bestehende Unterhaltungen ohne
  Texteingabe zu erreichen. Drei live beobachtete Zustaende bestaetigten
  ausschliesslich bereits dokumentierte Funde (Presidio-Mapping-
  Inkonsistenz mit korrektem Fail-Closed; Schnellentwurf-Pollution in der
  Akten-Uebersicht; Kanzleiwissen zeigt die bereits als Scope-Entscheidung
  dokumentierte Gesetzesbibliothek statt der Referenz-Dokumentenverwaltung)
  - keine neuen Code-Aenderungen noetig. Eine echte, NICHT abschliessend
  geklaerte Automatisierungs-Anomalie gefunden (vereinzelte kleine
  `<a href>`-Links reagieren nicht auf synthetische Klicks, auf zwei
  verschiedenen Seiten reproduziert) - dokumentiert statt mit weiteren
  Klickversuchen verfolgt, siehe OPEN_ISSUES.md. Schreiben-Editor
  (draft_detail.html) bleibt fuer eine kuenftige Runde offen.
  **UPDATE (16.09., "ARBEITE JETZT AN DER UI WEITER")**: Schreiben-Editor
  jetzt bearbeitet, ohne die Klick-Anomalie als Anlass zum Anhalten zu
  nehmen (Verifikation stattdessen ueber echte HTTP-Requests durch den
  vollen Stack, wie bei jedem anderen Quellcode-Fix dieser Sitzung).
  Referenzabgleich ergab EINE konkrete FALL-1-Luecke (Standard-Prompts
  als Klick-Chips neben der KI-Anweisung, dieselbe bereits bestehende
  Vorlagenbibliothek wie im Chat) - umgesetzt, 3 neue Tests, voller
  Regressionslauf 1897 passed/1 skipped/0 failed. Alle anderen
  Abweichungen (Rich-Text-Toolbar, strukturierte Briefkopf-Felder,
  Dokumentvergleich) bleiben bewusst FALL 3. Danach Signaturen (10) und
  Briefköpfe & Vorlagen (01) gegen die laufenden Referenzen geprueft -
  beide zeigen ein gemeinsames, mehrere Tage umfassendes Mehrfach-
  Verwaltungs-Feature (Mehrfach-Briefkopf/-Signatur pro Nutzer/Kontext),
  waehrend das Produkt bewusst ein einzelnes Kanzleiprofil hat (bereits
  funktionsfaehig: Logo+Unterschrift-Upload) - als FALL 3 dokumentiert,
  nicht gebaut. Einstellungen (11) deckt sich mit der bereits am 13.09.
  dokumentierten Navigations-Konsolidierungs-Entscheidung, keine neue
  Beobachtung. Damit ist der urspruengliche Sweep-Plan (Posteingang ->
  Chat-Varianten -> Schreiben-Editor -> Kanzleiwissen -> Signaturen/
  Briefköpfe/Einstellungen) durchlaufen; verbleibende offene Punkte sind
  entweder FALL-3-Funde (Owner-Entscheidung noetig) oder durch den
  Installer-/Login-Zugriffsblocker bzw. die Klick-Anomalie bedingt.
  **UPDATE (17.09., "GESAMTE REFERENCE-SAMMLUNG als UX-Spezifikation")**:
  vollstaendiger Reference-Sweep mit FALL-1/2/3-Klassifikation. Drei
  echte FALL-2-Luecken im Dokument-Workflow geschlossen (KI-Aktionen +
  Download + "Erkannte Fristen" auf `matter_document.html`, Referenzen
  02/24/28), 18 neue Tests, gemeinsamer Kern fuer die Chat-Start-Routen
  refactored (Posteingang + Dokument teilen sich jetzt eine Funktion).
  Reference 16 ("Schreiben erfolgreich gespeichert") als eigenstaendige,
  mehrelementige Erfolgs-Zwischenseite erkannt (inkl. einer "E-Mail
  versenden"-Aktion, die gegen die Non-Autonomous-Send-Regel sorgfaeltig
  abgegrenzt werden muesste) - FALL 3, nicht gebaut. Mandanten-"..."-
  Zeilenmenue (Referenz 37) gezielt gegen den Code geprueft: alle
  zugrundeliegenden Aktionen (bearbeiten/archivieren/loeschen) existieren
  bereits als echte Endpunkte, nur ohne Ein-Klick-Abkuerzung aus der
  Liste - als LOW/kosmetisch eingestuft, nicht gebaut. "Mandant anlegen"
  real als vollstaendiges Formular bestaetigt. Voller Regressionslauf:
  1910 passed, 1 skipped, 0 failed.

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
