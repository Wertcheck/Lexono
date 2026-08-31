# FUTURE_ROADMAP.md – Priorisierte Erweiterungen nach v0.1.0

**Stand:** Prompt 45 Abschluss  
**Basis-Version:** v0.1.0 (MVP, Pilot-fertig)  
**Dieses Dokument:** Planung für v0.2.0–v1.0 (2–6 Monate)

---

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
