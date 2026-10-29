# Kontrolliertes Update des Kanzlei-Systems auf den Kanzlei-Piloten (29.10.2026)

**Status:** Update durchgeführt (Owner-Freigabe vom 29.10.2026). Alle Sicherheitsprüfungen sind bestanden, kein Abbruch
war nötig.

- **NICHT freigegeben:** keine Datenschutz-, P0-08-, C-14- oder Pilotfreigabe.
- **Testdaten:** ausschließlich synthetisch; keine echten Mandanteninhalte an Testdienste gesendet.

## 1. Vorabprüfungen

| Prüfung | Ergebnis |
|---|---|
| Installer vorhanden | `dist\installer\Lexono_Setup.exe` |
| SHA-256 | `142713C8F065B90342680955DCC11047C20912167FF03AD417BDBD90315C9213` (= freigegeben) |
| Version | 0.1.0 (Produktversion des Installers) |
| Quellstand | Branch `feature/feedback-diagnose`, HEAD `e127fcd`; App-Paket aus `3d3e611`, Installer-Skript aus `01ffd98`, keine Änderung an App/Installer seitdem |
| Paket `dist\Lexono` | `Lexono.exe` `D9DF6196…7B8C8879`, Profil `kanzlei-pilot`, keine Finance-Laufzeitkomponenten/-Templates, Migrationen 040–042 enthalten (`bundle_check_vor_update.json`) |
| Lexono beendet | kein `Lexono.exe`-Prozess, kein Entwicklungsserver auf den Produktivdaten |

## 2. Backup (vollständig, verifiziert)

**Speicherort:** `C:\Users\Bonit\LexonoBackups\vor-update-01ffd98-20261029-1726\`. Er liegt bewusst außerhalb des
Repositorys und enthält vertrauliche Daten. Die Wiederherstellungsschritte stehen dort in `WIEDERHERSTELLUNG.md`.
Prüfbericht: `backup_bericht_zusammenfassung.json`.

| Gesichert | Dateien | Ergebnis |
|---|---|---|
| `C:\ProgramData\Lexono` (DB, Dokumente, `.env`, `.setup_complete`, Logs, lokale KI-Downloads) | 957 (1,97 GB) | alle SHA-256 identisch |
| `C:\ProgramData\KanzleiAI` (Altverzeichnis) | 1 | identisch |
| `%LOCALAPPDATA%\Lexono` (Programm 7ce54d7) | 1027 (1,28 GB) | identisch |
| Datenbank zusätzlich über die SQLite-Backup-API | – | Revision `schritt3_042`, `integrity_check` ok, 59 Tabellen, 15 959 Zeilen; Inhalts-Hash von Quelle, Rohkopie und API-Kopie identisch |
| Vorheriger Installer 7ce54d7 (`197C55A5…`) | – | im Backup-Ordner |

- **Lizenzdaten:** Sie liegen in der Datenbank (`module_licenses`) und sind damit gesichert.
- **WebView2-Profil:** Es liegt nicht im Programmordner; der Installer berührt es nicht.

## 3. Installation

Ausgeführt mit `Lexono_Setup.exe /SILENT /SUPPRESSMSGBOXES /NORESTART /LOG`: sichtbarer Fortschritt, keine Abfragen.
Exitcode 0, Dauer 75 s. Auszug aus dem Protokoll: `installer_log_auszug.txt`.

**Vorhandene Installation:**

- **Zielordner:** Es wurde die bestehende Installation `%LOCALAPPDATA%\Lexono` verwendet, mit Anhang an das bestehende
  Uninstall-Log.
- **Vor dem Kopieren geleert:** `_internal\app` und `_internal\migrations\versions` (gewünscht, siehe `01ffd98`). Danach
  wurden 1027 Dateien kopiert.

**Registry** (`registry_vorher.json` / `registry_nachher.json`):

- Uninstall-Schlüssel unter derselben AppId, neu geschrieben.
- DisplayVersion 0.1.0, InstallLocation unverändert, InstallDate 20261028 → 20261029, Aufgabe `desktopicon` beibehalten.
- Es ist weiterhin genau ein Lexono-Uninstall-Eintrag vorhanden.

**Verknüpfungen:** Desktop sowie Startmenü (Lexono, Lexono deinstallieren) sind neu erstellt und zeigen unverändert auf
`wscript.exe "…\Lexono\Start.vbs"`.

**WebView2:**

- **Runtime:** 156.0.4314.50 ist installiert.
- **Bootstrapper:** Er endete mit `0x80040C28`. Die Installation war davon nicht betroffen („Installation process
  succeeded“ vor diesem Schritt). Die App lief danach im nativen WebView2-Fenster (Abschnitt 4c).
- **Grenze:** Die genaue Bedeutung des Codes ist nicht geprüft; vermutlich bedeutet er, dass die Runtime bereits
  vorhanden ist.

**Datenverlust:** keiner.

- **Programmordner:** `Lexono.exe` `28F9C043…` → `D9DF6196…`, Dateien 1027 → 1025. Die zwei entfernten Dateien sind die
  Finance-Altlast `fibu.html` und verwaiste Templates.
- **Produktive DB, `.env`, `.setup_complete`:** unmittelbar nach der Installation **byte-identisch**
  (`fingerprint_vor_install.json` / `fingerprint_nach_install.json`).

## 4. Nachprüfung in der tatsächlich installierten Anwendung

### 4a. Mit den echten Daten – nur lesend, ohne Anmeldung (`prod_postcheck.json`)

Die installierte `Lexono.exe` startete ohne Fenster mit dem Standard-Datenverzeichnis und wurde danach beendet.

| Prüfung | Ergebnis |
|---|---|
| Start | ok in 3,5 s, 0 Tracebacks |
| Migration beim Start | keine nötig; Revision `schritt3_042` → `schritt3_042` |
| Integrität nach dem Start | `integrity_check` ok |
| Datenumfang | 15 959 → 15 959 Zeilen, keine Tabelle verändert (auch Unterhaltungen, Dokumente, Benutzer, Lizenzen) |
| Konfiguration | `.env` unverändert (Hash) |
| Login-Seite | 200 |
| Finance `/dashboard/fibu`, `fibu.css` | 404 / 404 |
| Ausgelieferte Assets | Pseudonymisierungsfix und Lizenzseiten-Stile vorhanden |

Nach allen Prüfungen ist die produktive DB **byte-identisch** zum Stand vor der Installation (`fingerprint_final.json`).

**Bewusst nicht mit den echten Daten geprüft:**

- **Anmeldung mit einem Kanzlei-Konto:** Ich habe keine Zugangsdaten. Echte Unterhaltungen und Dokumente hätte ich
  außerdem nicht ansehen oder fotografieren dürfen. Ihr Erhalt ist über Zeilenzahlen, Inhalts-Hashes und Datei-Hashes
  belegt.
- **Workflows:** Sie hätten Testdaten in die produktive Datenbank geschrieben; das wird in 4c geprüft.

### 4b. Cloud-Verbindung und Statusanzeige – getrennt, ohne Inhalte (`prod_cloud_status.json`)

- **Konfiguration:** direkter Anthropic-Zugang (Schlüssel gesetzt, kein Gateway). Werte wurden nicht ausgegeben.
- **Erreichbarkeit** von `api.anthropic.com:443` (nur TCP/TLS-Handshake): Der erste Versuch endete mit einer
  Zeitüberschreitung (einmaliger Ausreißer), alle 5 Wiederholungen waren erreichbar (0,11–0,25 s).
- **Aufrufprotokoll:** Die letzte Anfrage ist nicht fehlgeschlagen. Die von der App berechnete Statusanzeige lautet
  `reachable`, Route `direct`.
- **Grenze:** Eine echte Anfrage an die Cloud-KI wurde nicht gesendet; das wäre ein Versand von Inhalten.

### 4c. Workflows mit der installierten EXE, isoliertes Datenverzeichnis (`installierte-exe-workflows/`)

**Rahmen:**

- **Programm und Fenster:** die tatsächlich installierte `%LOCALAPPDATA%\Lexono\Lexono.exe` im eigenen
  WebView2-Fenster, mit eigenem WebView2-Profil im Job-Ordner.
- **Daten:** eigenes Datenverzeichnis, angelegt von der installierten EXE (`migrate`, `create-admin`), synthetisches
  PDF.
- **Erfolgsweg:** offizieller lokaler Prüfendpunkt (Loopback, ungültiger Platzhalterschlüssel, 12 s Verzögerung, kein
  Netz).

Ergebnis: **16/16 Prüfungen bestanden**.

| Prüfung | Ergebnis |
|---|---|
| Lizenzseite: Lexono Verfügbar/„Aktiv genutzt“, Finance und Finance & Tax „In Entwicklung“/„Bald verfügbar“, Modul nur `kanzlei`, kein Querscroll | bestanden |
| Pseudonymisierungsdialog beim Erstversuch bedienbar, alle drei Optionen aktiv, erreichbar und wählbar | bestanden |
| Abbrechen: kein Auftrag, kein KI-Aufruf, Eingabe erhalten | bestanden |
| Erneut senden: genau 1 Auftrag | bestanden |
| Mehrfachklickschutz: 3 Klicks auf Bestätigen = genau 1 KI-Aufruf | bestanden |
| Sichtbarer Ladezustand mit echten Phasen | bestanden |
| Schriftsatzerstellung: Ergebnisblock mit „Offene Fragen“ | bestanden |
| Anfrage pseudonymisiert (Testname nicht in der Anfrage) | bestanden |
| Erstversuch direkt bestätigt: Erfolg, genau ein weiterer KI-Aufruf | bestanden |
| Fehlerbehandlung (Prüfendpunkt gestoppt): verständliche Meldung, keine hängende Anzeige | bestanden |
| Tracebacks | 0 |

## 5. Vergleich vorher/nachher

| Bereich | Vorher | Nachher |
|---|---|---|
| Programm | 7ce54d7 (`28F9C043…`), ohne Build-Profil, Finance-Router im Paket (lizenzgesperrt) | 01ffd98-Installer / 3d3e611-Paket (`D9DF6196…`), Profil `kanzlei-pilot`, kein Finance-Code |
| Produktive DB | Revision 042, 15 959 Zeilen, `9F0B1D44…` | Revision 042, 15 959 Zeilen, byte-identisch |
| `.env` / `.setup_complete` | – | byte-identisch |
| Registry | 1 Uninstall-Eintrag, InstallDate 20261028 | 1 Uninstall-Eintrag, InstallDate 20261029 |
| Verknüpfungen | Desktop + Startmenü → `Start.vbs` | unverändert → `Start.vbs` (neu erstellt) |
| Laufender Prozess | keiner | keiner (die App wird beim nächsten normalen Start über die Verknüpfung geöffnet) |

## 6. Offene Risiken

1. **Echte Cloud-KI:** Nach dem Update ist sie nicht mit einer echten Anfrage geprüft (bewusst, kein Versand). Die erste
   echte Anfrage im Betrieb ist der erste End-to-End-Nachweis gegen Anthropic.
2. **Anmeldung und Ansicht:** Mit echten Konten und Daten ist das nicht durchgeführt. Die Datenintegrität ist über
   Hashes und Zeilenzahlen belegt, eine Sichtprüfung durch die Kanzlei steht aus.
3. **WebView2-Bootstrapper `0x80040C28`:** Die Bedeutung ist nicht abschließend geprüft. Die Runtime ist vorhanden und
   funktioniert.
4. **Einmalige Zeitüberschreitung** beim ersten Erreichbarkeitstest: danach stabil.
5. **Rücksprung:** Ist er nötig, gelten die Schritte in `WIEDERHERSTELLUNG.md` im Backup-Ordner. Es gab keine eigenmächtige
   Neuinstallation oder Datenbankrücksetzung.
