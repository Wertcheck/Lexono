# Übergabe an Claude Code (Windows) – Prompt 36 + 37

> **Historisches Einmal-Handoff-Dokument (Prompt 36/37).** Sein Zweck ist
> erfüllt - `CLAUDE.md` übernimmt diese Rolle seitdem dauerhaft. Nur bei
> Interesse an der damaligen Übergabesituation relevant.

Dieses Dokument ist der Übergabepunkt von der Entwicklungssitzung im Chat (Claude Sonnet 5)
an eine Claude-Code-Sitzung auf deiner Windows-Zielmaschine. Claude Code liest `CLAUDE.md`
automatisch zuerst – dieses Dokument ergänzt nur den für Prompt 36/37 spezifischen Kontext,
der nicht schon in `CLAUDE.md`/`ARCHITECTURE.md`/`TODO.md` steht bzw. dort schwer auffindbar
wäre.

## Warum diese Sitzung auf Windows läuft, nicht im Chat

- PyInstaller muss auf der Zielplattform laufen, um eine echte Windows-`.exe` zu erzeugen –
  Cross-Building aus Linux ist nicht zuverlässig.
- Inno Setup (Standardweg für einen Windows-Installer) läuft nur unter Windows.
- Windows-Dienst-Registrierung, Pfade, Berechtigungen lassen sich in der Linux-Sandbox nicht
  wirklich testen, nur behaupten.

## Stand des Projekts

- Phasen 1–7 vollständig abgeschlossen (Prompts 01–35), 708/708 Tests grün, 15 Commits.
- Aktueller Branch/Stand: `main`, letzter Commit `ec65440` ("Prompt 35: Export/Backup...").
- `CLAUDE.md`, `ARCHITECTURE.md` (44 nummerierte Abschnitte), `TODO.md`, `SECURITY_REVIEW.md`
  sind die maßgeblichen Referenzdokumente – bitte **vor** Beginn lesen (CLAUDE.md sagt das
  ohnehin, aber wichtig genug für die Wiederholung).

## Entscheidung, die Prompt 36/37 direkt betrifft (16.08., mit dem Anwalt abgestimmt)

**Getrennte Installation je Kanzlei** – jede Kanzlei erhält eine eigene, unabhängige
Installation mit eigener Datenbank (kein `tenant_id`, kein gemeinsam genutzter Datenbestand
zwischen Kanzleien). Siehe TODO.md, Abschnitt "Entscheidung zur Mehr-Kanzlei-Fähigkeit"
für die vollständige Begründung. Konkrete Folgen für diese beiden Prompts:

- Der Installer installiert **eine** Instanz für **eine** Kanzlei. Keine Mandanten-/Tenant-
  Auswahl im Installer nötig.
- "Multi-Kanzlei-Profile + Cross-Tenant-Tests" (späterer Punkt in Phase 8) bedeutet in diesem
  Modell: der Setup-Assistent (Prompt 37) muss das Aufsetzen **mehrerer unabhängiger**
  Installationen auf verschiedenen Rechnern/für verschiedene Kanzleien unterstützen (Branding,
  Klassifikationsschlüsselwörter, Policies als Installationszeit-Konfiguration je Instanz) –
  **nicht** Mandantentrennung innerhalb einer laufenden Anwendung.
- Lizenz-/Auslieferungsmodell (wie eine neue Installation praktisch an eine zweite Kanzlei
  verteilt wird – Download-Link, physischer Datenträger, o. Ä.) ist weiterhin **offen**, wird
  bei Bedarf gesondert geklärt. Für Prompt 36/37 selbst nicht blockierend.

## Was für den Installer bereits vorhanden ist (nicht neu bauen)

- `scripts/create_admin.py` – legt den initialen Admin-Nutzer an (liest `ADMIN_EMAIL`/
  `ADMIN_INITIAL_PASSWORD` aus Umgebungsvariablen, generiert bei Bedarf ein sicheres Zufalls-
  passwort, erzwingt Passwortänderung beim ersten Login). Der Installer/Setup-Assistent sollte
  dieses Skript **aufrufen**, nicht die Logik duplizieren.
- `scripts/create_backup.py`, `scripts/retry_failed_items.py`, `scripts/seed_synthetic_data.py`
  – weitere CLI-Skripte, die ggf. in eine geplante Windows-Aufgabenplanung eingebunden werden
  könnten (siehe ARCHITECTURE.md §31/§35 für Kontext) – nicht Teil des Installers selbst, aber
  gut zu kennen.
- `alembic upgrade head` – muss beim ersten Start (und bei jedem Update) laufen.
- `.env.example` – Vorlage für die Konfigurationsdatei, inkl. Kommentaren zu jedem sicherheits-
  relevanten Wert (Session-Secret, Cookie-Secure-Flag, DB-Pfad, Speicherverzeichnisse,
  Logging, Budget).
- Kein existierender Top-Level-Entry-Point (`run.py` o. Ä.) – die App wird bisher nur über
  `uvicorn app.main:app` gestartet. Für PyInstaller wird vermutlich ein dünner Entry-Point
  nötig (z. B. `run.py`, der `uvicorn.run(...)` mit den richtigen Parametern aufruft).

## Wichtige Pfad-/Datenverzeichnis-Hinweise

- `database_url` (Default `sqlite:///./data/kanzlei_ai.db`), `intake_storage_dir` (Default
  `data/intake`), `mail_attachment_storage_dir` (Default `data/mail_attachments`),
  `log_file_path` (Default `None`, optional) – alle relativ zum Arbeitsverzeichnis. Für eine
  "richtige" Windows-Installation vermutlich sinnvoll, diese auf einen Pfad unter
  `%PROGRAMDATA%` oder `%APPDATA%` zu legen (schreibbar für normale Nutzer, nicht
  `Program Files`, das i. d. R. nur für Admin-Installationen beschreibbar ist) – das ist eine
  Entscheidung, die in dieser Sitzung getroffen werden sollte, bitte in ARCHITECTURE.md
  begründet dokumentieren.
- Datenbank + Dokumentenspeicher enthalten **unpseudonymisierte Mandanteninhalte** (siehe
  ARCHITECTURE.md §44) – der Installer sollte diese Verzeichnisse nicht versehentlich in ein
  von Windows automatisch synchronisiertes/cloud-gesyncetes Verzeichnis legen (OneDrive-
  Default-Ordner etc.), ohne das bewusst zu entscheiden und zu dokumentieren.

## Sicherheitsrelevante Punkte, die der Installer/Setup-Assistent beachten muss

Aus `SECURITY_REVIEW.md`, weiterhin gültig:

- `SESSION_SECRET_KEY` **muss** in Produktion gesetzt werden (langer Zufallswert) – der
  Installer/Setup-Assistent sollte das automatisch generieren und in die `.env` schreiben,
  nicht dem Anwalt überlassen. Ohne gesetzten Wert startet die App in Nicht-Entwicklungs-
  umgebungen nicht (siehe app/config/settings.py, bewusst so).
- `APP_ENV` muss in einer echten Installation auf etwas anderes als `"development"` gesetzt
  sein, sonst greifen die bewusst eingebauten Dev-Fallbacks (Secret-Key, Cookie-Secure-Flag) –
  siehe ARCHITECTURE.md §38.
- Initiales Admin-Passwort darf nicht im Code/Installer-Skript stehen (siehe
  `scripts/create_admin.py` – bereits korrekt gelöst, nur weiterverwenden).

## Offene, bewusst noch nicht entschiedene Punkte im Gesamtprojekt

Diese sind **nicht** zwingend Teil von Prompt 36/37, aber relevant, falls sie während der
Arbeit berührt werden – bitte nicht "nebenbei" mitentscheiden, sondern zurückstellen und im
Chat besprechen, außer sie sind für den Installer selbst unumgänglich:

1. **2FA** fehlt weiterhin (SECURITY_REVIEW.md Punkt 3) – zwingend vor öffentlichem/
   produktivem Einsatz, nicht vor diesem Schritt.
2. **Code-basierte Rechte-Matrix** (ARCHITECTURE.md §38) – kein Sicherheits-Gate, nur eine
   spätere Skalierungsfrage bei echter Mehrkanzlei-Anbindung.
3. **Kontext-Frage aus Prompt 23** (ob die Neugenerierung den bisherigen Entwurfstext erhalten
   soll) – weiterhin offen, nicht sicherheitsrelevant, betrifft diesen Schritt nicht.
4. **Ollama/lokales Modell** (ARCHITECTURE.md §43) – weiterhin offene, bewusst nicht
   vorweggenommene Entscheidung, betrifft diesen Schritt nicht.
5. **Kein UI-Trigger im Posteingang**, um aus einer Nachricht direkt einen Entwurf zu
   erstellen (ARCHITECTURE.md §36) – funktionale Lücke, kein Bezug zum Installer.
6. **Fehler-/Retry-System nur für OCR/Intake verdrahtet**, nicht Klassifikation/Matching
   (ARCHITECTURE.md §40) – kein Bezug zum Installer.
7. **Kein Restore-Mechanismus** für Backups, nur Erzeugen (ARCHITECTURE.md §44) – falls der
   Setup-Assistent auch eine "Wiederherstellen aus Backup"-Option anbieten soll, wäre das
   eine bewusste Erweiterung über den bisherigen Plan hinaus – bitte vorher kurz rückmelden,
   nicht einfach mitbauen.
8. **Kein automatisches Löschen alter Backups/Exporte**, keine Verschlüsselung der Archive
   (ARCHITECTURE.md §44) – liegt beim Betreiber, evtl. für den Setup-Assistenten relevant
   (z. B. ein Hinweis-Dialog), aber keine neue Funktionalität ungefragt ergänzen.
9. **Lizenz-/Auslieferungsmodell** für weitere Kanzleien – s. o., weiterhin offen.
10. **Kein Windows-reservierte-Namen-Schutz** bei Dateinamen (CON, PRN, AUX, COM1-9, LPT1-9) –
    aus SECURITY_REVIEW.md als kleiner, nicht sicherheitskritischer Nachtrag vermerkt, auf
    einer echten Windows-Installation ggf. jetzt erstmals praktisch relevant – bitte kurz
    prüfen, ob das in der Praxis auftritt, und bei Bedarf im Chat rückmelden statt still zu
    beheben, falls der Fix größer als ein paar Zeilen wird.

## Erwartete Vorgehensweise (aus CLAUDE.md, hier nur betont)

- Kleinstmöglicher sinnvoller Schritt, kein Scope-Creep.
- Tests schreiben, wo sinnvoll möglich (ein Windows-Installer selbst lässt sich nicht per
  pytest testen – aber die Setup-/Konfigurationslogik, sofern in Python, schon).
- Nach Abschluss: `ARCHITECTURE.md` (neuer nummerierter Abschnitt, fortlaufend ab §45),
  `TODO.md` (Prompt 36 und ggf. 37 abhaken) aktualisieren – **exakt im bisherigen Stil**
  (siehe bestehende Abschnitte als Vorlage: Zusammenfassung, Testergebnisse, offene Punkte).
- Git-Commit mit aussagekräftiger Nachricht, wie bei allen bisherigen Prompts.
- Am Ende: kurzer Bericht, was gebaut wurde, was getestet werden konnte und was nicht (z. B.
  weil kein zweiter Windows-Rechner für einen echten Cross-Install-Test verfügbar ist), und
  welche der oben genannten offenen Punkte berührt wurden.

## Rückgabe an den Chat

Bitte nach Abschluss dieser Sitzung zurückmelden (im Chat mit Claude Sonnet 5, nicht hier):
- Was wurde gebaut (Dateiliste)?
- Testergebnisse (inkl. was NICHT testbar war und warum)?
- Wurde eine der offenen Fragen oben berührt/entschieden? Welche und wie?
- Commit-Hash(es)?

Damit der Fortschritt im Hauptverlauf (TODO.md/ARCHITECTURE.md/Gesprächsverlauf) konsistent
bleibt, unabhängig davon, in welcher Umgebung tatsächlich gebaut wurde.
