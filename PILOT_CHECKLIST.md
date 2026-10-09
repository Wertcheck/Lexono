# PILOT_CHECKLIST.md – Vor-Start-Checkliste (Prompt 44)

Diese Checkliste wird am Vortag des Pilot-Starts durchgearbeitet, um sicherzustellen,
dass alles Notwendige vorbereitet ist.

---

## A. System & Hardware (Tag -1)

- [ ] Windows 10/11 (64-Bit) verfügbar? (Workstation des Anwalts)
- [ ] RAM ≥4 GB installiert? (8 GB empfohlen)
- [ ] Speicher ≥500 MB frei? (für Datenbank + Dokumente)
- [ ] Internet-Konnektivität stabil? (`ping api.anthropic.com` funktioniert)
- [ ] Windows Firewall konfiguriert? (localhost:8000 nicht blockiert)
- [ ] Microsoft Edge WebView2 Runtime vorhanden? (Windows 11 und aktuelle Windows-10-Systeme: ja;
      der Installer richtet sie bei Bedarf ein)

---

## B. Installation & Setup (Tag -1 bis -0.5)

- [ ] `Lexono_Setup.exe` bereitstellen (vom Projektteam; Windows 10/11 64-Bit, keine Admin-Rechte nötig)
- [ ] Installer ausführen:
  - [ ] Installationsordner: `%LocalAppData%\Lexono` (keine Admin-Rechte nötig)
  - [ ] Datenverzeichnis: `C:\ProgramData\Lexono` (entsteht erst beim ersten Start, nicht beim Installieren)
- [ ] Am Ende des Installers „Lexono jetzt starten“ angehakt lassen **oder** später die Verknüpfung
      „Lexono“ im Startmenü verwenden. Beim allerersten Start öffnet sich ein **Konsolenfenster mit dem
      Setup-Assistenten** – dieses Fenster nicht schließen (siehe C).
- [ ] Startmenü-Eintrag „Lexono“ vorhanden (Desktop-Verknüpfung nur, wenn im Installer die Option
      „Desktop-Verknüpfung anlegen“ gewählt wurde)

---

## C. First-Run Setup-Assistent (Tag 0, Morning)

- [ ] Lexono über das Startmenü („Lexono“) starten – ohne vorhandene Daten öffnet sich der Setup-Assistent
      in einem Konsolenfenster. Alternativ (z. B. zur Fehlersuche) in PowerShell:
      `cd "%LocalAppData%\Lexono"` und `.\Lexono.exe serve`
- [ ] Assistent-Fragen beantworten:
  - [ ] Admin-E-Mail eingeben: `anwalt@kanzlei.local` (Beispiel)
  - [ ] Admin-Passwort eingeben (oder leer lassen → Zufallspasswort generiert und einmalig angezeigt)
  - [ ] Frage „Lokale KI (Ollama) jetzt automatisch einrichten? [J/n]“: mit Enter bestätigen. Lexono erkennt
        die Hardware, wählt ein passendes Modell und lädt es bei Bedarf herunter (je nach Rechner und
        Internetverbindung von unter einer Minute bis über eine Stunde; das Fenster zeigt in Abständen
        „Einrichtung läuft noch …“ – das ist kein Fehler). Schlägt der Schritt fehl, ist die Installation
        trotzdem nutzbar; Wiederholung: `Lexono.exe local-ai-setup`
  - [ ] SESSION_SECRET_KEY wird automatisch generiert ✓
  - [ ] `.env` unter `C:\ProgramData\Lexono\.env` geschrieben ✓
  - [ ] Migration (alembic upgrade head) läuft durch ✓
  - [ ] Admin-Nutzer angelegt ✓
  - [ ] Server startet unter `http://127.0.0.1:8000`; ein natives Lexono-Fenster öffnet sich mit der
        Anmeldeseite (jeder weitere Start läuft ohne sichtbares Konsolenfenster, Protokoll in
        `C:\ProgramData\Lexono\app.log`)

---

## D. Dashboard-Login & Passwort-Änderung (Tag 0)

- [ ] Im Lexono-Fenster anmelden (alternativ im Browser: `http://127.0.0.1:8000/dashboard`)
- [ ] Login mit Admin-E-Mail + (generiertem) Passwort
- [ ] Dashboard lädt → Inbox, Akten, etc. sichtbar
- [ ] Passwort-Änderungs-Dialog (erzwungen beim ersten Login)
- [ ] Neues Admin-Passwort setzen (sicher!)
- [ ] Nochmal Login mit neuem Passwort prüfen

---

## E. Konfiguration (Tag 0, Afternoon)

### E1: Scan-Ordner

- [ ] Scan-Eingabe-Ordner prüfen: `C:\ProgramData\Lexono\data\intake`
- [ ] Testdatei (PDF) ablegen → System sollte aufnehmen
- [ ] Prüfen: Inbox zeigt neue Einträge ✓

### E2: Claude-API-Key (kritisch!)

- [ ] `.env`-Datei öffnen: `C:\ProgramData\Lexono\.env` (im Windows-Editor)
- [ ] `ANTHROPIC_API_KEY` eintragen bzw. prüfen. Der Setup-Assistent fragt den Claude-API-Schlüssel **nicht** ab. Er wird einmalig von der betreuenden
      Person in die Datei `C:\ProgramData\Lexono\.env` eingetragen (Zeile `ANTHROPIC_API_KEY="…"`),
      danach Lexono beenden und neu starten. Der Schlüssel ist in der Oberfläche nicht einsehbar oder
      änderbar und gehört nie in E-Mails, Chats oder Screenshots.
- [ ] Falls leer oder fehlerhaft:
  - [ ] Anthropic-Dashboard öffnen (https://console.anthropic.com)
  - [ ] API-Key erzeugen/kopieren
  - [ ] In `.env` eintragen: `ANTHROPIC_API_KEY=sk_...`
  - [ ] Lexono beenden und über das Startmenü neu starten
- [ ] Test: In der Seitenleiste unten die Statusanzeigen „Cloud-KI“ und „Lokale KI“ prüfen. „Cloud-KI (Gateway)“ zeigt „Bereit“
      nur, wenn die Cloud-Adresse nachweislich erreichbar ist (Prüfung alle ca. 45 s, ohne Anfrage und ohne
      Schlüsselnutzung); sonst „nicht konfiguriert“, „wird geprüft…“, „nicht erreichbar“ (Tooltip nennt die
      Ursache, z. B. Zeitüberschreitung) oder „Anfrage fehlgeschlagen“ (letzte echte Anfrage endete mit einem
      Anbieterfehler, z. B. Schlüssel/Guthaben). Ein erster
      Chat („Was ist der Unterschied zwischen Besitz und Eigentum?“) muss eine Antwort liefern

### E3: E-Mail-Integration (Optional)

- [ ] Falls E-Mail-Ingestion gewünscht:
  - [ ] Dashboard → Einstellungen → Tab „E-Mail“
  - [ ] IMAP-Server, Benutzername, Passwort eingeben
  - [ ] "Test-Verbindung" klicken
- [ ] Falls nicht gewünscht → überspringen (nicht erforderlich für Pilot-Erfolg)

### E4: Logging (Optional)

- [ ] Protokolle liegen unter `C:\ProgramData\Lexono\logs\kanzlei_ai.log` (vom Setup-Assistenten gesetzt,
      `LOG_FILE_PATH` in der `.env`) und `C:\ProgramData\Lexono\app.log` (Startprotokoll)
- [ ] Log-Level `INFO` (Standard) belassen; blockierte KI-Anfragen erscheinen als
      „Anfrage/Antwort blockiert (Platzhalter: …)“ – nie mit Klartextwerten

---

## F. Benutzer & Rollen (Tag 0, Evening)

- [ ] Ggf. zusätzliche Anwälte hinzufügen (falls mehrere im Pilot):
  - [ ] Dashboard → Einstellungen → Tab „Benutzer“
  - [ ] Neuen Benutzer anlegen: E-Mail + Rolle (Admin / Anwalt / Mitarbeiter) → Speichern
  - [ ] Das initiale Passwort wird **einmalig** auf dem Bildschirm angezeigt (es wird nicht per E-Mail
        versendet) – sicher an den Benutzer weitergeben; er muss es beim ersten Login ändern
- [ ] Falls nur ein Anwalt: Admin-Account reicht aus

---

## G. Testlauf (Tag 0, Evening oder Tag 1 Morning)

**G0. Chat-Schnelltest (zuerst, ca. 10 Minuten, nur synthetische Daten):**

- [ ] Neuer Chat: „Was ist der Unterschied zwischen Besitz und Eigentum?“ → Antwort erscheint, kein
      Schriftsatz-Panel
- [ ] Mandant und Akte anlegen (Mandanten → Neuer Mandant; Akten → Neue Akte), „Chat zu dieser Akte
      starten“, ein synthetisches PDF anhängen, „Fasse das Dokument zusammen.“ → Zusammenfassung; das
      Dokument erscheint in der Akte (Dokumente)
- [ ] „Erstelle ein Schreiben an die Gegenseite …“ → Schriftsatz-Panel mit Kanzlei-Briefkopf und Unterzeichner
      (aus Einstellungen → Kanzlei; mehrere Briefköpfe: Einstellungen → Kanzlei → Briefköpfe verwalten),
      Prüfpunkte stehen **außerhalb** des Schreibens
- [ ] „Im Editor öffnen“ → Entwurf bearbeiten; Kopieren im Chat; Export DOCX/PDF aus der Entwurfsansicht
- [ ] Lexono beenden und neu starten → Chat, Akte und Entwurf sind unverändert vorhanden
- [ ] Hinweis: Dokumentanalyse braucht die lokale KI. Ist sie nicht erreichbar, erscheint ein klarer
      Hinweis und es wird nichts an die Cloud gesendet.

**G1. Intake-Workflow (Scan-Ordner):**

- [ ] Kompletter Mini-Workflow durchspielen:
  - [ ] Test-PDF in Scan-Ordner legen
  - [ ] Intake durchlaufen (Inbox zeigt Eintrag)
  - [ ] Klassifikation prüfen (manuell korrekt?)
  - [ ] Aktenzuordnung prüfen (existierende Akte oder neue?)
  - [ ] "Entwurf generieren" klicken
    - [ ] Claude API wird aufgerufen (dauert ~2–5 Sekunden)
    - [ ] Entwurf erscheint in Überprüfungs-Pane
    - [ ] Review-Findings prüfen (Rechtsquellen, offene Punkte)
  - [ ] Entwurf genehmigen oder mit Anmerkung zurückgeben
  - [ ] Status auf "approved" prüfen

- [ ] Wenn alle Schritte funktionieren → **GO für Pilot-Start!**
- [ ] Wenn Fehler auftreten:
  - [ ] Logs prüfen (`C:\ProgramData\Lexono\logs\kanzlei_ai.log`)
  - [ ] Typische Fehler?
    - [ ] Claude API nicht konfiguriert → Key nochmal prüfen
    - [ ] OCR schlägt fehl → Tesseract nicht installiert (Optional für Pilot)
    - [ ] Datenbank-Fehler → Support anfordern
  - [ ] Fehler beheben vor Pilot-Start

---

## H. Backups & Notfall-Plan (Tag 0, Night)

- [ ] Backup des gesamten Installationsordners machen:
  - [ ] `%LocalAppData%\Lexono` → externe Festplatte kopieren
- [ ] Backup des Datenverzeichnisses machen (Lexono dafür beenden oder das Dashboard-Backup nutzen):
  - [ ] `C:\ProgramData\Lexono` → externe Festplatte kopieren
  - [ ] Alternativ: Dashboard → Backup (vollständige Sicherung als ZIP)
- [ ] Notfall-Kontakt festlegen (falls Fragen während Pilot):
  - [ ] Entwickler-Kontakt (E-Mail/Chat)
  - [ ] Falls nicht verfügbar → Support-Dokumentation (PILOT_PLAYBOOK.md)

---

## I. Anwalt-Einweisung (Tag 1, Morning)

- [ ] Anwalt sitzt am System
- [ ] **Walkthrough (30–60 Min):**
  - [ ] Dashboard-Navigation (Inbox → Akten → Entwürfe)
  - [ ] Scan-Ordner (wohin Dokumente legen)
  - [ ] Entwurf-Workflow (Klassifikation → Entwurf → Review → Freigabe)
  - [ ] Feedback-Sammlung (wie Erfahrungen notieren)
  - [ ] Notfall-Kontakt (wen anrufen bei Problemen)
- [ ] Anwalt testet einen echten Workflow alleine
- [ ] Fragen klären

---

## J. Go/No-Go Entscheidung (Tag 1, Noon)

- [ ] **Checkliste A–I vollständig?**
  - [ ] JA → Alle Häkchen gesetzt? → **GO für Pilot-Start**
  - [ ] NEIN → Fehler beheben, dann erneut prüfen

- [ ] **System-Funktionalität:**
  - [ ] Dashboard lädt ohne Fehler
  - [ ] Claude API funktioniert (Test-Entwurf generiert)
  - [ ] Logs zeigen keine kritischen Fehler
  - [ ] Backup vorhanden

- [ ] **Anwalt bereit:**
  - [ ] Einweisung erhalten + verstanden
  - [ ] Erste echte Aufgaben bereitet vor (3–5 Akten zum Verarbeiten)
  - [ ] Feedback-Prozess klar (tägliche Notizen, wöchentliche Reviews)

---

## K. Pilot-Start! (Tag 1, PM oder Tag 2, AM)

- [ ] **Server läuft kontinuierlich oder nur bei Bedarf:**
  - [ ] Empfehlung: Server morgens starten, abends stoppen (für Backup)
  - [ ] Alternative: Im Hintergrund laufen lassen (braucht Monitoring)

- [ ] **Anwalt beginnt mit echten Workflows**
  - [ ] 10 Akten zu verarbeiten (mind. über 2–4 Wochen)
  - [ ] 5+ Entwürfe zu generieren
  - [ ] Täglich Feedback notieren

- [ ] **Entwicklung in Bereitschaft:**
  - [ ] Kontakt prüfen regelmäßig (tägliche Status-Updates bei Bedarf)
  - [ ] Logs von Anwalt sammeln (Ende jeder Woche)
  - [ ] Kritische Fehler: Sofort eskalieren

---

## L. Wöchentliche Checkpoints (Wochen 1–4)

**Jede Woche:**

- [ ] Montag 9 AM: Feedback-Call mit Anwalt (15 Min)
  - [ ] Wie läuft's? Bugs aufgetaucht?
  - [ ] Woran arbeitet er diese Woche?
- [ ] Donnerstag Abend: Backup machen (Dashboard → Backup-Button oder manuell)
- [ ] Freitag 5 PM: Logs sammeln & Statistiken aus Dashboard exportieren
  - [ ] Systemstatus (API-Aufrufe, Kosten, Fehler)

---

## M. Pilot-Abschluss-Vorbereitung (Tag 25–28)

- [ ] Wurden Erfolgskriterien erreicht? (Siehe PILOT_PLAYBOOK.md §5.1)
  - [ ] ≥10 Akten: __ von 10 (Zahl eintragen)
  - [ ] ≥5 Entwürfe: __ von 5 (Zahl eintragen)
  - [ ] Keine kritischen DB-Fehler: ✓/✗
  - [ ] Claude API funktioniert: ✓/✗
  - [ ] Klassifikation >50% korrekt: ✓/✗ (Anwalt-Einschätzung)
  - [ ] Keine Versand-Fehler: ✓/✗
  - [ ] Audit-Trail nachvollziehbar: ✓/✗

- [ ] Quantitatives Feedback sammeln:
  - [ ] Logs exportieren (Gesamt-Statistiken)
  - [ ] Qualitätsbewertungen exportieren (Prompt 43)
  - [ ] Anwalts-Notizen kompilieren

- [ ] Qualitatives Feedback:
  - [ ] "Was hat gut funktioniert?"
  - [ ] "Was war frustrierend?"
  - [ ] "Fehlerhafte Szenarien notieren"

- [ ] Pilot-Report schreiben (für Prompt 45)

---

**✅ Nach Abschluss dieser Checkliste → Pilot kann beginnen!**

**Probleme während Pilot → PILOT_PLAYBOOK.md, Abschnitt "Fehlerbehandlung"**

**Nach Pilot-Ende → PILOT_PLAYBOOK.md, Abschnitt "Übergabe an Prompt 45"**
