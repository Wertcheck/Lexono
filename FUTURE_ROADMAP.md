# FUTURE_ROADMAP.md – Priorisierte Erweiterungen nach v0.1.0

**Stand:** Prompt 45 Abschluss  
**Basis-Version:** v0.1.0 (MVP, Pilot-fertig)  
**Dieses Dokument:** Planung für v0.2.0–v1.0 (2–6 Monate)

---

## Strategisches Vorhaben: Lexono Data Privacy Framework (Priorität: hoch, release-übergreifend)

**Ziel:** Den bisher schrittweise gewachsenen Datenschutz (Privacy-Gateway, Pseudonymisierung, Rückfrage, Siegel,
Entscheidungen) zu einem geschlossenen, nachweisbaren Rahmenwerk zusammenführen – mit festen Sicherheitsgrenzen,
messbaren Qualitätszielen und regelmäßiger Validierung. **Status: nur Planung (12.10.2026)** – dieser Abschnitt
beschreibt Vorhaben, keine Implementierung. Referenz für den Ist-Zustand: `PRIVACY_REVIEW_ARCHITECTURE.md`,
`PERFORMANCE_BENCHMARKS.md`, Commits bis `9d9ab9c`.

**Priorität vor anderen v1.0-Themen:** Phasen P1–P3 sind Voraussetzung für einen Einsatz über den Pilot hinaus;
P4–P6 folgen in dieser Reihenfolge. Jede Phase gilt erst als abgeschlossen, wenn ihre Abnahmekriterien mit
synthetischen Daten belegt sind. Vollständige Leckfreiheit wird nie aus synthetischen Tests abgeleitet.

#### Phase P1: Privacy by Design und Datenschutzarchitektur
- **Ist-Zustand (belegt):** Einziger Cloud-Weg über `ClaudePrivacyGateway.prepare_request`; Allowlist-Schema mit 8 Feldern;
  Local-First (Presidio/spaCy, Ollama lokal); Aktenisolation über `matter_id`; Architekturdoku in `PRIVACY_REVIEW_ARCHITECTURE.md`.
- **Offene Risiken:** Keine formale Datenfluss-Inventur je Funktion (welches Feld enthält welche Datenkategorie);
  Relay-Server (`gateway/`) prüft das Allowlist-Schema bewusst nicht selbst; Bedrohungsmodell nicht als eigenes Dokument gepflegt.
- **Maßnahmen:** Datenfluss- und Datenkategorien-Register; Bedrohungsmodell (STRIDE o. ä.) für Gateway, Relay, lokale KI;
  Architektur-Entscheidungen als ADRs im bestehenden `.agentic/DECISIONS.md`.
- **Abhängigkeiten:** keine (Startphase).
- **Abnahmekriterien:** Register deckt 100 % der Cloud-Einstiegspunkte ab (Abgleich mit Code-Suche nach Provider-Aufrufen);
  jedes Payload-Feld hat eine dokumentierte Datenkategorie; Bedrohungsmodell vom Owner abgenommen.

#### Phase P2: Pre-Cloud-Prüfung und Fail-Closed
- **Ist-Zustand (belegt):** Pseudonymisierung mit mehrstufiger Erkennung (Regex, bekannte Entitäten, NER, PROPN-Signal,
  Modell-Probing), Restrisiko-Scan, Final Payload Gate, Antwort-Leak-Check; versiegelte Payload – alle vier Cloud-Provider
  lehnen unversiegelte/veränderte Payloads ab; Fehler in der Prüfung blockieren.
  Synthetische Restlecks (Stand `9d9ab9c`, nicht-interaktiv): 0,4 % mit Akte, 0,7 % ohne Akte.
- **Offene Risiken:** Restlecks bei Namen ohne unabhängiges Signal (z. B. „Richter“ in Überschriften, „Lange“ als Adjektiv
  gelesen); Fachbegriff-Fehlalarme im Masken-Modus in künstlichen Namensrollen-Sätzen (100/600); Siegel-Schlüssel nur
  prozesslokal (ausreichend für Ein-Prozess-Betrieb, nicht für verteilte Varianten); Erkennung hängt von `de_core_news_lg` ab.
- **Maßnahmen:** Festes Benchmark-Set als Release-Gate; Modell-Upgrade-Prozess mit Vorher/Nachher-Messung;
  Relay-seitige Signatur-/Herkunftsprüfung falls der Relay je Fremd-Clients bedient.
- **Abhängigkeiten:** P1 (Register definiert, welche Felder zu prüfen sind).
- **Abnahmekriterien:** Release blockiert automatisch, wenn Restlecks > 0,5 % (mit Akte) bzw. > 1,0 % (ohne Akte) oder
  Rechtswort-Fehlalarme > 1/132 auf dem Referenz-Benchmark; Test „unversiegelte Payload wird abgelehnt“ für jeden Provider grün.

#### Phase P3: Human-in-the-Loop
- **Ist-Zustand (belegt):** Datenschutz-Rückfrage im Chat vor jedem Cloud-Aufruf (drei Optionen), erneute Gesamtprüfung nach
  jeder Entscheidung; Wiederherstellung nach Neuladen (rein lokal); Fundstelle im Dokument; prozessübergreifende
  Doppelversand-Sperre in der Datenbank, nach Abbruch keine automatische Wiederaufnahme.
- **Offene Risiken:** Nicht-interaktive Pfade (Schriftsatz-Generator, Entwurfsanweisungen, Review) fragen nicht, sondern
  maskieren vorsichtshalber; Rückfragequote in echten Akten unbekannt (nur synthetisch gemessen: 4 Rückfragen in 19 Dokumenten).
- **Maßnahmen:** Rückfrage auch in Formular-Workflows (vor dem Absenden); Sammelansicht offener/unsicherer Entscheidungen je Akte.
- **Abhängigkeiten:** P2 (Kandidatenerkennung), P5 (Aufbewahrung der Entscheidungen).
- **Abnahmekriterien:** Kein Cloud-Aufruf mit offener Rückfrage (Test je Einstiegspunkt); höchstens 1 Rückfrage je
  synthetischem Standarddokument im Median; Pilot-Rückmeldung zur Verständlichkeit dokumentiert.

#### Phase P4: Datenschutzkonformes Lernen aus Feedback
- **Ist-Zustand (belegt):** Entscheidungen aktenbezogen (`privacy_decisions`), „kein personenbezogenes Datum“ nur in der
  Unterhaltung, Widerspruch → strengere Behandlung; globale Statistik nur als HMAC ohne Klartext (`privacy_feedback_stats`);
  Auswertungsskript ohne automatische Regeländerung.
- **Offene Risiken:** Kein definierter Prozess, wie aus der Statistik geprüfte Regeln oder Tests werden; keine Mindestanzahl
  übereinstimmender Entscheidungen festgelegt.
- **Maßnahmen:** Review-Prozess „Statistik → synthetischer Testfall → Regel → Positiv-/Negativtests → Freigabe“;
  Schwellen für Auffälligkeit; Kennzeichnung widersprüchlicher Begriffe.
- **Abhängigkeiten:** P2 (Benchmark-Gate), P6 (Audit der Freigaben).
- **Abnahmekriterien:** Keine Regeländerung ohne verlinkten synthetischen Test und Owner-Freigabe; Statistik enthält
  nachweislich keine Klartext-Begriffe (automatisierter Test); kein Modelltraining aus Einzelentscheidungen.

#### Phase P5: Datenminimierung, Löschung und Mandantentrennung
- **Ist-Zustand (belegt):** Pseudonym-Zuordnungen nur im Arbeitsspeicher; API-Log speichert nur Hash des Prompts;
  Rückfrage-Entscheidungen werden mit der Unterhaltung gelöscht (unterhaltungsbezogene) bzw. bleiben als Aktendatum;
  Akten werden weich gelöscht (Aufbewahrung). Seit 13.10.2026 (`PRIVACY_REVIEW_ARCHITECTURE.md` §27): gelöschte Akten
  liefern keinen KI-Kontext und sind über Dokumentansicht, Suche und API nicht mehr erreichbar; doppelte Dokumente und
  Fristen gelöschter Dokumente werden nicht gesendet; Wissenseinträge mit Namen anderer Akten werden nicht gesendet;
  Lebenszyklus je Datenart in §27.2 dokumentiert.
- **Offene Risiken:** Kein dokumentiertes Löschkonzept mit Fristen über alle Tabellen (Dokumente, Chats, Entscheidungen,
  Audit); kein Mehr-Kanzlei-Modell (eine Datenbank je Kanzlei – Mandantentrennung nur über Akten).
- **Maßnahmen:** Lösch- und Aufbewahrungskonzept je Datenkategorie (aus P1; Vorschlag K1–K9 in §27.5); Löschlauf mit
  Nachweis; Prüfung, ob Entscheidungen beim endgültigen Löschen einer Akte vollständig entfernt werden; anfragebezogene
  Dokumentauswahl statt ganzer Akte (Optionen M3–M6 in §27.1, nur mit Qualitätsmessung). Priorisierte Arbeitspakete
  B1–B7 (Stufe B), W1–W9 und O1–O6 (Stufe C) in `PRIVACY_REVIEW_ARCHITECTURE.md` §28.3; B1 (kein Browser-Cache für
  sensible Antworten) erledigt. W1 teilweise (§29): eine Chatfrage mit Anhang sendet nur den Text der angehängten
  Dokumente (Fristen aktenweit, sichtbarer Hinweis); Editor, Neugenerierung und Schriftsatz weiter ganze Akte.
  Benchmark `benchmarks/chat_scope_v1` (§30): Informationsverfügbarkeit offline gemessen; Antwortqualität erst nach
  freigegebenem Live-Lauf. Entscheidungsvorlage Editor/Neugenerierung (Optionen O1–O5) in §30.4, offen.
  Adversarialer Pseudonymisierungs-Benchmark `benchmarks/pseudonymization_v1` (§31): Recall/Precision je Typ,
  Stilfeld-Lücke behoben; P05 (Platzhalterkonsistenz bei Anrede/Genitiv) behoben (§32, Konsistenz 2/2);
  §33: andere Person mit gleichem Nachnamen bei widersprechendem Geschlecht getrennt, Adjektive nicht mehr dem Mandanten
  zugeordnet, Bundes-Fundstellen lesbar und Bundes-Aktenzeichen sonst erkannt (Fehlalarme 10 -> 7). Offen:
  OCR-Schreibweisen, eigene Bundesverfahren in reiner Zitierform, vorbestehende Restrisiko-Fehlblockaden,
  Absturz bei Mandantennamen, die Teil eines internen Markers sind (z. B. "Alt").
- **Abhängigkeiten:** P1 (Register).
- **Abnahmekriterien:** Für jede Tabelle mit personenbezogenen Daten ist eine Löschregel definiert und per Test belegt;
  aktenübergreifender Zugriff auf Entscheidungen ist per Test ausgeschlossen.

#### Phase P6: Auditierbarkeit und kontinuierliche Sicherheitsvalidierung
- **Ist-Zustand (belegt):** Unveränderliches Audit-Log (Entscheidungen ohne Begriff protokolliert); Blockgründe kategorisiert;
  reproduzierbare Benchmarks (`scripts/bench_name_detection.py`, `scripts/bench_chat_without_file.py`); Payload-Prüfung
  des installierten Builds per Fang-Server (QA-Werkzeug außerhalb des Repos).
- **Offene Risiken:** Benchmarks laufen manuell; der Fang-Server-QA-Lauf ist nicht Teil des Repos; kein periodischer Bericht.
- **Fortschritt (14.10.2026, §34):** Realistic Legal Benchmark `benchmarks/legal_e2e_v1` – versionierter Korpus
  synthetischer Akten (Dev/Test getrennt, Validator gegen erfundene Referenzwerte), Quellenregister (Nutzungsrechte
  BLOCKIERT bis zur Prüfung durch eine verantwortliche Person) und E2E-Runner über Import/OCR, Verarbeitung, Suche, Chat,
  Schriftsatz, Bearbeitung und Export mit Payload-Erfassung vor der Cloud-Grenze; Regressionstest in der Suite. Erste
  Befunde behoben: drei Wege, auf denen ein Geburtsdatum ohne Bezeichnung oder zerteilt (Fristausschnitt, Auszug langer Dokumente, Schreibweisen) im Klartext hinausging. Gemessen (§34.6): Fehlalarme der Maskierung zerstören Kernfakten ("bei Grün" -> [PERSON], "Testament"); F (§35): strukturelle Kompositum-Regel mit Namensprobe 0 umgesetzt, keine neuen Lecks; LE-T003 entblockt durch erhaltene Feldgrenze in der PDF-Aufbereitung (§35.6), ohne Gate-Lockerung. Q (§36): lokale Fundstellenprüfung (Existenz getrennt von Stützung) umgesetzt, erfundene Fundstellen 0/12 -> 12/12 markiert. §34.7: langer Vertrag im E2E, neuer Testfall LE-T003 (alle Cloud-Anfragen durch NER-Fehlalarm im Restrisiko-Scan blockiert, kein Leck), Firmenname ohne Rechtsform geschuetzt. Offen: mehr Testfälle, Lauf gegen den
  installierten Build, Klassifikationskorrektheit, Antwortqualität eines echten Modells (nach Freigabe).
- **Maßnahmen:** Benchmark- und Payload-Prüfung als fester Release-Schritt; Fang-Server als versioniertes QA-Werkzeug ins
  Repo; Datenschutzbericht je Release (Restlecks, Fehlalarme, Rückfragen, Blocks, Laufzeit).
- **Abhängigkeiten:** P2 (Gate-Kriterien), P4 (Freigabeprozess).
- **Abnahmekriterien:** Jeder Release enthält einen Datenschutzbericht mit den Kennzahlen aus P2/P3; Payload-Prüfung des
  installierten Builds mit 0 bekannten Originalwerten in den synthetischen Referenzfällen; Audit-Log deckt jede
  Rückfrage-Entscheidung und jeden Datenschutz-Block ab.

---

## Stand Qualitätspunkte P1/P2 (15.10.2026, belegt)

Es gibt keine eigene `ROADMAP.md`; diese Datei ist die Planungsgrundlage. Der Stand ist nur, was mit synthetischen Daten
gemessen und in `PRIVACY_REVIEW_ARCHITECTURE.md` belegt ist.

| Punkt | Stand | Beleg |
|---|---|---|
| P1.1 Tabellenversand im installierten Build | erledigt: Ursache Prüfskript; klare Meldung bei unlesbarem Anhang; installierter Build PASS | §38 |
| P1.2 Aktenzuordnung („Ihr Zeichen“, bekannte Absender, Mehrdeutigkeit) | erledigt: automatisch nur bei Aktenzeichen und bekanntem Absender; installierter Build PASS | §39 |
| P2.1 Fristen/Klassifikation (Ereignisdaten, Unbekannt) | erledigt: Ereignisdaten als Frist 27 → 0 bzw. 17 → 0 (Legal-E2E) | §40 |
| P2.2 Testkorpus + Modellvergleich | erledigt: Korpus `benchmarks/classification_deadlines_v1` (Dev 34, Test 33, blind, eingefroren); Regeln im Test: kritische Fehler 12 → 6; Claude und OpenAI im Test ohne belastbaren Unterschied, beide deutlich besser als die Regeln | §41 |
| P2.3 Blinder Testkorpus v2 + Dev-Korrekturen | erledigt: `test_v2` (104 Dokumente, vier blinde Agenten, eingefroren). Regeln blind: Typ 64 → 84/104, Fristen 63 → 66/76, kritische Fehler 18 → 16 (3 neue Fristfehler dokumentiert, nicht am Test optimiert) | §42 |
| P2.4 Modellvergleich mit Dateiname und 3 Läufen | erledigt: Claude Typ 95, Fristen 75/76, kritisch ≈ 2; OpenAI Typ ≈ 92, Fristen 76/76, kritisch ≈ 0; kaum Streuung; kein belastbarer Sieger; ≈ 2,71 USD | §42 |

**Nächste Schritte (Vorschlag, nicht umgesetzt):**
1. **Fristen-Restfehler** aus `test_v2` als **neue** Dev-Fälle nachbauen:
   - Zeilenumbruch mit vergangenem Ereignis;
   - „Datum:“-Kopfzeile;
   - Fristbeginn über ein Komma hinweg;
   - Termine (12/21);
   - Kündigungsandrohung, Steuerbescheid-Überschätzung.

   Danach einen frischen blinden Split **v3** einmal messen. Der P2.2-Split ist nicht mehr unabhängig.
2. **Owner-Entscheidung:** Soll eine cloudgestützte Fristen-/Typ-**Zweitmeinung** als **Vorschlag** (nie automatisch)
   über einen neuen Gateway-Zweck geprüft werden? Das ist eine Architekturentscheidung.
   - **Belegt:** 16 gegenüber 0–3 kritischen Fristfehlern auf 104 blinden synthetischen Dokumenten.
   - Etwa 5 % der Dokumente (5/104) wären fail-closed ausgeschlossen.
   - Vor einer Entscheidung fehlen eine Messung auf realitätsnäheren, nicht modellgenerierten Texten und eine
     Datenschutz-Folgenabschätzung.
   - **Machbarkeitsentwurf** (nicht umgesetzt): `PRIVACY_REVIEW_ARCHITECTURE.md` §43. Die Cloud darf nur
     ergänzen und markieren, nie entfernen, überschreiben oder bestätigen. Er setzt eine Änderung des
     Grundprinzips „keine Fristenbestimmung in der Cloud“ voraus.
   - **Owner-Entscheidung 15.10.2026:** Die Zweitmeinung wird als manuelle, ergänzende Funktion weiter
     evaluiert, nur Claude. **Stufe 1 erledigt:** netzfreie Grundlage `app/second_opinion/`
     (Antwortvalidierung, Längengrenze ohne Kürzung, Vergleich, Übernahme nur als unbestätigter
     Vorschlag), nicht eingebunden; §43.8.
   - **Nächster Schritt:** Stufe 2 mit Fang-Server im Feature-Branch. Vorher Owner-Freigabe für den
     neuen Gateway-Zweck dort.

## Release-Planung

### v0.2.0 (Quick Wins, nächste Woche nach Pilot)

**Ziel:** Minor-Verbesserungen ohne Architektur-Änderung

#### Feature 1: Dashboard-UI für Qualitätsbewertungen
- **Problem (Pilot-Finding):** Prompt 43 API existiert, aber keine Web-Seite
- **Lösung:** Modale/Card im Draft-Review hinzufügen
  - Bewertungs-Skalen (1–5) mit Slider
  - Kommentar-Textarea
  - Speichern & Statistiken anzeigen
- **Estimated Effort:** 4–6 Stunden
- **Depends on:** Prompt 43 (existiert bereits)

#### Feature 2: E-Mail-Polling-Daemon
- **Problem (Pilot-Finding):** E-Mails nur beim Server-Neustart gepoll
- **Lösung:** Background-Task (Celery? APScheduler?)
  - Alle 5 Min IMAP checken
  - Optional konfigurierbar (Interval, Aktivität)
  - Mit Fehlerbehandlung (Netzwerk-Fehler tollerieren)
- **Estimated Effort:** 6–8 Stunden
- **Architecture:** Neues `app/scheduler/` Modul

#### Feature 3: Setup-Docs für Tesseract — ✅ ERLEDIGT (statt Doku: echte Bündelung)
- **Problem (Pilot-Finding):** Tesseract nicht auf allen Windows-Systemen einfach installierbar
- **Umgesetzte Lösung (weitergehend als ursprünglich geplant):** statt nur Doku wird Tesseract
  jetzt direkt im Windows-Installer mitgeliefert (`windows/vendor/tesseract/`, erzeugt über
  `windows/fetch_tesseract.ps1`, siehe `windows/kanzlei_ai.spec`) – keine manuelle
  Zusatzinstallation mehr nötig. Zusätzlich: verständliche Verfügbarkeitsanzeige auf der
  Systemstatus-Seite (`tesseract_health_check()` in `app/documents/ocr.py`) statt eines erst
  dokumentweise auffallenden Fehlers, für den Fall, dass ein älterer Build ohne Bündel oder eine
  manuelle Portable-Installation ohne den Installer läuft.

#### Feature 4: Log-Rotation
- **Problem (Pilot-Finding):** Logs wachsen unbegrenzt
- **Lösung:**
  - `logging.handlers.RotatingFileHandler` verwenden
  - Config-Option: `LOG_ROTATION_MB` (default 10 MB)
  - `LOG_BACKUP_COUNT` (default 5 alte Files)
- **Estimated Effort:** 1–2 Stunden

**v0.2.0 Release-Datum:** Tag nach Pilot-Abschluss  
**Total Effort:** ~15 Stunden

---

### v0.3.0 (Medium-Term Improvements, +2–3 Wochen nach v0.2.0)

**Ziel:** Stabilität, Performance, erweiterte Features

#### Feature 1: Multi-Kanzlei-Profile vollständig
- **Basis:** Prompt 38 (existiert als Analyse + JSON-Lade-Logik)
- **Erweiterung:**
  - Kanzleiprofil nicht nur in JSON, sondern auch in Datenbank
  - Dashboard-Admin: Profil bearbeiten (Kanzleiname, Klassifikations-Keywords)
  - API für Profil-Abruf
  - Tests für Cross-Profile-Isolation
- **Estimated Effort:** 8–10 Stunden
- **Priority:** Mittel (für 2. Kanzlei relevant)

#### Feature 2: Dokumentvorlagen erweitert
- **Basis:** Prompt 39 (existiert, validierte Platzhalter)
- **Erweiterung:**
  - Template-Verwaltungs-UI (Anlegen, Bearbeiten, Löschen)
  - Template-Vorschau (Rendering mit Test-Werten)
  - Automatische Template-Integration in Draft-Export
  - Mehrsprachige Vorlagen (Deutsch + optional Englisch)
- **Estimated Effort:** 10–12 Stunden

#### Feature 3: Advanced Logging (JSON, Structured)
- **Basis:** Prompt 32 (Logging-Config existiert)
- **Erweiterung:**
  - Structured Logging (JSON-Format optional)
  - ElasticSearch/Splunk-Export möglich
  - Anwalt-freundliche Log-Suche im Dashboard
- **Estimated Effort:** 6–8 Stunden

#### Feature 4: Rate-Limiting erweitert
- **Basis:** Prompt 26 (Basis-Schutz existiert)
- **Erweiterung:**
  - Per-Nutzer Rate-Limits (z. B. max 10 Entwürfe/Stunde)
  - Per-Akte Rate-Limits (Spam-Schutz)
  - Admin-Dashboard: Rate-Limit-Übersicht
- **Estimated Effort:** 4–6 Stunden

**v0.3.0 Release-Datum:** +4 Wochen nach v0.2.0  
**Total Effort:** ~30 Stunden

---

### v0.4.0 (UX Improvements, +1–2 Monate nach v0.3.0)

**Ziel:** Bessere Benutzer-Erfahrung, Schnelligkeit

#### Feature 1: Dashboard-Redesign (Responsive)
- **Problem:** UI funktioniert, ist aber nicht auf Tablets/kleineren Screens optimiert
- **Lösung:**
  - Responsive CSS (Tailwind Core erweitern, oder Bootstrap 5 prüfen)
  - Mobile-friendly Split-Pane Layout
  - Touch-freundliche Buttons
- **Estimated Effort:** 12–16 Stunden
- **Risk:** Möglicherweise neue Abhängigkeit nötig (derzeit nur Jinja2 + HTMX)

#### Feature 2: Bulk-Operationen
- **Problem:** Nur Einzeldokumente verarbeitbar
- **Lösung:**
  - Mehrfach-Auswahl in Inbox
  - "Alle Klassifikationen genehmigen" / "Alle Ablehnen"
  - ZIP-Upload statt Scan-Ordner
- **Estimated Effort:** 8–10 Stunden

#### Feature 3: Advanced Search (Faceted)
- **Problem:** Suche ist einfach (Hybrid), keine Facetten
- **Lösung:**
  - Filter nach Dokumenttyp, Akte, Datum, Status
  - Saved Searches
  - Search-Historik
- **Estimated Effort:** 6–8 Stunden

---

### v1.0 (Security & Stability, +3–6 Monate nach v0.4.0)

**Ziel:** Production-grade Security, Multiple-Deployment-Optionen

#### Feature 1: 2-Factor Authentication (2FA)
- **Status:** Bewusst zurückgestellt (Prompt 26/38)
- **Implementierung:**
  - TOTP (Time-based OTP, Google Authenticator)
  - Backup-Codes
  - Enforcement-Policy (Optional vs. Mandatory)
- **Estimated Effort:** 10–14 Stunden
- **Risk:** User-Support-Aufwand (Passwort vergessen + 2FA)

#### Feature 2: HTTPS/TLS erzwingen
- **Status:** Localhost-Default OK, aber für Netzwerk-Setup nötig
- **Implementierung:**
  - Self-Signed Cert im Installer generieren
  - Oder: Anwalt generiert eigenes Cert
  - HTTPS-Redirect (HTTP → 301 zu HTTPS)
  - HSTS Header
- **Estimated Effort:** 4–6 Stunden
- **Dependency:** Certificate-Verwaltung (z. B. Fernet für Cert-Storage)

#### Feature 3: Windows-Dienst-Registrierung
- **Status:** Nicht im MVP (Prompt 36)
- **Implementierung:**
  - `pywin32` nutzen für Service-Registrierung
  - Installer erweitert (Inno Setup `[Run]` Abschnitt)
  - Auto-Start bei Windows-Boot
  - Crash-Recovery (Service neustartet bei Fehler)
- **Estimated Effort:** 8–10 Stunden
- **Complexity:** Höher (Windows-spezifisch, muss auf echter Windows-Maschine getestet werden)

#### Feature 4: Ollama-Integration (Lokale LLM Alternative)
- **Status:** ⚠️ STALE - dieser Eintrag beschreibt einen längst überholten
  Stand. Tatsächlich umgesetzt (ARCHITECTURE.md §65-§69): `OllamaLocalLLMProvider`,
  Hardware-Erkennung/Modell-Empfehlung, automatischer Installer
  (`LocalAiSetupService`), Anbindung an den Anwendungsstart. Standardmäßig
  AUS (`local_ai_enabled=False`) - real auf CPU-only-Hardware gemessen:
  ~247s pro Anfrage für `qwen3:4b`, für synchrone Chat-Interaktion nicht
  praxistauglich (ARCHITECTURE.md §66/§70). KEIN Fallback-zu-Claude-Verhalten
  wie hier ursprünglich geplant - bewusst das Gegenteil: fail-closed, kein
  Klartext-Fallback bei nicht erreichbarer lokaler KI (Datenschutz vor
  Verfügbarkeit). Offener Folgepunkt: kleineres/schnelleres lokales Modell
  oder asynchrone Verarbeitung, um lokale KI praxistauglich in den
  interaktiven Chat-Pfad zu integrieren (siehe ARCHITECTURE.md §70).

#### Feature 5: macOS Support (Future Consideration)
- **Status:** Bewusst zurückgestellt (Handoff-Punkt 4)
- **Implementierung:**
  - PyInstaller für macOS
  - `.app` Bundle + Code-Signierung
  - Apple-Developer-Account (kosten $99/Jahr)
  - Notarisierung durch Apple (erfordert weitere Schritte)
- **Estimated Effort:** 16–24 Stunden
- **Requirement:** Echter macOS-Entwicklungsrechner
- **Timeline:** Nur wenn echte Kanzlei mit macOS-Arbeitsplätzen ansteht

---

## Backlog: Nicht Priorisiert (Mittelfristig Offen)

### Funktionale Erweiterungen

1. **Automatische Neugenerierung bei schlechter Bewertung**
   - Problem: Anwalt bewertet Entwurf mit 1–2 Stars → sollte neu generieren?
   - Status: OFFEN (siehe ARCHITECTURE.md §47, "Keine automatisierten Auswirkungen")
   - Design-Frage: Trigger & Constraints (zu viele Zyklen? Budget?)

2. **Fine-Tuning Pipeline**
   - Problem: System könnte von Feedback lernen
   - Status: OFFEN (war bewusst ausgeschlossen – keine Auto-Training)
   - Anforderung: Manueller Prozess nötig (Datenvorbereitung, Anthropic API)

3. **Mehrsprachige Oberfläche**
   - Currently: Deutsch + Englisch (Code)
   - Erweiterung: UI-Texte, Hilfe in DE/EN/FR/...
   - Estimated Effort: 6–8 Stunden (Gettext-Integration)

4. **Integration mit Mandanten-Management-Tools**
   - Beispiel: Anwälte nutzen bereits Lexware/DATEV/Mandantentools
   - Status: OFFEN (keine Spezifikation von Pilot-Kanzlei)
   - API-Erweiterung nötig (Export-Formate, Webhooks)

### Infrastruktur & Deployment

1. **Docker-Containerisierung**
   - Vorteil: Einfacher Deployment auf Linux-Servern
   - Use-Case: bezieht sich auf die KanzleiAI-Client-Anwendung selbst
     (weiterhin offen). Der Multi-Kanzlei-Aspekt ist inzwischen TEILWEISE
     erledigt: `gateway/` (ARCHITECTURE.md §70) ist bereits eine
     eigenständige, deploybare FastAPI-Komponente mit
     Kanzlei-Mandantentrennung - noch nicht containerisiert/deployed, aber
     als Code fertig und lokal getestet.
   - Estimated Effort: 4–6 Stunden (Dockerfile + docker-compose)

2. **PostgreSQL-Vollständigkeit**
   - Currently: SQLite MVP-tested, PostgreSQL nur theoretisch
   - Requirement: Echte PostgreSQL-Instanz testen (Migrationen, Alembic)
   - Estimated Effort: 3–4 Stunden (Test-Setup)

3. **Backup-Restore-UI**
   - Currently: Dashboard-Buttons für Backup (v0.1.0), kein Restore
   - Erweiterung: Restore aus ZIP im Dashboard
   - Estimated Effort: 4–6 Stunden
   - Risk: Datenverlust wenn falsch verwendet

---

## Technical Debt & Optimization

### Adressiert (Sollte nicht mehr offen sein)

- [x] `PromptContextBuilder` verdrahten (Prompt 16 → Integration Prompt 42)
- [x] Draft-Feedback-Versionierungs-Bug (Prompt 23 → behoben)
- [x] Klassifikator-Substring-Bug (Prompt 42 → behoben)
- [x] Template-Paths für Installer (Prompt 36 → behoben)

### Noch Offen (Aber nicht blockierend für v0.2.0)

1. **`app/api/schemas.py` ist riesig** (~400 Zeilen)
   - Sollte in mehrere Module aufgeteilt werden
   - Impact: Maintenance (gering)

2. **Einige SQLAlchemy-Queries könnten optimiert sein**
   - z. B. `get_drafts_for_matter()` lädt alle Relationen
   - Lazy-Loading vs. Eager-Loading nicht durchgehend konsistent
   - Impact: Performance bei großen Datenmengen

3. **Error-Handling in `app/intake/` ist repetitiv**
   - Viele `try/except ValueError:` Blöcke
   - Könnte zu Custom-Exception-Hierarchie refaktoriert werden
   - Impact: Code-Qualität (niedrig)

4. **Keine Request-ID-Tracking**
   - Schwierig, einen Request über Logs zu verfolgen
   - Solution: Middleware + contextvars (`request_id` in jedem Log)
   - Impact: Debugging (gering, aber nützlich für Support)

---

## Risk Assessment

### High Risk (Wenn nicht adressiert, könnte v1.0 verhindern)

- **2FA nicht implementiert** → Kann für regulierte Branchen nötig sein
  - Mitigation: Dokumentieren als "Enterprise Feature" (v1.0+)
- **Keine HTTPS** → Nicht production-ready für Netzwerk-Setup
  - Mitigation: v0.4.0 geplant
- **Windows-Dienst fehlt** → Kein richtiger Dauerbetrieb
  - Mitigation: v1.0 geplant, bis dahin "Einzelinstallation"

### Medium Risk (Nice-to-Have vor v1.0)

- **Ollama-Integration nicht getestet** → Könnte für Low-Cost-Kanzleien wichtig sein
  - Mitigation: v1.0 geplant
- **macOS nicht getestet** → Kann später relevant werden
  - Mitigation: Nur wenn Kanzlei macOS nutzt (nicht MVP)

### Low Risk (Kann später adressiert werden)

- **UI auf Tablets nicht optimiert** → Pilot nutzt nur Desktop
  - Mitigation: v0.4.0 (Nice-to-Have)
- **Bulk-Operationen fehlen** → Einzeln-Verarbeitung langsam
  - Mitigation: v0.4.0 (wird erst bei >100 Akten/Monat wichtig)

---

## KPI & Success Metrics für Roadmap

Während Entwicklung nächster Versionen tracken:

| Metrik | Baseline (v0.1.0) | Ziel (v1.0) |
|--------|-------------------|------------|
| Entwurf-Generierung | 7.2s Ø | <5s |
| Dashboard Ladezeit | ~800ms | <500ms |
| Klassifikations-Korrektheit | 72% | >85% |
| Fehlerrate | <1% | <0.5% |
| Support-Tickets | - | <1 pro Woche |
| Cost pro Entwurf | $0.12 | <$0.10 |
| Nutzer-Zufriedenheit | 7.5/10 (geschätzt) | >8.5/10 |

---

**Dieser Roadmap ist kein Versprechen, sondern eine Planung. Prioritäten können sich ändern basierend auf Pilot-Feedback und Anforderungen zusätzlicher Kanzleien.**

**Nächste Überprüfung:** Nach v0.2.0 Release (in 1 Woche)
