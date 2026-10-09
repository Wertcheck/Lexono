# PILOT_SUPPORT.md – Support-Kurzanleitung und Clean-Room-Testmatrix (begleiteter Pilot)

Für die betreuende Person (nicht für Kanzleimitarbeitende). Ergänzt `PILOT_PLAYBOOK.md` und
`PILOT_CHECKLIST.md`. Stand: 09.10.2026. Alle Angaben wurden gegen den installierten Build bzw. den
Code geprüft; nicht Geprüftes ist als **NICHT VERIFIZIERT** markiert.

## 1. Grundregeln für Support und Diagnose

- **Keine API-Schlüssel, Tokens oder Passwörter** in E-Mails, Tickets, Chats, Screenshots oder Logs
  weitergeben. Die Datei `C:\ProgramData\Lexono\.env` wird **nie** verschickt.
- **Keine Mandantendaten** in Supportmaterial. Für Fehlerbeschreibungen genügen Uhrzeit, Meldungstext
  und Schritte; Fehler nach Möglichkeit mit **synthetischen** Daten nachstellen.
- **Keine destruktiven Diagnosen auf produktiven Daten** (kein Löschen, kein Zurücksetzen, keine
  Test-Uploads in echte Akten). Nachstellen nur in einer getrennten Testumgebung: eigenes
  Datenverzeichnis (`LEXONO_DATA_DIR=<leerer Testordner>`) bzw. eigene Datenbank (`DATABASE_URL`),
  Lexono der Kanzlei vorher beenden (es läuft immer nur eine Instanz).
- **Vor jedem Eingriff ein Backup** (Dashboard → Backup).

## 2. Wo steht was?

| Was | Wo |
|---|---|
| Programm | `%LocalAppData%\Lexono\Lexono.exe` |
| Daten (Datenbank, Dokumente, `.env`) | `C:\ProgramData\Lexono` |
| Anwendungslog | `C:\ProgramData\Lexono\logs\kanzlei_ai.log` (Dateiname bewusst unverändert) |
| Startausgabe | `C:\ProgramData\Lexono\app.log` |
| Erreichbarkeit | `http://127.0.0.1:<PORT>/health` → `{"status":"ok"}` (PORT steht in `.env`) |
| Start mit sichtbarer Konsole (Fehlersuche) | `Lexono.exe serve --no-window` |

Die Logs enthalten laut Prüfung im installierten Build **keine** Schlüssel, Chat-Inhalte oder
Fehlerantworten der Cloud, sondern nur feste Kategorien (z. B. `AuthenticationError (HTTP-Status: 401)`,
`Cloud-KI-Status: unreachable (Verbindung abgelehnt)`). Vor dem Weitergeben trotzdem durchsehen.

## 3. Vorgehen je Störung

### 3.1 Start
1. `/health` aufrufen. Antwortet es nicht: läuft `Lexono.exe` (Task-Manager)? Zweiter Start wird wegen
   Einzelinstanz abgewiesen.
2. Mit `Lexono.exe serve --no-window` in einer Konsole starten und die Ausgabe lesen; dazu
   `app.log` und das Ende von `kanzlei_ai.log`.
3. Fenster bleibt leer / WebView2-Meldung: Edge-WebView2-Runtime fehlt oder ist defekt (siehe README).
4. Datenbankmigrationen laufen beim Start automatisch; manuell: `Lexono.exe migrate`.
5. Nichts löschen. Bei defekten Daten: Backup einspielen mit `Lexono.exe restore --archive <Datei>`
   (Lexono vorher beenden).

### 3.2 Lokale KI / Ollama
- Sidebar „Lokale KI“ und Logzeile `Lokale KI bereit (Modell '…')` bzw. die Fehlermeldung im Chat:
  *„Die lokale KI (Ollama) ist gerade nicht verfügbar … nichts an die Cloud gesendet …“*.
- Ollama läuft nicht: Lexono startet es beim Start selbst; sonst `Lexono.exe local-ai-setup`
  (startet ein installiertes, gestopptes Ollama und lädt das Modell nach; Download kann lange dauern).
- Zu wenig Arbeitsspeicher: andere Programme schließen, erneut versuchen.
- Wichtig: Ohne lokale KI läuft **keine** Dokumentanalyse und es geht **nichts** in die Cloud
  (Fail-closed). Allgemeine Chat-Fragen funktionieren weiter.

### 3.3 Cloud
Sidebar „Cloud-KI (Gateway)“ (Prüfung ca. alle 45 s, ohne Anfrage und ohne Schlüssel):

| Anzeige | Bedeutung | Maßnahme |
|---|---|---|
| nicht konfiguriert | kein Schlüssel / keine Gateway-Adresse | Schlüssel in `.env` eintragen (`ANTHROPIC_API_KEY="…"`), Neustart |
| wird geprüft… | kurz nach dem Start | warten |
| nicht erreichbar (Tooltip nennt die Ursache) | Netz/Firewall/Proxy/DNS/Zertifikat | Internetverbindung, Firewall, Proxy prüfen |
| Anfrage fehlgeschlagen | Adresse erreichbar, letzte echte Anfrage wurde vom Anbieter abgelehnt | Schlüssel/Guthaben/Gateway-Zugang prüfen; verschwindet nach der nächsten erfolgreichen Anfrage |
| Bereit | Adresse erreichbar und letzte Anfrage ohne Fehler | – |

Chat-Meldung bei Anbieterfehlern: *„Die Antwort konnte aus technischen Gründen nicht erzeugt werden.
Es handelt sich nicht um eine Datenschutz-Blockierung.“*

### 3.4 Privacy-Blockaden
- Blockaden sind **gewollt** (Fail-closed) und dürfen nie durch Abschwächen der Prüfung umgangen werden.
- Meldungen unterscheiden: *„… nicht ausreichend anonymisierten Wert … blockiert – kein Text wurde
  übernommen“*, *„unerwarteten Platzhalter … nicht übernommen“* (Formulierungsfehler der KI – erneuter
  Versuch genügt meist) und *„…aus Datenschutzgründen blockiert“*.
- Erfassen: Uhrzeit, Meldungstext, Art der Anfrage (Chat/Schriftsatz/Dokument) – **nicht** den
  Dokumentinhalt. Mit synthetischem Dokument nachstellen und der Entwicklung melden.
- Tritt eine Blockade wiederholt bei demselben Dokumenttyp auf, ist das ein Fehlerfall für die
  Entwicklung, kein Grund, Schutzmechanismen zu deaktivieren.

## 4. Diagnosedaten sicher weitergeben

1. Lexono beenden oder nur lesend vorgehen; nichts verändern.
2. Nur die **Textlogs** kopieren (`kanzlei_ai.log`, `app.log`), nicht `.env`, nicht die Datenbank,
   nicht den Dokumentordner, keine Backups.
3. Vor dem Versand durchsehen und Namen, Aktenzeichen, Adressen, E-Mail-Adressen schwärzen; nach
   `sk-`/`lxg_`/`ANTHROPIC` suchen (darf nicht vorkommen).
4. Zusätzlich angeben: Lexono-Version, Windows-Version, Uhrzeit, Meldungstext, Ergebnis von `/health`.
5. Übermittlung nur über den vereinbarten, verschlüsselten Kanal.

## 5. Clean-Room-Testmatrix (frische Windows-VM / Testrechner)

**Stand: NICHT VERIFIZIERT.** Am 09.10.2026 stand keine frische Windows-VM und kein Testrechner zur
Verfügung (Entwicklungsrechner: Windows 11 Pro, kein Administratorkonto in der Sitzung, keine VM-Software
und kein Windows Sandbox vorhanden). Eine ungeprüfte Umgebung wurde bewusst nicht aufgebaut.

**Tatsächlich geprüft (Entwicklungsrechner, nicht frisch):** Installation per Silent-Install,
Erststart mit leerem Datenverzeichnis (`LEXONO_DATA_DIR`), Admin-Anlage mit erzwungenem Passwortwechsel,
Migrationen und Neustart, Ollama installiert+läuft, Ollama installiert+gestoppt, Ollama nicht erreichbar,
Download-Fehler.

**Noch offen – auf einer frischen Windows-10/11-VM ohne Entwicklerwerkzeuge, ohne Python, ohne Ollama:**

| # | Schritt | Erwartung | Ergebnis |
|---|---|---|---|
| C1 | Snapshot der leeren VM anlegen; `Lexono_Setup.exe` (SHA-256 notieren) installieren | Installation ohne Adminrechte nach `%LocalAppData%\Lexono`; Startmenüeintrag | offen |
| C2 | „Lexono jetzt starten“: erster Start | Konsole des Setup-Assistenten erscheint | offen |
| C3 | Assistent: Admin-E-Mail/Passwort eingeben, lokale KI **Ja** | Admin angelegt, Migrationen laufen, `.setup_complete` entsteht | offen |
| C4 | Pfad **ohne Ollama**: Ollama-Installation und Modell-Download (Dauer, Fortschritt, Fehlertexte) | Ollama wird eingerichtet, Modell geladen, „einsatzbereit“ | offen |
| C5 | Wie C4, aber Netz trennen während des Downloads | klare Fehlermeldung, Lexono startet trotzdem | offen |
| C6 | Fenster öffnet sich; Anmeldung; erzwungener Passwortwechsel | Wechsel Pflicht, danach Dashboard | offen |
| C7 | API-Schlüssel manuell in `C:\ProgramData\Lexono\.env`, Neustart | Sidebar Cloud-KI „Bereit“ | offen |
| C8 | Chat-Schnelltest (Checkliste G0) mit synthetischen Daten | alle Punkte erfüllt | offen |
| C9 | Lexono beenden, neu starten; Windows neu starten | Daten vorhanden, Ollama wird gestartet | offen |
| C10 | Deinstallation | `C:\ProgramData\Lexono` bleibt erhalten | offen |
| C11 | Zweiter Durchlauf: Ollama vorab installiert und laufend | Wiederverwendung, kein Neudownload der Runtime | offen |
| C12 | Sicherheitssoftware (Windows Defender/SmartScreen) beim Start und Download des unsignierten Installers beobachten | Warnungen dokumentieren | offen |

Eine Pilotfreigabe darf erst als vollständig verifiziert gelten, wenn C1–C11 durchlaufen und
dokumentiert sind.
