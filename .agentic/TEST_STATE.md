# TEST_STATE – Regressions-Baseline

## Aktuelle Baseline (verbindlich, darf sich nicht verschlechtern)

```
2174 passed, 1 skipped, 0 failed  (voller Lauf via pytest, 24.09., "PRODUCT
COMPLETION MODE": +4 Tests fuer die neue "Erkannte Frist"-Vorschau in der
Posteingang-Zuordnungskarte, siehe DECISIONS.md)
```

Vorherige Baseline derselben Sitzung: 2170 (+2 Tests fuer die
"Vorschläge"-Schnellaktionszeile im Entwurf-Editor).

Vorherige Baseline derselben Sitzung (24.09., "HARD ROADMAP PRIORITY"):
2168 (+1 Test fuer den Presidio-NER-Fehlklassifikations-Fix
"Erbschaftsteuerbescheid faelschlich als PERSON erkannt").

Davor (24.09., "ROADMAP-ALIGNED PRODUCT
COMPLETION"): 2167 (neuer Erbschaftsteuer-Komplexfall [12 Tests] +
Session-Widerruf-Zeitstempel-Praezisionsfix [4 Tests]).

Erster voller Lauf dieser Sitzung (vor jeder Aenderung, reine Bestands-
pruefung) ergab 2154 - eine Differenz von +1 gegenueber der zuletzt
dokumentierten 20.09.-Baseline (2153), die nicht weiter aufgeschluesselt
wurde (kein Fehlschlag, keine Regression - vermutlich ein zwischen der
letzten Dokumentationsaktualisierung und jetzt bereits im unkommitteten
Bestand ergaenzter Test). +13 gegenueber dieser 2154-Zwischenbestands-
pruefung auf 2167 (12 neue Erbschaftsteuer-Tests + 4 neue Session-Timing-
Tests - 3 der vier Session-Tests ersetzen/ergaenzen keinen bestehenden
Test 1:1, Netto-Zuwachs 13 statt 16, da ein Test umbenannt/praezisiert
statt neu hinzugefuegt wurde). +1 auf die jetzige 2168 (NER-Fix).

+6 gegenueber der vorherigen Baseline (20.09., 2147): 4 neue Tests fuer
`DeadlineAnalysisService.analyze_message` (tests/test_deadlines_service.py)
+ 2 neue Integrationstests fuer die Verdrahtung in
`accept_matter_suggestion` (tests/test_web_inbox.py) - siehe eigenen
Abschnitt unten.

+6 gegenueber der Baseline davor (20.09., 2141): 3 neue Tests in
tests/test_web_drafts.py (Briefkopf-Vorschau leer/mit Kanzleiname,
Signatur-Block mit Unterzeichner-Name), 3 neue in tests/test_web_settings.py
(logo-file/signature-file jetzt fuer Nicht-Admins erreichbar, logo-file
weiterhin login-pflichtig) - siehe eigenen Abschnitt unten fuer den vollen
Fund-zu-Fix-Verlauf.

+94 gegenueber der letzten Baseline (19.09., 2047): siehe OPEN_ISSUES.md
für die einzelnen Funde dieser Runde (Owner-Direktiven "PRIORITAETS-
ERGAENZUNG: ECHTER DOKUMENTVIEWER" → "KI-WAITING-/BUFFERING-UX" →
"AUTONOMOUS CONTINUATION" → "WORKSTREAM A/B/C/D"), u. a.: echter
visueller Dokumentviewer (PDF+DOCX-Seiten-Rendering via PyMuPDF,
`app/documents/rendering.py`); projektweite KI-Waiting-UX
(`app_ai_loading.js`); CRITICAL-naher Pseudonymisierungs-Fund (voller
Name + blosser Nachname erhielten zwei verschiedene Platzhalter,
blockierte echte Antworten fälschlich, `app/privacy/pseudonymizer.py`);
Chat-/Dokument-Löschen (Soft-Delete für Dokumente inkl. Migration
`schritt3_015`, Hard-Delete für Chats); echter synthetischer
Mehrdokument-Fall (`generate_complex_case`, inkl. echter DOCX-
Schreibfähigkeit im Generator); Briefkopf/Signatur real mit echten
Assets verifiziert (Logo-Ueberlappungs-Fix + eine dokumentierte, noch
offene DOCX-Bild-Rendering-Limitation); ProcessingError blieb dauerhaft
auf "retrying" haengen (echter Zufallsfund beim GUI-Durchgang, zwei
Root-Causes in `app/errors/service.py` behoben). Alle Punkte: real
getestet, Regression grün, gebaut, installiert, an der echten
laufenden Instanz live verifiziert (siehe jeweiliger OPEN_ISSUES.md-
Eintrag für die Live-QA-Belege).

Zwischenstand seit der davor liegenden ausführlichen Historie unten
(17.09., 1917) nicht Zeile für Zeile nachgetragen (Produktarbeit hat
Vorrang, §24) - die einzelnen Funde/Test-Zuwächse zwischen 1917 und 2047
stehen jeweils vollständig in OPEN_ISSUES.md bei ihrem eigenen Eintrag
(u. a. AKTENBESTAND-Fastpath-Live-Verifikation, Login-Bildschirm-
Referenz-Umsetzung inkl. zweier Korrekturrunden, Admin-Passwort-Reset
für Nicht-Admin-Nutzer, mehrere Referenzbild-Funde vom 19.09.).

+7 gegenueber der letzten Baseline (17.09., Overnight-Direktive): (1) neue
Kategorie `original_value_leaked` in `api_logger.py::_BLOCK_CATEGORIES` +
Audit-Log-Kategorisierung fuer `DraftingService`s Antwortvalidierungs-Block
(2 Tests `test_privacy_api_logger.py` + 1 erweiterter Test
`test_drafting_service.py`) - echter, live in der installierten App
reproduzierter Fund (irrefuehrende "aus Datenschutzgruenden blockiert"-
Meldung fuer den "Zusammenfassen"-Button trotz unbedenklichem Inhalt), (2)
P1-Streaming-Status-Ereignisse fuer den nicht-streaming-faehigen Pfad (4
Tests `test_drafting_service_streaming.py` + 2 Tests `test_web_chat.py`,
davon einer eine echte VOR-dem-Fix-rot-Regression: die bestehende SSE-
Router-Annahme "jedes Nicht-delta-Ereignis ist done" haette bei einem
durchgereichten "status"-Ereignis einen `AssertionError` ausgeloest) -
siehe OPEN_ISSUES.md fuer beide Funde im Detail.

+6 (KI-Aktionen aus dem Aktendokument + neue Chat-Route) und +4 (Dokument-
Download) und +4 ("Erkannte Fristen" auf der Dokumentseite) und +1
(Download-Link im Chat-Dokument-Workspace) gegenueber der letzten
Baseline - vollstaendiger Reference-Sweep, drei FALL-2-Luecken in
`matter_document.html` geschlossen (KI-Aktionen, Download, Fristen-
Anzeige), siehe OPEN_ISSUES.md fuer die Einzelheiten.

+3 gegenueber der letzten Baseline: Standard-Prompts-Chips im Schreiben-
Editor (`draft_detail.html`), reine Wiederverwendung der bereits
bestehenden `PromptTemplateService`/Chat-Vorausfuell-Musters - siehe
OPEN_ISSUES.md fuer die volle FALL-1-Einordnung.

+12 gegenueber der letzten Baseline: Posteingang-Aktionen laut Referenz
`04_posteingang_nachricht_detail.png` nachgebaut ("EXECUTION ORDER
CORRECTION"/"CLARIFICATION"-Direktiven) - manueller Aktenzuordnungs-
Picker (5 Tests, reuse von `accept_matter_suggestion`) sowie
"Zusammenfassen"/"Antworten" ueber eine neue Chat-Einstiegsroute
`POST /dashboard/chat/from-message/{id}` (7+2 Tests, reuse von
`ChatService`/`DraftingService`) - siehe OPEN_ISSUES.md fuer die volle
Fall-1/2/3-Einordnung. Eigener Testfehler gefunden und korrigiert: ein
Test nahm faelschlich an, "Zusammenfassen" erzeuge keinen `Draft`-
Datensatz - tatsaechlich persistiert `DraftingService.create_draft`
IMMER einen Draft, unabhaengig vom `purpose`.

+1 gegenueber der Baseline davor: CRITICAL-Fund UI/UX-Sweep - Posteingang-
Detailansicht stuerzte real (HTTP 500) fuer jede nicht zugeordnete
Nachricht MIT gefundenem Zuordnungsvorschlag ab (fehlender `icons`-Import
in `partials/message_detail.html`, nur ueber die tatsaechlich von der UI
genutzte HTMX-Partial-Route sichtbar, nicht ueber den vollen Seitenaufruf
- siehe OPEN_ISSUES.md fuer die volle Herleitung). Neuer Test
(`tests/test_web_inbox.py::test_unmatched_message_with_match_shows_
suggestion_card_via_detail_partial`) nachweislich VOR dem Fix real
fehlgeschlagen (derselbe Traceback wie im echten Server-Log), NACH dem
Fix gruen.

+12 gegenueber der Baseline davor: Aktenbestand-Fastpath in
`app/chat/service.py` (P1-Fund vom 14.09., "Chat kennt den AKTENBESTAND
nicht", jetzt umgesetzt - siehe OPEN_ISSUES.md/DECISIONS.md). Zwei echte
Fehler beim Testen dieser neuen Funktion gefunden und behoben, BEVOR der
Code als fertig galt: (1) die Aktivitaets-Berechnung bezog anfangs
`Matter.updated_at` mit ein, was beim Insert immer auf die tatsaechliche
Wanduhrzeit gesetzt wird und dadurch zuverlaessig die zuletzt ANGELEGTE
(nicht die zuletzt bewegte) Akte gewinnen liess - entfernt, nur noch
Dokument-/Nachrichten-Aktivitaet mit `created_at`-Fallback fuer leere
Akten; (2) die Erkennungs-Regex akzeptierte nur ein optionales
Fragezeichen am Ende, keinen Punkt ("Zeig mir die letzte Akte." matchte
nicht) - erweitert auf `[\.\?!]?`.

Erneut voll bestaetigt am 16.09. (UI/UX-Referenzbild-Sweep, "CONTINUE
MAGNETIC CODING") nach der `clients_list.html`-Korrektur (redundante
"Mandanten"-Ueberschrift entfernt, siehe OPEN_ISSUES.md/DECISIONS.md) -
Zahl unveraendert zur letzten Baseline (keine neuen Tests noetig, die
bestehenden 24 `tests/test_web_clients.py`-Tests deckten die Aenderung
bereits ab). Laufzeit diesmal deutlich hoeher (380s vs. 140s) - keine
Regression, sondern Systemlast durch parallel laufende Anwendungen des
Nutzers auf demselben Rechner waehrend dieser Sitzung (siehe OPEN_ISSUES.md,
Automatisierungs-Zuverlaessigkeitsfund).

Letzter voller, bestätigter Lauf davor: 15.09., CHAT-02 (Owner-Freigabe +
Umsetzung, Agentic-Coding-Modus). Achtes, letztes Allowlist-Feld
nonymisierter_gespraechsverlauf - Owner-Entscheidungen final: A max.
10 History-Messages, B max. 3.000 Zeichen/Nachricht + 12.000 gesamt
(inkl. Rollen-Praefix), C user+assistant, D nur aktuelle ChatConversation.
send_message/send_message_stream teilen sich EINE
_build_history-Implementierung; aktuelle Nachricht wird ueber eine am
Code verifizierte current_message_id ausgeschlossen. History
durchlaeuft denselben gemeinsamen Pseudonymisierungslauf wie jedes andere
Feld - Platzhalter-Konsistenz Sachverhalt<->Verlauf UND erneute
Pseudonymisierung bereits rekonstruierten Assistant-Contents real
getestet. 2 echte Fehler beim Testen gefunden und behoben: Zeichenbudget
zaehlte den Rollen-Praefix nicht mit (12.032 statt budgetierter 12.000
Zeichen real gemessen); Rollen-Label "Lexono" wurde von Presidios
deutschem NER als PERSON-Entitaet erkannt (kein Datenschutzproblem,
aber unbrauchbare Kennzeichnung) - jetzt "Assistent". Ende-zu-Ende mit
demselben 4-Turn-Dialog wie in der urspruenglichen Chat-Intelligence-
Forensik bewiesen: Turn 4 enthaelt jetzt tatsaechlich Inhalte aus Turn
1-3 (vorher strukturell unmoeglich). +23 Tests in test_chat_service.py, 1
bestehender Test in test_privacy_gateway.py an die neue 6-Tupel-Signatur
angepasst, 1 bestehender Sicherheitsreview-Test bewusst von "sieben
Feldern/fuenf Kanaelen" auf "acht Felder/sechs Kanaele" fortgeschrieben
(derselbe geschlossene-Menge-Beweis, keine Abschwaechung). CHAT-03 bleibt
ausdruecklich blockiert (Owner-Vorgabe). Siehe DECISIONS.md.

Vorheriger bestätigter Lauf: 15.09., CHAT-04 umgesetzt (nach
"weiter" ohne inhaltliche Antwort auf die CHAT-02-Fragen - CHAT-02 blieb
deshalb bewusst unangetastet, nur CHAT-04 wurde bearbeitet, da dafuer
keine offene Produktfrage mehr bestand). _should_skip_llm_privacy_layers
prueft jetzt "jede gefundene Entitaet bereits der Akte bekannt" statt
"keine Mappings" (griff praktisch nie, weil der Aktentitel fast immer den
Mandantennamen enthaelt). Sicher: exakter Abgleich, jede Unschaerfe faellt
auf die langsamere volle Pipeline zurueck. ECHT gegen echtes Ollama
gemessen (qwen2.5:1.5b): 2,60s (Skip, realer Fall "Hallo") vs. 24,35s
(Kontrollgruppe mit neuem Namen, volle Pipeline lief korrekt weiter) -
9,4x. +8 Tests (6 Unit, 2 Integration mit echtem Presidio-Mapping).
(Hinweis: zwei pytest-Laeufe wurden zwischenzeitlich wegen Speicherdruck
abgebrochen - beim zweiten Mal identifiziert: llama-server, von der
eigenen Ollama-Messung mit 30-Minuten-Keep-Alive geladen, 1,2 GB. Sauber
per keep_alive:0 freigegeben, danach lief der Lauf sauber durch - keine
Testursache.)

Vorheriger bestätigter Lauf: 15.09., Dokumentengenerator-Demodaten.
document_templates war in der installierten Instanz VOLLSTAENDIG LEER
(0 Zeilen) - das echte, KI-freie Feature liess sich damit nicht
vorfuehren/testen. generate_shared_document_templates() (ueber den
bestehenden --with-knowledge-base-Schalter, idempotent) legt zwei
Mustertexte an. Echt verifiziert: gegen eine frisch geseedete Kopie
gerendert, beide Vorlagen erscheinen in der Auswahl mit echten Akten. +4
Tests, inkl. Beweis ueber die bestehende generate_from_template-Logik,
dass KEIN Platzhalter unaufgeloest bleibt (bei konfiguriertem
Kanzlei-Profil). Siehe DECISIONS.md.
(Hinweis: der vorherige Lauf wurde einmal wegen kurzzeitigem Speicherdruck
auf der Maschine abgebrochen - keine Testursache, beim Wiederholen mit
freiem Speicher lief er sauber durch.)

Vorheriger bestätigter Lauf: 15.09., Nachtrag "Chat Intelligence &
Local-AI Architecture Forensic" + STT-Privacy-Korrektur.
- Generator: include_draft-Opt-in fuer generate_case() (Fix fuer eine
  durch die neuen Entwurfs-/Postausgangs-Daten selbst verursachte
  Regression in test_end_to_end.py, Cross-Matter-Isolation).
- CHAT-05: technischer Fehlschlag wurde als Datenschutz-Blockierung
  angezeigt - eigene Kategorie in app/privacy/api_logger.py.
- Privacy-Korrektur: der Diktier-Button in der Entwurfs-Anweisungsleiste
  rief die echte (cloud-basierte) Browser-Spracherkennung auf, waehrend
  der Chat-Composer dieselbe Erkenntnis bereits hatte - jetzt konsistent
  deaktiviert, bis eine lokale STT-Loesung existiert.
- CHAT-01 (P0): die Antwort-Vollstaendigkeitspruefung blockierte JEDE
  natuerliche Chatantwort ("Hallo"). Live gegengeprueft: "Guten Tag, wie
  kann ich Ihnen helfen?"/"Vielen Dank." kippen von BLOCKIERT auf OK; die
  manipulierte/geleakte Variante bleibt BLOCKIERT; formulate_draft bleibt
  mit identischem Aufbau blockiert (Gegenprobe). Das ausgehende Final
  Payload Gate ist unveraendert.
- CHAT-04 GEPRUEFT, NICHT umgesetzt: die vorgeschlagene Lockerung wuerde
  eine bereits bewusst getroffene P0-Privacy-Entscheidung aufheben - siehe
  OPEN_ISSUES.md.
Siehe DECISIONS.md fuer alle Einzelbegruendungen.

Vorheriger bestätigter Lauf: 15.09., Generator-Qualitaet der
synthetischen Steuerkanzlei. Gemessen gegen eine KOPIE der installierten
Datenbank (Instanz unangetastet). BEFUND: der Generator ist NICHT die
Ursache der duerftigen Demo-Daten - er erzeugt echte Steuerfaelle; die
generischen Altdaten tragen keine DEMO-Nummer und werden vom Reset
korrekt nicht erfasst. Zwei echte Generator-Fehler gefunden: das
Aktenzeichen-Kuerzel war GEWUERFELT (Umsatzsteuer-Nachschau -> "BP",
Betriebspruefung -> "Sonst") und der Aktentitel endete auf
kleingeschriebenen Namensfragmenten ("- musterbau", "- julia"). Beides
behoben, +4 Tests. Eigener Fehler im ersten Anlauf offengelegt und
abgedeckt: "Handwerk Schmidt & Söhne" wurde faelschlich zu "Söhne".
WEITERHIN OFFEN: der Generator erzeugt keine Entwuerfe/Postausgangs-
Eintraege - die Gold-Workflow-Stationen am Ende haben in der Demo-Basis
also noch keinen realistischen Datensatz (siehe OPEN_ISSUES.md).

Vorheriger bestätigter Lauf: 15.09., Platzhalter-Mandanten-Fix.
Gefunden beim Rendern der Entwurfs-/Postausgangsseiten gegen eine KOPIE der
echten synthetischen Kanzlei-Datenbasis (nicht gegen Fixtures): die
installierte Instanz enthielt **28 identische Mandanten** "Ohne
Mandantenzuordnung" bei 40 Mandanten - create_quick_matter legte bei
jedem Entwurf ohne Aktenauswahl einen NEUEN an. In der
Entwicklungsdatenbank unabhaengig bestaetigt (7 von 7). Fix: EIN
gemeinsamer Sammel-Mandant; namentliche Mandanten werden bewusst NICHT
zusammengefuehrt. Aktenisolation gegengeprueft (kein KI-/Retrieval-Pfad
filtert nach client_id) und per Test abgesichert, dass zwei
Schnellentwuerfe weiterhin ZWEI getrennte Akten ergeben. +6 Tests.
Altbestand bewusst NICHT angetastet - dafuer
scripts/merge_placeholder_clients.py (Standard: nur anzeigen).

Vorheriger bestätigter Lauf: 15.09., UI-Block Gold-Workflow-Ende
(Entwurf -> Freigabe -> Postausgang). Drei echte Funde: (1) die
Entwurfsseite behauptete im Fliesstext, ein "tatsaechlicher Postausgang mit
Versandfunktion" existiere noch nicht - seit Prompt 25 FALSCH; (2) der
Postausgang wickelte die fertige Statuspille in eine ZWEITE Pille; (3) das
Pilot-Feedback benutzte das FRISTEN-Statusmakro, dessen Wertebereich nicht
passt - dadurch stand der rohe interne Wert "zur_pruefung" in der
Oberflaeche. Dazu: Akte auf der Entwurfsseite war eine rohe UUID ohne Link,
Entwurfs-/Postausgangsliste ohne Mandanten, Entwurfsliste ohne echten Link.
+7 Tests, 2 bestehende Tests PRAEZISIERT (nicht abgeschwaecht): einer hatte
den widerlegten Satz festgenagelt, einer den internen Wert "pending" als
sichtbar abgesichert. Siehe DECISIONS.md.

Vorheriger bestätigter Lauf: 14.09., Desktop-Blocker ("zweites,
scheinbar unsichtbares Element neben dem Lexono-Icon"). Diagnose ergab:
KEIN zweiter Shortcut, KEIN Installer-Fehler - eine verwaiste `app.log`
vom 12.09. auf dem Desktop, die wegen `HideFileExt=1` und fehlender
`.log`-Dateizuordnung als leere Kachel "app" erscheint. Ursache ihrer
Entstehung: `Start.vbs` leitete den Logpfad vom SKRIPTVERZEICHNIS ab;
jetzt vom Datenverzeichnis. +10 Tests (5 Aktendokument-Ansicht inkl.
Aktenisolations-404, 2 Start.vbs-Logpfad inkl. Auswertungsreihenfolge, 1
"Installer legt genau EIN Desktop-Element an", 2 build.ps1
ISCC-Auffindung). Real verifiziert: Upgrade- UND Clean-Installation mit
manipulationsgesicherter Hashpruefung; Desktop-Symbolgitter vor/nach
Installation, Start und Beenden jeweils unveraendert.

Vorheriger bestätigter Lauf: 14.09., UI-Block "Aufgaben & Fristen":
die Fristenübersicht war strukturell IMMER leer (fragte nur `Task` ab, das
von keinem Code-Pfad je erzeugt wird - während 178 real erkannte
`Deadline`-Sätze nur in der Einzelakte sichtbar waren). Jetzt werden die
erkannten Fristen angezeigt, überfällige als TEXT markiert, Liste auf 50
begrenzt mit ehrlichem Gesamthinweis. Zusätzlich: Demo-Daten erzeugen
jetzt auch NICHT zugeordnete Eingangspost (Gold-Workflow-Startzustand) -
inkl. dabei selbst gefundener Reset-Regression (Waisen-Nachrichten). 8
neue Tests. Gold-Workflow-Segment real durchgespielt: Vorschlag → Übernehmen
→ Nachricht + Anhang in der Akte + AuditEvent, in der DB verifiziert.

Vorheriger bestätigter Lauf: 14.09., Posteingangs-Zuordnung real auf
der synthetischen Kanzlei-Datenbasis gemessen - zwei strukturelle
Matching-Fehler gefunden und behoben (Mandantenname wurde nie verglichen;
blosse E-Mail-Adresse galt als "Anzeigename"). Messbare Wirkung: 8/8
Demo-Nachrichten von `no_match` (0.30) auf `needs_review` (0.50). 3 neue
Tests in `test_matching_matcher.py`, 1 bestehender Test PRÄZISIERT (nicht
abgeschwächt: prüft die RFC-2606-Garantie jetzt je enthaltener Adresse
statt nur am Stringende). Siehe DECISIONS.md.

Vorheriger bestätigter Lauf: 14.09., Nachtrag "synthetische
Steuerfachanwaltskanzlei als Testbasis" - 25 neue Tests (Demo-Daten:
Kennzeichnung/Stammdaten/Reset/Idempotenz inkl. sicherheitskritischem
Nachweis, dass der Reset echte Mandanten nicht antastet; echte,
extrahierbare PDF-Dateien; steuerrechtliche Dokumenttypen inkl. der beiden
real gefundenen Fehlklassifikationen; Mandanten-Aktenzähler mit beiden
Randfällen). **Beobachtung**: in EINEM Zwischenlauf ein einmaliger,
danach zweimal nicht reproduzierbarer Flake in
`test_chat_service.py::test_send_message_traces_routing_and_downstream_steps_under_one_trace_id`
- als offener Punkt in OPEN_ISSUES.md festgehalten, nicht "auf Verdacht"
umgeschrieben.

Vorheriger bestätigter Lauf: 14.09., UI/UX-Audit-Fortsetzung
(Chat-Startseite real gegen `assets/ux-ui/05_chat_startseite.png`
angeglichen) - 1 bestehender Test (`test_chat_empty_state_quick_actions_
have_distinct_accent_colors`) aktualisiert, da er die durch die aktuelle
Referenz ueberholte "bewusst ohne Gruen"-Farbregel kodierte (kein
Netto-Testverlust, gleiche Anzahl Farb-Assertions). Gleicher Gesamtstand
(1737) wie im vorherigen Lauf, da nur bestehende Tests angepasst, keine
neuen hinzugefuegt.

Vorheriger bestaetigter Lauf: 14.09., Performance-Benchmark der
Kern-Workflows (Overnight-Direktive §12-13) deckte real auf, dass
"Elbchaussee 45" (Strassenname mit Suffix "chaussee") vom bisherigen
`_STREET_PATTERN`-Regex nicht erfasst wurde - 5 neue parametrisierte
Tests in `test_privacy_detectors.py` fuer weitere reale deutsche
Strassennamen-Suffixe (chaussee/damm/ufer/steig/promenade/wall/steg/
anger). Siehe DECISIONS.md fuer die vollstaendige Herleitung inkl. eines
zweiten, bewusst nicht behobenen NER-Konsistenz-Fundes und der
Login-UI-Ueberarbeitung.

Vorheriger bestaetigter Lauf: 14.09., Overnight-Direktive §8 - realer
Security-/Privacy-Regressionsfall "Frau Müller" root-caused (weder
known_entities noch Presidio/spaCy-NER noch die deterministische
Regel-Heuristik erkannten bisher einen blossen Nachnamen mit Anrede-/
Rollenwort ohne Vornamen, z. B. "Frau Müller"/"Herr Müller"/"Klägerin
Müller"/"Der Beklagte Müller") + teilweise geschlossen (`security_check.py::
_find_possible_unrecognized_names` + `local_ai_provider.py::
_build_known_entities`, siehe DECISIONS.md/OPEN_ISSUES.md für den vollen
Befund inkl. bewusst offen gelassener Restlücke bei komplett anredelosem
Nachnamen ohne Aktenzuordnung). Real E2E gegen die installierte Anwendung
verifiziert (Produktions-DB, echter HTTP-Flow, echter Block ohne
Cloud-Aufruf bei "Frau Müller", keine Fehlalarme bei harmlosen
Nachrichten). 24 neue Tests insgesamt (22 in
`test_privacy_security_check.py` - davon 15 parametrisiert für die volle
§9-Rollenwort-Matrix + 1 Mehrpersonen-Test -, 2 in
`test_ai_providers_local.py`, davon 1 während der Härtung selbst als
echte Regression gefunden: ein einzelner Buchstabe als "Nachname" hätte
per Substring-Suche jedes Vorkommen dieses Buchstabens im Text getroffen
- durch Mindestlänge 3 verhindert).

Vorheriger bestaetigter Lauf: 14.09., Nachtrag zum SGB-/Mail-Zyklus:
Mail-Anhänge werden jetzt real über `DocumentProcessingService`
extrahiert (vorher dauerhaft `extracted_text=None`, da
`MailIngestionService` diesen Schritt anders als Chat-/Schriftsatz-Upload
nie aufrief) - 1 neuer Test mit echtem Anhangsinhalt. Dabei zusätzlich
real gefunden (P2, nicht in diesem Lauf behoben): `ClassificationService`
wird projektweit von keinem der drei Upload-Pfade aufgerufen, wodurch
eine Nachricht/ein Dokument mit Anhang sich nie automatisch zuordnen
kann. Neuer Installer-Rebuild + Clean-Upgrade-Install real durchgeführt.

Vorheriger bestaetigter Lauf: 14.09., Sozialrecht-Kern (SGB I-XII)
importiert (12 Bücher, 3.115 Normen) + ein echter, root-cause-behobener
Parserfehler (SGB XII: zwei Anlagen mit identischem `enbez`, `parse_law_xml`
überspringt jetzt konsequent nicht-zitierfähige Struktur-Blöcke) +
Chat-Fast-Path-Erweiterung (`_normalize_law_code` für mehrteilige Kürzel
wie "SGB I"/"SGB 1", fehlende Verb-Phrase "was regelt" ergänzt). ZUSÄTZLICH:
alle 34 bisher importierten Gesetze (BGB, ZPO, AO, StGB, ... + SGB) erstmals
real in die PRODUKTIONS-Datenbank importiert (vorher nur Dev-DB) - 11.137
Normen, 0 Duplikate, real per installierter Anwendung verifiziert (5
Testfragen über den echten Chat-Endpunkt). 9 neue Tests (3
`_CODE_OVERRIDES`/`_KNOWN_TITLES`, 1 parametrisiert für
`_normalize_law_code`, 2 SGB-Fast-Path, 2 `parse_law_xml`-Struktur-Block-Fix,
1 Duplikat-Regressionstest).

Vorheriger bestaetigter Lauf: 14.09., Posteingang: automatische
Mail-Ingestion + Aktenzuordnung erstmals real mit der Anwendung verbunden
(vorher vollstaendig implementiert, aber nie aufgerufen - siehe
DECISIONS.md), neue "Automatische Zuordnung (Vorschlag)"-Karte +
"Übernehmen"-Endpunkt im Posteingang, sowie ein dabei gefundener und
behobener echter CSRF-Fund (`chat_router.py::link_matter` nutzte
`require_login` statt `require_role()`). 16 neue Tests (4 Mail-Ingestion-
Hintergrundtask, 4 `build_mail_provider`, 1 CSRF-Regression, 7
Vorschlagskarte/Übernehmen). Echter Installer-Rebuild + Clean-Upgrade-
Install + E2E-Smoke-Test gegen die reale Produktionsinstanz bestanden
(Login, Vorschlagskarte, CSRF-Ablehnung, Übernehmen, AuditEvent - alle
real verifiziert, Testdaten danach vollstaendig entfernt).

Vorheriger bestaetigter Lauf: 14.09., Gesetzesbibliothek massiv
erweitert (22 echte Gesetze, 8049 Normen statt vorher 1 Gesetz/2518 -
siehe DECISIONS.md). Zwei echte Fundamentalfehler beim Import gefunden
und behoben: `law_code`-Jahres-Suffix (AO/UStG/KStG) und Artikel- vs.
Paragraphen-Deep-Link-Schema (GG) - Chat-Fast-Path erkennt jetzt auch
"Art"/"Artikel"-Zitate. 4 neue Tests (2x `build_source_url`-Artikelmuster/
None-Fall, 4 Parametrisierungen fuer den Artikel-Fast-Path).

Vorheriger bestaetigter Lauf: 13.09., Ollama-`keep_alive`-Fix (real
gegen die installierte Produktionsinstanz gemessener Kaltstart: 144.67s
vs. 6.79-10.26s warm - siehe DECISIONS.md) + realer Installer-Rebuild/
Clean-Upgrade-Install/E2E-Smoke-Test (echter Login+Chat-Workflow mit
Dokument+PII gegen die echte installierte `.exe`, echtes Ollama, echter
Anthropic-Aufruf - Testkonto danach vollstaendig entfernt). 1 neuer Test
(`test_generate_structured_sends_extended_keep_alive`).

Vorheriger bestaetigter Lauf: 13.09., Streaming-Architekturentscheidung
(serverseitiges, gepuffertes Streaming fuer den risikobasierten Fast Path -
siehe DECISIONS.md): `DraftingService.create_draft` in `_prepare_and_gate`/
`_finish_non_streaming` refaktoriert (reine Extraktion, 38 bestehende Tests
vorher/nachher unveraendert gruen), neue `create_draft_stream`, `write_stream`
in `AnthropicClaudeWritingProvider`, `ChatService.send_message_stream`, neuer
SSE-Endpunkt `POST /dashboard/chat/send-stream`, `chat.html`-Composer nutzt
echtes Fetch-Streaming mit Fallback. Real gemessen (echte Anthropic-Aufrufe):
TTFR 8.1-8.6s vs. TOTAL 16.9-18.4s fuer den streaming-faehigen Fall (ca.
44-56 % TTFR-Reduktion), Gesamtlatenz unveraendert wie gefordert. 6 neue
Tests in `tests/test_drafting_service_streaming.py` (inkl. kritischer
Anomalie-Abbruch-Gegenprobe), 4 neue in `tests/test_chat_service.py`, 2 neue
in `tests/test_web_chat.py` (echter SSE-Roundtrip). 1 bestehender Test an
die neue `setLoadingState()`-Struktur angepasst (gleiche Kernaussage).
Siehe DECISIONS.md.

Vorheriger bestaetigter Lauf: 13.09., Gesetzesbibliothek ("Gesetze im
Internet") an den Chat angebunden - echter vertikaler Slice: neues Modul
`app/laws/gesetze_im_internet.py` (offizielles XML-Parsing/Import, real
gegen die Live-Seite verifiziertes Deep-Link-Schema), Migrationen
`schritt3_011`/`schritt3_012` (`LawSection.source_name/doknr/source_url`,
`ChatMessage.law_section_id`), neuer Chat-Fast-Path fuer reine
Normzitat-Fragen (`app/chat/service.py::_looks_like_pure_norm_question`,
Ganze-Nachricht-Regex-Anker) mit echter, gemessener Latenz von 31.5 ms
(vorher 18-22s) fuer "Was steht in § 558 BGB?", echte Zitate in der
"Quellen & Verweise"-Karte (`app/web/chat_router.py::
_gather_message_sources` erweitert). 9 neue Tests in
`tests/test_laws_gesetze_im_internet.py` + 13 neue Tests in
`tests/test_chat_service.py` (inkl. kritischer Gegenproben gegen
Fallbezug/Zusatzinhalt). Ein echter Regex-Bug (`erkl[äa]er?e` matchte
"erkläre" nicht) wurde durch einen fehlschlagenden neuen Test gefunden
und behoben. Siehe DECISIONS.md.

Vorheriger bestaetigter Lauf: 13.09., UI/UX-Ueberarbeitung Phase 4
(Chat: Breadcrumb, Nutzernachrichten-Bubble, Kopieren-Button, "Vollständigen
Editor öffnen"-Link, echte "Quellen & Verweise"-Karte, "Aktenbezug"/Chat-
zu-Akte-Zuordnung mit neuem `link-matter`-Endpunkt) + Phase 5 (KI-Analyse-
Transparenzkarte, Entwurf-Editor-Header mit Statuspille/"Zuletzt
gespeichert"). 10 neue Tests. Siehe DECISIONS.md.

Vorheriger bestaetigter Lauf: 13.09., UI/UX-Ueberarbeitung Phase 1-2
(Navigation auf sechs Bereiche reduziert, Profilmenü, Einstellungen-Hub,
neue echte Akten-Uebersicht/-Detailseite statt Platzhalter). 8
Sidebar-Tests in test_web_inbox.py auf die neue Architektur umgeschrieben
(alte Struktur ist bewusst ueberholt, keine Regression), 13 neue Tests in
test_web_matters.py, kleinere Anpassungen in test_web_account.py/
test_web_laws.py/test_web_placeholder.py. Siehe DECISIONS.md fuer die
volle Begruendung.

Vorheriger bestaetigter Lauf: 13.09., vierte Fortsetzung
(Architekturentscheidung: risikobasierte §65-Pipeline). CASE A
(einfache Chat-Frage ohne Dokument/PII) überspringt jetzt die beiden
LLM-gestützten §65-Schritte (Vorabanalyse + semantische
Antwortvalidierung) - real gemessen ~57-103s → ~16-21s. Presidio/
Pseudonymisierung und die deterministische Platzhalter-
Integritätsprüfung bleiben unverändert Pflicht. CASE B/C (Dokument
bzw. explizites Drafting) bleiben unverändert auf der vollen Pipeline,
2/2 real erfolgreich, keine Regression. 8 neue Tests (2 in
test_ai_providers_local.py, 6 in test_drafting_service.py; 2
Bestandstests in test_performance_regression.py fachlich an die neue,
bewusste Architekturentscheidung angepasst statt entfernt). Siehe
DECISIONS.md fuer die volle Evidence Matrix.

Vorheriger bestaetigter Lauf: 13.09., spaeter (P0 Root-Cause-
Engineering-Run: Performance + Routing). Request-Correlation-Trace
ergaenzt (`app/observability/perf_trace.py`, 5 neue Tests) und in
`ChatService.send_message`/`DraftingService.create_draft` verdrahtet (2
neue Integrationstests). Realer End-zu-Ende-Fund: die lokale
Antwortvalidierung (`validate_claude_response`) ist ein ZWEITER,
ebenfalls architektonisch verpflichtender Ollama-Roundtrip - real 14-33s
zusaetzlich zur bereits gefixten Vorabanalyse. Unabhaengiger Routing-Fund:
"Erstelle daraus einen Einspruch." (explizites Auftragsbeispiel) wurde
nicht als Drafting erkannt - "einspruch"/"widerspruch"/"beschwerde" zur
Substantiv-Liste in `_DRAFTING_TRIGGER_PATTERN` ergaenzt (5 neue Tests).
Siehe DECISIONS.md fuer die volle Hypothesenpruefung (H1-H9) und reale
CASE-A/B/C-Messwerte.

Vorheriger bestaetigter Lauf: 13.09. (Performance-Fix: reale
Chat-Latenz war 4-5 Minuten, dominiert von `OllamaLocalLLMProvider.
process()` ohne `format`-Schema-Constraint (real gemessen ~124s je
Aufruf). Fix: `process()` nutzt jetzt denselben Constraint wie
`generate_structured()` (real gemessen ~9x schneller). ECHTER
Zwischenfund waehrend der Verifikation: derselbe Constraint liess das
Modell bei praktisch leerem Sachverhalt (typisch fuer einfache
Chat-Fragen ohne Akteninhalt) wiederholt FIKTIVE Fallgeschichten
erfinden (Verstoss gegen CLAUDE.md "Niemals ... erfinden") - behoben
durch eine explizite Anti-Halluzinations-Anweisung im System-Prompt
("Kein inhaltlicher Sachverhalt vorhanden." statt Erfindung), real
verifiziert mit dem exakten Produktions-Prompt (3 Wiederholungen, alle
korrekt). 6 neue Performance-Regressionstests in
tests/test_performance_regression.py (5 Szenarien + 1 "niemals
uebersprungen"-Test) + 1 neuer Test fuer den Anti-Halluzinations-Prompt
in tests/test_ollama_local_llm_provider.py. Siehe DECISIONS.md fuer die
vollen Messwerte.

Vorheriger bestaetigter Lauf: 13.09. (Chat-Default-Fix: Zentraler
Chat verwendet jetzt "chat_response"-Zweck statt immer "formulate_draft"
- siehe DECISIONS.md; POS-Tag-Verfeinerung der "unerkannte Namen"-
Heuristik in security_check.py, behebt einen frueher dokumentierten,
bewusst zurueckgestellten Fund (ADJ+NOUN-Ueberschriften wie "Salvatorische
Klausel" faelschlich als Name gewertet); Wortgrenzen-Fix in
app/deadlines/extractor.py. 7 bestehende Tests, die sich auf den ALTEN,
fehlerhaften Heuristik-Nebeneffekt verlassen hatten, wurden auf
deterministische Stubs bzw. den tatsaechlich beabsichtigten Fund
umgestellt (siehe DECISIONS.md fuer die volle Begruendung je Test).

Vorheriger bestaetigter Lauf: 12.09. (vollständiger KanzleiAI→Lexono-
Rename über das gesamte aktive Produkt - Spec/Installer/Paketname/
Session-Cookie/Backup-/Log-Dateinamen/Thread-Name/verbleibende
CLI-Hinweistexte in `backup.html`/`settings.html`; `app/setup/paths.py`s
`resolve_data_dir()`-Datenmigration per 11 dedizierten Tests inkl. echter
SQLite-Fixture abgedeckt). Siehe `.agentic/DECISIONS.md` (Eintrag
"Supersedes...") fuer den vollen Evidenz-Kontext. Verlauf der Baseline:
1463 (31.08.) →
1472 → 1484 → 1485 → 1486 → 1487 → 1491 → 1493 (01.09.) → 1520 (12.09.,
natives Fenster-Chrome ersetzt eigene Titelleiste - 9 `_NativeApi`-Tests
entfernt, 2 DWM-Tests ergänzt) → 1522 (12.09., Local-AI-Heartbeat, 2 neue
Tests) → 1526 (12.09., FastEmbed-Lazy-Load-Fix, 4 neue Tests in
`tests/test_search_service.py`) → 1528 (12.09., First-Run-Fix: 1
fehlerhaft-kodierter Test korrigiert, 3 neue Tests in
`tests/test_run_entrypoint.py`) → 1531 (12.09., Start.vbs-Fix: 3 neue
Tests in `tests/test_setup_wizard.py`, 1 korrigiert in
`tests/test_start_vbs.py`) → 1538 (12.09., vollstaendiger
KanzleiAI→Lexono-Rename: `tests/test_setup_paths.py` komplett neu
geschrieben, 11 Tests fuer `resolve_data_dir()`-Migration statt vorher
weniger; diverse Tests in `test_auth_web.py`,
`test_rate_limiting_and_session_revocation.py`, `test_end_to_end.py`,
`test_start_vbs.py`, `test_documents_ocr.py`,
`test_local_ai_ollama_installer.py` an neue Namen angepasst, kein
Netto-Testverlust).

## Wie ausführen

Volle Suite (aus aktiviertem `.venv`):

```
pytest
```

Bei Verdacht auf Testisolationsprobleme (siehe Vorfall unten) IMMER die
volle Suite laufen lassen, nicht nur einzelne/geänderte Testdateien –
Einzeldatei-Läufe können ordnungsabhängige Fehler verdecken.

## Bekannter Vorfall: Testisolation über `app.state`

`app.state` gehört zum einzigen prozessweiten `app`-Singleton
(`from app.main import app`). Tests, die den echten FastAPI-Lifespan via
`with TestClient(app) as ...:` durchlaufen lassen (z. B.
`tests/test_main_local_ai_startup_check.py`), setzen `app.state.local_ai_status`
dauerhaft für den restlichen Testprozess. Tests, die einen bestimmten
Ausgangszustand dieses Attributs erwarten, MÜSSEN ihn selbst
speichern/löschen/wiederherstellen (siehe
`tests/test_web_chat.py::test_chat_page_shows_local_ai_checking_state_without_lifespan`
als Referenzmuster).

## Nach Änderungen an dieser Datei

Diese Datei nach jedem vollständigen Testlauf mit neuem Ist-Stand
aktualisieren (Agent I – QA/Test Engineer ist dafür zuständig), nicht nur
bei Verschlechterungen.

## 17.09. (Overnight-Direktive "AUTONOMOUS MULTI-HOUR PRODUCT BUILD")
```
1924 passed, 1 skipped, 0 failed
```
+2 (Draft.message_id-Plumbing), +4 (message/document → Draft.message_id
end-to-end), +2 (Anhang-Chip-Links Posteingang), +2 (Deadline→Dokument-Link
statt nur Akte-Link) gegenueber 1917 - siehe OPEN_ISSUES.md fuer die drei
zusammengehoerigen "Dokumente verbinden"-Funde.

## 17.09. (Fortsetzung, Overnight-Direktive) - Fristen-Pruefung + Party-Anbindung
```
1946 passed, 1 skipped, 0 failed
```
+8 (Fristen bestaetigen/verwerfen, echter Schreibpfad fuer
Deadline.review_status), +8 (Party anlegen/entfernen, echter Schreibpfad
fuer den bereits laufenden known_entities-Konsumenten) gegenueber 1931 -
beide P1-Funde: Datenmodell + Lesepfad/Anzeige existierten bereits
vollstaendig, es fehlte in beiden Faellen ausschliesslich die Schreib-
Aktion. Siehe OPEN_ISSUES.md fuer die volle Herleitung je Fund.

## 17.09. (Fortsetzung) - reset_demo_data Party-Bereinigung
```
1947 passed, 1 skipped, 0 failed
```
+1 gegenueber 1946: `reset_demo_data` kannte `Party` noch nicht (das
Modell ist erst seit heute ueberhaupt anlegbar) - waere sonst eine neue
Waisen-Quelle gewesen, siehe OPEN_ISSUES.md.

## 18.09. ("WEITERARBEITEN" - vier neue Schreibpfad-/UX-Funde)
```
1972 passed, 1 skipped, 0 failed
```
+25 gegenueber 1947: "Akte anlegen" (7), "Akte bearbeiten/abschließen/
wieder öffnen" (6), Dokument-"Umbenennen" (6), Posteingang-Suche (8) -
25 statt 27, da einzelne bestehende Tests miterweitert statt neu
hinzugefuegt wurden. Live gegen den frisch installierten Build verifiziert
(GUI + HTTP), siehe OPEN_ISSUES.md.

## 18.09. (Fortsetzung) - Dokument-Upload zu bestehender Akte
```
1979 passed, 1 skipped, 0 failed
```
+7 gegenueber 1972: neue Route `POST /{matter_id}/documents/upload`
(`app/web/document_actions_router.py`) - Referenzabgleich zeigte einen
projektweit fehlenden direkten Upload-Weg zu bereits bestehenden Akten.
Live gegen frisch installierten Build verifiziert (GUI-Button + HTTP-
Request-Zyklus), siehe OPEN_ISSUES.md.

## 18.09. (Fortsetzung) - Entwurf-PDF-Export
```
1991 passed, 1 skipped, 0 failed
```
+12 gegenueber 1979: `DraftPdfExportService` (`app/export/pdf_export_service.py`)
- Referenzabgleich zeigte PDF als primaeren Export-Format-Radiobutton,
projektweit existierte nur DOCX. Wiederverwendet bereits vorhandene
`pymupdf`-Abhaengigkeit (keine neue Bibliothek), identisches Muster wie
`GeneratedDocumentPdfExportService`. Visuell per gerenderter Vorschau
geprueft.

## 18.09. (Fortsetzung) - Frist manuell anlegen
```
1998 passed, 1 skipped, 0 failed
```
+7 gegenueber 1991: neue Route `POST /{matter_id}/deadlines`
(`app/web/deadline_actions_router.py`) - Fristen entstanden projektweit
ausschliesslich automatisch aus Dokumenten, kein manueller Anlegeweg fuer
telefonisch/anderweitig bekannte Fristen.

## 18.09. (Fortsetzung) - Draft Quality Ratings UI-Anbindung
```
2005 passed, 1 skipped, 0 failed
```
+7 gegenueber 1998: `app/web/quality_router.py` existierte bereits
vollstaendig und gesichert (Prompt 43/46), war aber projektweit
unverlinkt (gefunden per Orphan-Route-Suche, nicht per Referenzabgleich).
POST-Endpunkt von JSON auf das etablierte Formular+Redirect-Muster
umgestellt, neue "Qualitätsbewertung"-Sektion auf draft_detail.html (nur
fuer bereits freigegebene Entwuerfe). Live gegen frisch installierten
Build verifiziert (isolierter synthetischer Test-Datensatz, HTTP-Zyklus,
danach vollstaendig bereinigt).

## 18.09. (Fortsetzung) - Akte-Verlauf + Posteingang-HTMX-Ziel-Fix
```
2011 passed, 1 skipped, 0 failed
```
+6 gegenueber 2005: Akte-Verlauf (3 neue Tests test_web_matters.py + 1 in
test_audit_service.py fuer den Party-Einschluss-Fund) + Posteingang-
Filter/Suche-Sichtbarkeitsfix (2 neue Tests test_web_inbox.py). Gefunden
per systematischer Orphan-Service- bzw. HTMX-Ziel-Pruefung, nicht per
Referenzabgleich. Voller Lauf mit angehaengtem Speicherdruck-Vorfall (zwei
Versuche wegen Systemspeicherknappheit abgebrochen, dritter Versuch ohne
den schweren Embeddings-Modelltest erfolgreich, dieser danach isoliert
nachgeholt - 2010+1=2011 zusammengefasst). Live gegen frisch installierten
Build verifiziert (GUI + HTTP), siehe OPEN_ISSUES.md.

## 18.09. (Fortsetzung) - User.display_name Selbstbedienung
```
2015 passed, 1 skipped, 0 failed
```
+4 gegenueber 2011: `POST /dashboard/account/me/display-name` -
`User.display_name` wurde an mehreren Stellen mit `or user.email`-
Fallback gelesen, hatte aber projektweit keinen Schreibweg (gefunden per
systematischer "totes Modellfeld"-Suche). Live gegen frisch installierten
Build verifiziert (HTTP), siehe OPEN_ISSUES.md.

## 20.09. (Fortsetzung, "CONTEXT EXTENSION"-Direktive §5/§6) - Entwurf-Editor
Briefkopf-/Signatur-Vorschau + Logo/Signatur-Berechtigungsfix
```
2147 passed, 1 skipped, 0 failed
```
+6 gegenueber 2141: der zuvor als "decision-dependent" eingeordnete Editor-
Ausbau (siehe OPEN_ISSUES.md "Dokumentensystem-Audit 19.09.") wurde per
Owner-Direktive freigegeben, bewusst als kleinste professionelle Loesung
umgesetzt (Seiten-Vorschau mit echtem Briefkopf/Signatur, KEIN Rich-Text-
Editor, KEINE strukturierten Empfaenger-/Betreff-/Signatur-Felder auf
`Draft` - siehe DECISIONS.md). `draft_detail.html` zeigt jetzt `.document-
page` mit denselben Bausteinen wie der echte PDF-/DOCX-Export
(app/export/letterhead.py, wiederverwendet statt dupliziert). Dabei
gefundener echter Bug: `firm_logo_file`/`firm_signature_file`
(app/web/settings_router.py) waren `_require_admin`-gesperrt, obwohl
`export_draft_docx`/`export_draft_pdf` dieselben Bilddaten laengst jedem
angemeldeten Nutzer ausliefern - auf `require_login` angeglichen (3 neue
Tests in test_web_settings.py, 3 neue in test_web_drafts.py). Voller
Lauf gruen, Build/Install/SHA-256-Hash-Abgleich erfolgreich, live gegen den
tatsaechlich laufenden installierten Prozess verifiziert (echter Logo-/
Signatur-Upload per HTTP-Multipart über `System.Net.Http.HttpClient`
gegen Port 8000 - `Invoke-WebRequest` UND `HttpClientHandler`s
`CookieContainer` verwerfen das `Secure`-Cookie über `http://127.0.0.1`
beide stillschweigend, Workaround: `UseCookies=$false` + manueller
`Cookie`-Header aus dem `Set-Cookie`-Header der Login-Antwort; siehe
DECISIONS.md fuer den vollen Workaround-Fund. Bild-Bytes byteweise
verglichen (Upload == ausgelieferte Datei), HTML-Struktur/Reihenfolge im
echten Response geprueft, danach das produktive `FirmProfile`-Singleton
(`C:\ProgramData\Lexono\data\kanzlei_ai.db`, NICHT das Repo-`data/`-
Verzeichnis - siehe DECISIONS.md fuer die Datenverzeichnis-Klarstellung)
wieder auf den urspruenglichen leeren Zustand zurueckgesetzt und live
bestaetigt (Hinweistext + 404 auf logo-file wieder da).

## 20.09. (Fortsetzung, "CONTEXT EXTENSION"-Direktive §13/§15 - naechste
Arbeitseinheit nach Abschluss der Editor-Aufgabe) - Posteingang-
Fristenerkennung aus Nachrichtentext + nachtraeglich verarbeiteten Anhaengen
```
2153 passed, 1 skipped, 0 failed
```
+6 gegenueber 2147: `DeadlineAnalysisService` (bisher nur fuer Dokumente)
um `analyze_message` erweitert - erkennt Fristen jetzt auch direkt im
Nachrichtentext (`Message.body_text`) einer Posteingang-Nachricht, exakt
nach demselben, bereits etablierten und getesteten Muster wie
`analyze_document` (idempotent, `review_status` bleibt immer
"unreviewed", keine LLM-/Cloud-Beteiligung - reine lokale Regex-
Erkennung). Neue nullable `Deadline.message_id`-Spalte (Migration
`schritt3_016`, batch-mode FK wie bei `chat_messages.law_section_id`)
analog zum bereits vorhandenen `document_id`. Verdrahtet in
`app/web/router.py::accept_matter_suggestion` (dem einzigen Ort im
Produktivcode, an dem Nachrichten tatsaechlich einer Akte zugeordnet
werden - der separate automatische `MatterAssignmentService.assign_matter`-
Pfad wird nirgends aufgerufen, bereits vorher dokumentiert). Dabei ZWEITER
echter Fund behoben: Dokumentanhaenge, die VOR der Aktenzuordnung bereits
Volltext extrahiert bekamen, wurden bei ihrem ersten (mangels
Aktenzuordnung erfolglosen) Analyseversuch nur uebersprungen, nie erneut
versucht - blieben dadurch fuer immer unanalysiert. Beide Analysen laufen
jetzt bei jeder Aktenzuordnung nach (idempotent, keine Duplikate bei
mehrfacher Zuordnung/erneutem Aufruf). 4 neue Tests
(tests/test_deadlines_service.py: erfolgreiche Erkennung, kein Text,
keine Akte, Idempotenz) + 2 neue Integrationstests
(tests/test_web_inbox.py: Nachrichtentext-Frist ueber den echten HTTP-
Endpunkt, nachtraegliche Anhang-Analyse). Build/Install/SHA-256-Hash-
Abgleich erfolgreich; App-Neustart bestaetigt die automatische
`alembic upgrade head`-Anwendung der neuen Migration gegen die echte
Produktions-DB (Spalte `message_id` real per `PRAGMA table_info`
bestaetigt). Live-E2E: synthetische Nachricht mit Frist im Text
("...bis zum 21.11.2027...") ohne Aktenzuordnung real in die
Produktions-DB eingefuegt, ueber den echten HTTP-Zuordnungs-Endpunkt
(QA-Testkonto) einer echten Akte zugeordnet - Deadline-Datensatz real
entstanden (`due_date=2027-11-21`, `review_status=unreviewed`,
`message_id` korrekt gesetzt), auf der Aktendetailseite bestaetigt
sichtbar. Auf der "Aufgaben & Fristen"-Uebersichtsseite NICHT sichtbar -
kein Fehler in diesem Feature, sondern bereits dokumentierte
Datenbestand-Verschmutzung (hunderte alte Test-Fristen ab 1987, `LIMIT 50`
sortiert nach faelligstem Datum zuerst, siehe bestehender "225/284 Junk-
Fristen"-Fund in OPEN_ISSUES.md) - real durch Pruefung der Aktendetailseite
statt blinder Behauptung bestaetigt. Synthetische Nachricht + Deadline +
Audit-Events danach vollstaendig aus der Produktions-DB entfernt, per
Zaehlabfrage auf Null bestaetigt.
