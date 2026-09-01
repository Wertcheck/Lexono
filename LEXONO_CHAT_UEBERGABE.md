# LEXONO – Chat-Übergabe (für einen neuen ChatGPT-Planungs-Chat)

**Erstellt:** 01.09.2026, ca. 10:30 Uhr, von Claude (ausführender Coding-Agent,
Claude Code CLI) auf Basis des tatsächlichen Repository-/Git-/Test-Zustands
zu diesem Zeitpunkt. Repository: `C:\Projekte\kanzlei_ai`.

**Rollenverteilung (wichtig für den neuen Chat):** ChatGPT ist die
Planungs-/Analyse-/Prompting-Ebene. **Claude (Claude Code) bleibt der
ausführende Coding-Agent**, der tatsächlich am Repository arbeitet, Code
schreibt, Tests ausführt, Builds erstellt und Commits macht. Dieses
Dokument ist die Wissensgrundlage, damit ChatGPT wieder präzise Prompts
für Claude formulieren kann – es ersetzt NICHT die Interaktion mit Claude
selbst.

---

## A. Projektübersicht

**Lexono** ist eine konfigurierbare KI-gestützte Workflow-Plattform für
eine Anwaltskanzlei (aktueller Fokus: Steuer-/Wirtschaftskanzleien, nicht
primär Arbeitsrecht). Es verarbeitet E-Mails und Dokumente, ordnet sie
Akten zu, extrahiert Inhalte, erkennt mögliche Fristen, recherchiert in
konfigurierten Rechtsquellen, erstellt Antwortentwürfe/Schriftsätze und
legt sie nach menschlicher Freigabe ab bzw. in den Postausgang.

**Kernprinzip (nicht verhandelbar, siehe `CLAUDE.md`):** Der finale
Versand erfolgt standardmäßig NIEMALS autonom. Keine automatische
externe Kommunikation ohne explizite menschliche Freigabe. Keine
autonome rechtliche Entscheidung durch die KI.

**Bereits vorhandene Kernfunktionen** (verifiziert im Code vorhanden,
nicht nur geplant):

- Chat als zentrale Startseite nach Login (`/dashboard/chat`)
- Dokument-Upload im Chat (Drag & Drop, Büroklammer-Button)
- Lokale Dokumentverarbeitung (OCR via Tesseract, Textextraktion)
- Lokale Pseudonymisierung (Presidio + Regex-Detektoren) vor jedem
  Cloud-KI-Aufruf
- Lokale KI (Ollama) als verpflichtender Zwischenschritt vor Claude
- Cloud-KI-Anbindung über Claude (Anthropic API), direkt (Dev) oder über
  den Lexono-Gateway (Produktion)
- Schriftsatz-Generator (eigenständiges Tool, nutzt denselben
  `DraftingService` wie der Chat)
- Dokument-Workspace im Chat: extrahierter Text mit
  Pseudonymisierungs-Highlighting + Kontextleiste
- Rollen/Berechtigungen, Session-/CSRF-Schutz, Audit-Log
- Feedback-System mit lokaler Kategorisierung (`app/pilot_feedback/`)
- Backup/Export, Fehler-/Retry-System, Monitoring, Update-Mechanismus
  (nicht erzwungen)
- Windows-Installer (Inno Setup), natives Windows-Fenster (pywebview +
  WebView2), eigene (nicht-native) Fenster-Titelleiste

**Noch in Entwicklung / unvollständig:**

- Echtes PDF-Seiten-Rendering im Dokument-Workspace (aktuell nur
  extrahierter Text mit Highlighting, kein Zoom/Print/Seitennavigation)
- Model-Evaluation über weitere lokale KI-Runtimes (bisher nur Ollama;
  llama.cpp-Integration architektonisch vorbereitet, nicht umgesetzt)
- Systematischer Visual-QA-Loop mit Referenzbildvergleich
- Vollständige Feedback→Priorisierung→Release-Pipeline (aktuell nur
  Erfassung + lokale Kategorisierung)

**Langfristiges Produktziel:** Lexono soll sich für die Kanzlei wie eine
zentrale digitale Arbeitszentrale anfühlen – der Chat ist dabei der
zentrale Einstiegspunkt, Dokumente sind zentraler Bestandteil, bestehende
Fachfunktionen bleiben erreichbar, die Oberfläche bleibt ruhig und
professionell, Datenschutz/Sicherheit werden nie zugunsten von Bequemlichkeit
geopfert. Die Architektur soll so vorbereitet sein, dass Lexono während
einer Pilotphase kontinuierlich durch Feedback, Tests und kontrollierte
Releases verbessert werden kann (OHNE dass jetzt bereits eine große
Multi-Agenten-Unternehmensplattform gebaut wird – das ist explizit
zurückgestellt, siehe Abschnitt L).

---

## B. Verbindliche Architektur (aktueller Stand)

**Quelle:** `ARCHITECTURE.md` (Root) enthält seit 01.09. einen
prominenten Block **"AKTUELLER VERBINDLICHER ARCHITEKTURSTAND"** direkt
nach dem Titel – dieser Block ist die maßgebliche Kurzfassung, da das
Dokument selbst über 71 chronologisch gewachsene Abschnitte verfügt, von
denen mehrere spätere Abschnitte frühere bewusst überschreiben.

### Verbindlich, aktuell:

1. **Lokale KI ist zwingender, NICHT-optionaler Architekturbestandteil.**
   Sie ist ein PFLICHT-Zwischenschritt zwischen dem Privacy Gateway und
   Claude (`app/ai_providers/ollama_provider.py`, orchestriert über
   `DraftingService.create_draft`). Sie ersetzt Claude NICHT (kein
   „entweder/oder") und darf nicht zugunsten einer reinen Cloud-Lösung
   entfernt oder generell optional gemacht werden.
   - Runtime: **Ollama** (lokal, `http://127.0.0.1:11434`).
   - Aktuelles Standardmodell: **`qwen2.5:1.5b`** (datenbasiert gewählt,
     siehe Abschnitt H/Model Evaluation).
2. **Lokale Pseudonymisierung ist zwingend.** Microsoft **Presidio**
   (Analyzer, deutsches spaCy-Modell `de_core_news_lg`) + eigene
   Regex-Detektoren (E-Mail, IBAN, Telefon, Datum, Aktenzeichen etc.)
   erkennen PII; ein „Final Payload Gate" prüft, dass NUR pseudonymisierte
   Daten die Kanzlei-Maschine verlassen. Fail-closed: schlägt
   Pseudonymisierung fehl, erfolgt KEIN Cloud-Aufruf.
   - Im Nutzer-UI heißt das ausschließlich **„Pseudonymisierung"**, NICHT
     „Presidio" (Ausnahme: zwei Seiten, die sich selbst explizit als
     „Technische Transparenz" ausweisen, dort darf der Bibliotheksname
     stehen bleiben).
3. **Cloud-KI = Claude (Anthropic API).** Zwei Zugriffswege:
   - **Entwicklung:** direkter Anthropic-API-Key aus `.env`
     (`ANTHROPIC_API_KEY`) – NUR für Entwicklung/Qualitätstests, NICHT
     für den finalen Kanzlei-Installer.
   - **Produktion:** **Lexono-Gateway** – ein separater, selbst
     betriebener FastAPI-Relay-Server, der den echten Anthropic-Key hält.
     Der Kanzlei-PC kennt in Produktion NUR die Gateway-Adresse + ein
     Tenant-Credential, NIEMALS den echten API-Key. Der Gateway
     entscheidet NICHT über Datenschutz (das passiert vorher, lokal) –
     er ist reine Infrastruktur für Schlüssel-/Zugriffsverwaltung,
     Rate-Limiting, Tenant-Isolation. Kein Content-Storage im Gateway.
   - **Diese Gateway-Architektur überschreibt bewusst und dauerhaft**
     eine noch frühere Entscheidung („kein zentraler Proxy, direkte
     Anthropic-Anbindung") – siehe Abschnitt „verworfene Architekturen"
     unten.
4. **Natives Windows-Fenster ist zwingender Bestandteil des Produkts**
   (kein reiner Browser-Zugriff als Standard-Nutzungsweg). Technisch:
   - **pywebview** (Version 6.2.1, real im venv verifiziert) mit
     **WebView2**-Renderer (Microsoft Edge Runtime).
   - Das Fenster läuft seit 01.09. mit **`frameless=True`** – die native
     OS-Titelleiste ist entfernt, ersetzt durch eine **eigene, selbst
     gebaute Titelleiste** (HTML/CSS/JS: Drag-Region, Minimieren-Button,
     Schließen-Button, Eck-Resize-Griff unten rechts).
5. **Backend:** FastAPI + Uvicorn, serverseitig gerendert über
   Jinja2-Templates + HTMX (bewusst KEINE separate SPA/React/Vue, kein
   Node.js-Build-Schritt im gesamten Projekt).
   - Startup-Architektur: Uvicorn läuft in einem **Hintergrund-Thread**;
     `webview.start()` läuft **blockierend im Hauptthread** (technische
     Notwendigkeit nativer GUI-Event-Loops unter Windows). Siehe
     `run.py::_serve_with_window`.
6. **Datenbank:** SQLite für den Prototyp/aktuellen Stand; die
   Datenzugriffsschicht ist von Anfang an so abstrahiert (SQLAlchemy,
   Connection-String über Konfiguration), dass später PostgreSQL ohne
   Neuentwicklung von Datenmodell/Geschäftslogik möglich ist.
7. **Produktname: LEXONO.** „KanzleiAI"/„Kanzlei-AI" ist ein
   ÜBERHOLTER früherer Arbeitstitel und darf NICHT mehr als sichtbarer
   Produkt-/App-/UI-/Markenname verwendet werden. Ausnahme: interne
   technische Bezeichner, die aus Kompatibilitätsgründen (bestehende
   Installationen/Datenverzeichnisse) unverändert bleiben, z. B. die
   ausführbare Datei `kanzlei_ai.exe`, das Python-Package `app/`, und der
   `%ProgramData%\KanzleiAI`-Datenordner bestehender Installationen –
   diese NICHT blind umbenennen (würde bestehende Installationen/Daten
   gefährden).

### Datenfluss (verbindlich):

```
Dokument (Upload/Scan)
  → lokale Extraktion/OCR (Tesseract)
  → lokale KI (Ollama, Pflicht-Vorverarbeitung)
  → Presidio/Pseudonymisierung (lokal)
  → Final Payload Gate (lokal, prüft: nur Platzhalter, keine Originaldaten)
  → Lexono Gateway (hält den echten API-Key, KEIN Content-Storage)
  → Claude/Anthropic API (sieht NUR pseudonymisierten Text)
  → Antwort kommt pseudonymisiert zurück
  → lokale Rekonstruktion (Platzhalter → echte Werte, NUR lokal)
  → Anzeige für den Anwalt
```

### Frühere Architekturvarianten, die verworfen/überschrieben wurden:

(Diese Information ist wichtig, damit der neue Chat NICHT versehentlich
eine alte Variante als „aktuell" behandelt.)

- **„Local-First mit Ollama als alleinigem Standard-Provider"**
  (frühere Zwischenstufe) – ÜBERHOLT. Ersetzt durch: lokale KI als
  Pflicht-VORSTUFE vor Claude, nicht als Ersatz für Claude.
- **„Ollama vollständig entfernt"** (eine noch frühere Zwischenstufe,
  als ausschließlich Presidio+Claude ohne lokale KI galt) – ÜBERHOLT.
  Lokale KI wurde danach als zwingender Bestandteil wieder eingeführt
  und ist es bis heute.
- **„Kein zentraler Proxy, nur direkte Anthropic-Anbindung"** – TEILWEISE
  ÜBERHOLT. Die konkret abgelehnte Portkey-Drittanbieter-Lösung bleibt
  korrekt abgelehnt, aber das GRUNDPRINZIP „kein zentraler Proxy" wurde
  durch die Einführung des selbst betriebenen Lexono-Gateways bewusst
  aufgehoben. Ein zentraler Cloud-Relay ist heute Teil der verbindlichen
  Architektur (nur eben ein selbst kontrollierter, nicht Portkey).
- **Natives, klassisches Browser-Fenster ohne besondere Behandlung** –
  ÜBERHOLT seit der Einführung des nativen Desktop-Fensters; seit 01.09.
  zusätzlich die native OS-Titelleiste selbst ersetzt (frameless +
  eigene Titelleiste).

---

## C. Produktlogik (zuletzt beschlossen)

**Der Chat ist NICHT nur eine Startseite vor den eigentlichen Tools –
er soll perspektivisch die zentrale Arbeitszentrale von Lexono werden.**
Nutzer sollen aus dem Chat heraus möglichst viele typische
Kanzleivorgänge anstoßen können, ohne zwischen vielen Werkzeugen wechseln
zu müssen:

- Dokument hochladen (Drag & Drop oder Büroklammer-Button) – **bereits
  implementiert**
- Dokument analysieren / zusammenfassen – **bereits implementiert**
  (Quick-Actions im leeren Chat-Zustand, plus freie Texteingabe)
- Fragen zu einem Dokument stellen – **bereits implementiert** (Chat
  bleibt nutzbar, während ein Dokument im Workspace angezeigt wird)
- Dokumentinhalte verarbeiten (Pseudonymisierung/Highlighting sichtbar
  machen) – **bereits implementiert** (Dokument-Workspace, siehe unten)
- Schriftsätze/Antwortentwürfe erzeugen – **bereits implementiert** (über
  denselben `DraftingService` wie der eigenständige
  Schriftsatz-Generator, keine Doppelimplementierung)
- Weitere Lexono-Funktionen aus dem Chat heraus starten – **TEILWEISE**:
  „Akte öffnen" als Quick-Action verlinkt bereits auf die
  Aktenverwaltung; eine tiefere Integration weiterer Tools direkt aus
  dem Chat heraus ist NICHT vollständig umgesetzt (offen, siehe
  Abschnitt L)

**Weitere verbindlich festgelegte Anforderungen:**

- Bestehende Funktionen (Schriftsatz-Generator, Dokumentenverarbeitung,
  Monitoring, Einstellungen, Administration etc.) dürfen NICHT unnötig
  entfernt werden – der Chat wird dominant, ist aber kein Ersatz für
  alle anderen Werkzeuge.
- Keine Fake-Buttons, keine Fake-KI, keine Dummy-Funktionen, die wie
  fertige Produktfunktionen aussehen.
- UI muss bei 1366×768 UND 1920×1080 funktionieren (responsive
  Mindestanforderung).
- Fehlermeldungen müssen für den Anwender verständlich sein – NIEMALS
  Stacktraces, interne Pfade, Secrets oder technische Interna nach außen
  zeigen.
- Mikrofon-Button im Chat ist bewusst NUR als vorbereitete UI umgesetzt,
  NICHT an eine echte (typischerweise cloud-basierte) Spracherkennung
  angebunden – das würde den lokalen Privacy-Kernschutz umgehen (Audio
  würde vor jeder lokalen Pseudonymisierung an einen Cloud-Dienst
  gesendet).

---

## D. UI / UX – aktueller tatsächlicher Zustand

### ERLEDIGT (im Code umgesetzt UND funktional verifiziert):

- Chat als zentrale Startseite nach Login (`/dashboard/chat`)
- Chatverlauf, Enter zum Senden, Shift+Enter für Zeilenumbruch
- Dokumentanhang im Chat (Büroklammer-Icon – jetzt ein echtes
  Büroklammer-Symbol, vorher ein Upload-Pfeil-Icon)
- Drag & Drop für Datei-Uploads im Composer
- Vier Quick-Action-Kacheln im leeren Chat-Zustand: „Dokument
  analysieren", „Schriftsatz erstellen", „Dokument zusammenfassen",
  „Akte öffnen"
- **Vierfarbige Icon-Badges** für die vier Quick-Actions (grün/blau/
  lila/orange) – NEU, 01.09., real getestet
- Dokumentstatus-Anzeigen (Verarbeitet / Wartet auf OCR / Verarbeitung
  fehlgeschlagen / Format nicht unterstützt)
- Dokument-Workspace (`/dashboard/chat/{conversation_id}/document/
  {document_id}`): extrahierter Text mit farblich hervorgehobenen
  erkannten PII-Kategorien + rechte Kontextleiste „Erkannte
  Mandantendaten" + eingeklappte Unterhaltungsliste
- **KI-Ladezustand** („Lexono denkt nach…"-Sprechblase mit
  Puls-Animation) – NEU, 01.09.
- Globale Sidebar-Statusanzeige (Lokale KI / Cloud-KI) auf allen Seiten
  außer Chat (dort weiterhin im Chat-Header, um Dopplung zu vermeiden)
- Branding vollständig auf „Lexono" umgestellt, kein „KanzleiAI"-Rest
  mehr in sichtbaren UI-Templates (systematisch gegengeprüft)
- Sichtbares „Strg+K"-Tastenkürzel-Badge im Suchfeld entfernt
  (Tastenkürzel selbst funktioniert weiterhin)
- „Presidio" aus der normalen Chat-UI entfernt (nur noch
  „Pseudonymisierung"; zwei technische/administrative Seiten behalten
  den Bibliotheksnamen bewusst)

### SOURCE ERLEDIGT / IM AKTUELL INSTALLIERTEN BUILD NOCH NICHT VERIFIZIERT:

Der aktuell laufende/installierte Build (Zeitstempel 07:53 Uhr,
01.09.) enthält NICHT die beiden folgenden, bereits im Quellcode
fertiggestellten und committeten Änderungen:

1. **Icon-Clipping-/Padding-Fix der eigenen Titelleiste**: das
   Schließen-Icon (✕) wurde am rechten Fensterrand sichtbar
   angeschnitten (rechter Innenabstand von 6px auf 20px erhöht, Commit
   `e10c04e`). Per echtem Screenshot bestätigt (auch außerhalb des
   Fensterrands weiterhin sichtbar angeschnitten, also kein
   Screenshot-Artefakt).
2. **Vierfarbige Quick-Action-Badges** (Commit `176a204`, siehe oben).

Beide Änderungen SIND im aktuellsten Installer enthalten
(`dist/installer/Lexono_Setup.exe`, kompiliert 09:39 Uhr), dieser
Installer konnte aber bislang nicht erfolgreich real installiert werden
(siehe Abschnitt F).

### Fenster-/Titelleisten-Zustand im Detail:

- Native OS-Titelleiste: **entfernt** (`frameless=True`), ERLEDIGT und
  vom Nutzer real bestätigt funktionsfähig.
- Eigene Titelleiste zeigt: Drag-Region (leerer Bereich, klickbar zum
  Verschieben) + Minimieren-Button + Schließen-Button. **KEIN Logo/
  keine Wortmarke** in der Titelleiste (wurde bewusst entfernt, da
  Sidebar/Login-Box das Logo bereits zeigen – „doppelte
  Logo-Darstellung" vermieden).
- **Schließen-Button**: vom Nutzer REAL getestet und bestätigt
  funktionierend („x button closes the app") – das war der
  ursprüngliche kritische Bug (Titelleiste existierte anfangs nur in
  `base.html`, nicht in den eigenständigen `login.html`/`unlock.html`-
  Templates, wodurch das Fenster auf der allerersten Seite komplett
  unbedienbar war). Behoben durch Auslagerung in ein gemeinsames
  Partial + gemeinsame JS-Datei, die in allen drei Root-Templates
  eingebunden ist.
- Eck-Resize-Griff unten rechts (einzige Möglichkeit, die Fenstergröße
  zu ändern – es gibt KEINEN Maximieren-Button und KEIN
  Doppelklick-zum-Maximieren, das wurde bewusst NICHT umgesetzt, siehe
  Abschnitt H).
- **Wichtige Einschränkung bei der Verifikation**: automatisierte
  Screenshots direkt nach erzwungenem `ShowWindow`/`SetForegroundWindow`
  zeigten mehrfach INKONSISTENT eine leere Titelleiste (weder
  Minimieren noch Schließen sichtbar) – auch auf dem bereits vom Nutzer
  bestätigten Build. Plausibelste Erklärung: eine Race Condition
  zwischen der erzwungenen Fenster-Vordergrund-Aktion und dem
  `pywebviewready`-Aktivierungs-Timing der Titelleiste, NICHT ein
  echter Bug (der Nutzer hat den Button ja bereits real als
  funktionierend bestätigt). Ein einzelner „leerer" Screenshot ist
  daher KEIN verlässlicher Fehlerbeweis – siehe `VISUAL_QA.md`.

---

## E. Branding / Design

**Verbindliche CI-Werte (Design-Tokens in `app/web/static/css/app.css`):**

- Primärfarbe / aktueller Logo-Farbcode: **`#101828`** (dunkles Navy)
- Canvas-Hintergrund: **`#f8fafc`**
- Card-Hintergrund: **`#ffffff`**
- Sekundärtext: **`#64748b`**

**WICHTIG – ungeklärter Punkt zur Logo-/Primärfarbe:** Der Wert
`#101828` wurde in einer früheren Sitzung NICHT willkürlich gewählt,
sondern per Pixelfarbmessung aus einem tatsächlichen, vom Anwalt
bereitgestellten offiziellen Logo-Bild (`Desktop\Lexono Logo.png`)
verifiziert (gemessener Kernfarbwert ~`#0d1526`, dunkles Navy) und
danach bewusst auch auf primäre UI-Elemente ausgeweitet (dokumentiert in
`ARCHITECTURE.md` §61/§62 mit vollständiger Herleitung).

Später in dieser Sitzung kam die explizite Nutzeranforderung nach einem
„neuen grünen Logo" sowie „weiteren Akzentfarben". Dies steht im
Widerspruch zum verifizierten Navy-Wert. **Diese Frage wurde NICHT
eigenmächtig entschieden** – siehe Abschnitt L, Kategorie „BENÖTIGT
BENUTZERENTSCHEIDUNG". Der unabhängig davon umsetzbare Teil (mehrere
verschiedene Akzentfarben für die vier Chat-Quick-Actions) WURDE bereits
umgesetzt (grün/blau/lila/orange) – nur die „grüne" Badge-Farbe bindet
weiterhin bewusst an die noch ungeklärte Primärfarbe und übernimmt
automatisch den finalen Wert, sobald diese Frage geklärt ist.

**Logo-Datei:** `app/web/static/img/logo.svg` (+ `logo-white.svg` als
Variante für dunkle Hintergründe, aktuell nirgends eingebunden) – Motiv:
Dokument mit umgeknickter Ecke, zwei Textzeilen, darunter ein Schild mit
Kettenglied-Symbol. Aktuelle Füllfarbe: `#101828`.

---

## F. Aktueller Installer-Stand

- **Letzter erfolgreicher Rebuild:** kompiliert 01.09., ca. 09:39 Uhr.
  Pfad: **`dist/installer/Lexono_Setup.exe`**, Größe: **524.706.543
  Bytes (~525 MB)**. PyInstaller-Build UND Inno-Setup-Compile liefen
  beide fehlerfrei durch (bestätigt über Exit-Codes).
- Dieser Installer enthält ALLE bisherigen Fixes der Nacht, inklusive
  des Titelleisten-Paddings-Fixes und der vierfarbigen Quick-Action-
  Badges.
- **Die Silent-Installation dieses Builds ist auf der aktuellen
  Maschine VIERMAL hintereinander hängen geblieben** (`/VERYSILENT
  /SUPPRESSMSGBOXES`). Ein Versuch blieb über 8 Minuten bei konstant
  ~0% CPU auf dem `Lexono_Setup.tmp`-Prozess, ohne jeden Fortschritt bei
  der Datei-Extraktion.
- **Durchgeführte Diagnose:** `Get-MpPreference` zeigt
  `DisableRealtimeMonitoring: False` (Windows-Defender-Echtzeitschutz
  ist aktiv – aktueller Hauptverdächtiger für die Verzögerung/Blockade
  beim Entpacken der ~1,1GB großen, vielen Einzeldateien). Windows
  Application-Event-Log zeigte KEINEN Absturz/Fehler im relevanten
  Zeitraum – das Verhalten passt eher zu einer Scan-/
  Ressourcenkonflikt-Situation als zu einem Programmabsturz.
  **NICHT VERIFIZIERT**, ob Windows Defender tatsächlich die Ursache
  ist – nur die naheliegendste, nicht ausgeschlossene Erklärung.
- **Ausdrücklich NICHT getan:** Windows Defender wurde NICHT
  eigenständig deaktiviert. Eine Ausnahme für den Installationsordner
  wurde NICHT eingerichtet, weil das eine sicherheitsrelevante
  Systemänderung wäre, die nur nach ausdrücklicher Nutzerzustimmung
  vorgenommen werden darf.
- Nach dem vierten gescheiterten Versuch wurde das Retrying bewusst
  GESTOPPT statt endlos weiterzuversuchen. Das Installationsverzeichnis
  wurde als unbeschädigt verifiziert (kein korrupter/halbfertiger
  Zustand).
- **Aktuell installiert und laufend ist der VORHERIGE (dritte) Build**
  der Nacht (Verzeichnis-Zeitstempel 07:53:24 Uhr). Dieser enthält
  bereits:
  - den Titelleisten-Fix (frameless-Fenster, eigene Titelleiste)
  - die Logo-Entfernung aus der Titelleiste
  - den KI-Ladezustand (Thinking-Indicator)
  - einen real vom Nutzer bestätigten funktionierenden
    Schließen-Button
  Dieser Build enthält **NOCH NICHT** den Icon-Clipping-Fix und die
  vierfarbigen Badges (die sind nur im neueren, noch nicht installierten
  Build).
- **Sinnvoller nächster Schritt:** den bereits fertig gebauten neuesten
  Installer zu einem anderen Zeitpunkt real installieren (der Build
  selbst ist erwiesenermaßen NICHT das Problem – nur der lokale
  Silent-Install-Vorgang auf dieser spezifischen Maschine/zu diesem
  Zeitpunkt).

---

## G. Visual QA / Native Window – neue Testmöglichkeit

Am 01.09. wurde entdeckt, dass das native Windows-Fenster der Desktop-
App per **PowerShell + .NET `System.Drawing`** fotografiert werden kann
(`CopyFromScreen`, begrenzt auf den per `GetWindowRect` ermittelten
Fensterbereich – NIEMALS den gesamten Desktop, um keine unbeteiligten
Bildschirminhalte zu erfassen). Die gespeicherte PNG-Datei kann
anschließend über das `Read`-Tool tatsächlich visuell begutachtet
werden. Kein Browser-Automatisierungstool nötig, da es sich um ein
natives Fenster handelt, keinen Browser-Tab.

**Damit wurde ein echter Bug gefunden**: das Schließen-Icon der eigenen
Titelleiste war am rechten Fensterrand sichtbar angeschnitten (siehe
Abschnitt D/F). Der Fund wurde durch einen gezoomten Ausschnitt
bestätigt, der auch außerhalb des ursprünglich gemeldeten Fensterrands
noch dasselbe Anschneiden zeigte – also kein reines
Screenshot-Artefakt. Im Quellcode behoben (Commit `e10c04e`).

**Wichtige technische Einschränkung**: Wird das Fenster unmittelbar vor
der Aufnahme per `ShowWindow`/`SetForegroundWindow` zwangsweise in den
Vordergrund geholt, kann dies mit dem `pywebviewready`-
Aktivierungs-Timing des Titelleisten-Skripts kollidieren. Dadurch kann
ein einzelner Screenshot eine leere/nicht aktivierte Titelleiste zeigen,
OBWOHL der Build tatsächlich korrekt funktioniert (real beobachtet: auch
auf dem vom Nutzer bereits bestätigten Build trat das inkonsistent auf).
**Ein einzelner solcher Screenshot darf daher NICHT automatisch als
UI-Bug gewertet werden** – nur ein wiederholt/konsistent auftretender
Befund (wie das Icon-Clipping) ist belastbar. Diese Erkenntnis ist in
`.agentic/VISUAL_QA.md` dokumentiert.

Auch die richtige Prozess-ID muss vor einem Screenshot verifiziert
werden (`MainWindowTitle -eq "Lexono"` prüfen) – die von `Start-Process`
zurückgegebene PID kann ein Launcher-/Wrapper-Prozess ohne gültiges
`MainWindowHandle` sein, was zu einem Screenshot eines völlig anderen,
zufällig an denselben Bildschirmkoordinaten liegenden Fensters führen
kann (real einmal passiert).

---

## H. Bereits gelöste technische Probleme (relevant für zukünftige Entscheidungen)

- **pywebview-Startup-Freeze (behoben, frühere Sitzung):** Ursache war
  ein öffentliches `window`-Attribut auf der `_NativeApi`-Klasse
  (JS-Bridge). pywebviews eigene JS-Bridge-Generierung
  (`webview/util.py::get_functions`) traversiert rekursiv alle NICHT
  mit „_" beginnenden Attribute, um sie als JS-aufrufbare Funktionen
  bereitzustellen. Ein öffentliches `window`-Attribut führte in diesen
  nativen WinForms-Objektgraphen hinein, wo jeder Property-Zugriff über
  die .NET-Interop-Schicht (pythonnet) ein NEUES Wrapper-Objekt mit
  neuer Python-`id()` liefert – die eigene Zyklus-Erkennung von
  `get_functions` griff dadurch nie, die Rekursion lief endlos weiter
  (beobachtet als „Lexono (Keine Rückmeldung)" beim Fensteraufbau).
  **Fix:** das Attribut in `_window` umbenannt (führender Unterstrich),
  dadurch von `get_functions`s eigener Ausschlussregel erfasst. Bewusst
  als Lehre dokumentiert: KEINE öffentlichen Attribute auf `_NativeApi`,
  die auf komplexe native Objektgraphen verweisen.
- **FastAPI/Uvicorn + natives Fenster – Threading-Architektur:** Uvicorn
  läuft in einem Hintergrund-Thread; `webview.start()` blockiert im
  Hauptthread (Standard-Einschränkung nativer GUI-Event-Loops unter
  Windows). Der Webserver wird VOR dem Öffnen des Fensters auf
  Erreichbarkeit geprüft (`_wait_for_server_ready`).
- **Local-AI-Healthcheck:** ein stiller, nicht-blockierender
  Hintergrund-Check (`_run_silent_local_ai_check`, asyncio-Task im
  FastAPI-Lifespan) ermittelt den Lokale-KI-Status beim Start, OHNE den
  App-Start zu verzögern. Ergebnis landet in `app.state.local_ai_status`
  (Achtung: dieses `app.state`-Attribut ist NICHT immer gesetzt – siehe
  Test-Isolationsfund unten).
- **Presidio-Abhängigkeiten im PyInstaller-Bundle:** spaCy
  `de_core_news_lg` (das deutsche Sprachmodell) ist der mit Abstand
  größte Anteil am ~1,1GB-Bundle (dominiert durch die
  `vocab/vectors`-Datei, ca. 600MB). Bundling erforderte spezielle
  PyInstaller-Hooks (bereits gelöst, im `.spec` konfiguriert).
- **PyInstaller/Inno-Setup-Build-Fallstrick:** `pyinstaller`/`ISCC.exe`
  dürfen NICHT über einen verschachtelten `powershell -File
  script.ps1`-Aufruf gestartet werden – das verliert die
  venv-aktivierte PATH-Umgebung. Stattdessen `$env:Path` direkt in
  DERSELBEN PowerShell-Sitzung voranstellen, die dann den Build-Befehl
  ausführt.
- **WebView2-Bootstrapper-Bug (frühere Sitzung, behoben):**
  `dontcopy`-Flag in Inno Setup entpackt NICHT automatisch – erforderte
  einen expliziten `ExtractTemporaryFile('MicrosoftEdgeWebview2Setup.exe')`-
  Aufruf im `[Code]`-Abschnitt. Dieser Fix ist über mehrere Rebuilds
  hinweg stabil bestätigt.
- **Testisolationsfund (`app.state`):** `app.state` gehört zum einzigen
  prozessweiten `app`-Singleton. Tests, die den echten FastAPI-Lifespan
  durchlaufen lassen, setzen `app.state.local_ai_status` dauerhaft für
  den Rest des Testprozesses – Tests, die einen bestimmten
  Ausgangszustand erwarten, MÜSSEN ihn selbst speichern/löschen/
  wiederherstellen. Dieselbe Vorsicht gilt für Jinja-Templates, die
  `request.app.state.X` lesen (siehe nächster Punkt).
- **Jinja-Undefined-Fallstrick (01.09., real gefunden):** Ein
  Dict-Literal in Jinja (`{'a': ..., 'b': ...}[key]`) wertet ALLE Werte
  eager aus – auch nicht ausgewählte Zweige. Ein verketteter
  Attributzugriff auf einen potenziell undefinierten Wert
  (`undefined_var.attribut`) wirft dabei SOFORT einen `UndefinedError`,
  selbst wenn dieser Zweig gar nicht gewählt wird. Fix: riskante
  Attributzugriffe immer vorher in eine eigene `{% set %}`-Zeile mit
  `if/else`-Ternary auslagern (nutzt `__bool__`, nicht `__getattr__`).
- **Secure-Cookie-Testfallstrick:** Im Produktionsmodus
  (`app_env=production`) sind Session-Cookies `Secure`-geflaggt.
  Python `requests`/`http.cookiejar` senden solche Cookies NICHT über
  `http://127.0.0.1` zurück (RFC-6265-korrekt), obwohl WebView2/Chromium
  `http://127.0.0.1` korrekt als „potentially trustworthy origin"
  behandelt und Secure-Cookies dort sehr wohl sendet. Für
  HTTP-Smoke-Tests gegen die echte Installation: `Set-Cookie`-Header
  manuell aus jeder Response extrahieren und in nachfolgende Requests
  einsetzen, statt sich auf die Standard-Cookiejar-Logik zu verlassen.
- **Local-AI-Modell-Auswahl (datenbasiert):** Reale Benchmarks auf
  CPU-only-Hardware zeigten: `qwen3:4b` >20 Minuten Latenz (wegen
  „Thinking"-Modus, praktisch unbrauchbar), `llama3.2:1b` schnell, aber
  aufgabenverweigernd/unzuverlässig, `qwen2.5:1.5b` korrekt UND schnell
  (10-11s warm) – aktuelles Standardmodell. `gemma2:2b` (01.09. real
  getestet): langsamer (18,2s warm) UND unzuverlässig bei
  Platzhaltererhaltung (Pflichtkriterium – zwei reale Fälle von
  veränderten/verlorenen Platzhaltern in nur zwei Testläufen, einer
  davon eine echte Halluzination). `qwen2.5:1.5b` bleibt bestätigt die
  beste Wahl.
- **Silent-Install-Stall-Muster** (siehe Abschnitt F) – bisher NICHT
  vollständig gelöst, nur durch Kill+Retry umgangen (funktionierte bei
  den ersten drei Rebuilds der Nacht zuverlässig, beim vierten
  eskalierte es).

---

## I. Tests

- **Letzter voller, bestätigter Testlauf:** **1487 passed, 1 skipped, 0
  failed** (223,63s), Stand: Commit `e10c04e` (Titelleisten-Padding-Fix)
  und alle danach folgenden Commits sind reine Dokumentationsänderungen
  (`.agentic/*.md`, `ARCHITECTURE.md`) ohne Code-Auswirkung – die
  Zahl ist also für den aktuellen HEAD weiterhin gültig.
- Testverlauf im Laufe dieser Sitzung: 1463 → 1472 → 1484 → 1485 → 1486
  → 1487 (kontinuierlich neue Tests für neue Features ergänzt, KEIN
  einziger bestehender Test wurde entfernt/geschwächt, um „grün" zu
  werden).
- **E2E-Tests vorhanden** (nicht neu gebaut, bereits vorher vorhanden
  und funktionsfähig): `tests/test_e2e_pilot_scenario.py` und
  `tests/test_e2e_gateway_pilot_scenario.py` – prüfen den vollständigen
  Workflow **Dokument → lokale Pseudonymisierung → KI-Verarbeitung →
  Antwort-/Schriftsatzgenerierung**, inklusive: Originaltext bleibt
  lokal, Cloud-Payload enthält nachweislich KEINE Original-PII (Assert
  gegen den tatsächlich gesendeten JSON-Payload), Platzhalter werden
  korrekt gesetzt, Antwort wird korrekt lokal rekonstruiert.
- **Manuelles Real-Netzwerk-Smoke-Test-Skript** (nicht Teil der
  automatisierten Suite): `scripts/local_ai_smoke_test.py` – testet den
  vollen Pfad mit echten Netzwerkaufrufen (Ollama + echter
  Gateway-Prozess + echter Claude-Aufruf).
- **Bekannte Einschränkung:** kein automatisierter Browser-/Visual-Test
  über eine ganze Nutzungssitzung hinweg – nur die in Abschnitt G
  beschriebene native Fenster-Screenshot-Technik für Einzelzustände.
- **Bereits behobenes Testproblem:** siehe Testisolationsfund zu
  `app.state` in Abschnitt H – gelöst durch explizites
  Speichern/Wiederherstellen des Attributs in den betroffenen Tests.

---

## J. Git / Repository (tatsächlich verifizierter Zustand)

- **Branch:** `main`
- **Aktueller HEAD-Commit:** `da701705be401a2a673f24f82b5446bc9e06d77d`
  – „Projektgedaechtnis: Einschraenkung der Screenshot-Technik
  dokumentiert" (01.09.2026, 10:23:35 +0200)
- **Working Tree:** sauber (keine uncommitteten Änderungen)
- **Push-Zustand:** `main` ist **31 Commits vor `origin/main`** – **KEIN
  Push wurde durchgeführt** (durchgehend eingehalten, wie angewiesen)
- **Relevante Commits dieser Sitzung** (neueste zuerst, Auszug):
  - `da70170` – Screenshot-Technik-Einschränkung dokumentiert
  - `c3c2573` – vierter Installer-Rebuild gebaut, Install-Stall
    dokumentiert
  - `bf693d4` – echte Screenshot-Technik dokumentiert
  - `e10c04e` – Titelleisten-Padding-Fix (Icon-Clipping behoben)
  - `7a2f789` – gemma2:2b real getestet, qwen2.5:1.5b bestätigt
  - `176a204` – vierfarbige Quick-Action-Badges
  - `7c1ffd5` – Dokumentationskonsolidierung + verbindlicher
    Architekturstand
  - (weitere ältere Commits derselben Sitzung: Titelleisten-Fix,
    Dokument-Workspace, Agentenorganisation, Local-AI-Modellwahl,
    Gateway-Architektur – vollständige Historie über `git log`
    einsehbar)
- **Was committed, aber im installierten Build noch nicht verifiziert
  ist:** siehe Abschnitt D/F – Icon-Clipping-Fix + Quick-Action-Badges
  sind im Quellcode UND im neuesten (noch nicht installierten)
  Installer enthalten, aber NICHT im aktuell laufenden Build.

---

## K. Markdown-Dokumentation – Einordnung

Das Projekt enthält **63 Markdown-Dateien** insgesamt (12 davon im
Repository-Root). Wegen der langen, iterativen Entwicklungshistorie
enthalten mehrere ältere Dateien Architekturentscheidungen, die
inzwischen überholt sind.

### AKTUELL UND VERBINDLICH:

- **`CLAUDE.md`** (Root) – Master-Prompt/Grundregeln, wird von jeder
  Sitzung zuerst gelesen, ändert sich selten.
- **`ARCHITECTURE.md`** (Root) – kanonische Architekturquelle. Seit
  01.09. mit explizitem **„AKTUELLER VERBINDLICHER
  ARCHITEKTURSTAND"-Block direkt nach dem Titel** – DAS ist die
  maßgebliche Kurzfassung, nicht die 71 chronologischen Einzelabschnitte
  darunter.
- **`.agentic/PROJECT_STATE.md`** – reiner Ist-Zustand („Single Source
  of Truth" für den aktuellen Stand), seit 01.09. bewusst OHNE
  chronologische Verlaufsabschnitte.
- **`.agentic/OPEN_ISSUES.md`** – kategorisierte offene Punkte
  (CRITICAL/HIGH/MEDIUM/LOW/FUTURE + eigene Kategorie „Produktentscheidung
  erforderlich").
- **`.agentic/DECISIONS.md`** – Entscheidungslog mit Begründung
  (DECISION/REASON/DATE-Format, append-only).
- **`.agentic/TASK_MAP.md`** – Gesamtstand nach Kategorien A–K
  (Security, Produktfunktion, Chat/UX, Dokumentworkflow, UI/Visual,
  Agenten/Feedback, Model/AI, Installer/Deployment, Tests/QA,
  Dokumentation, langfristige Architektur).
- **`.agentic/TEST_STATE.md`**, **`.agentic/MODEL_EVALUATION.md`**,
  **`.agentic/VISUAL_QA.md`**, **`.agentic/AGENT_HANDOFFS.md`** –
  aktuell gepflegt.

### HISTORISCH / ÜBERHOLT (mit Hinweis-Header versehen, Inhalt bewusst
NICHT gelöscht):

- **`.agentic/SESSION_LOG.md`** – NEU (01.09.), bewusstes Archiv der
  chronologischen Verlaufserzählung, die vorher in PROJECT_STATE.md
  stand. Zeigt vergangene Etappen, NICHT den aktuellen Zustand.
- **`SECURITY_REVIEW.md`** (Root) – Stand 15.08.2026 (Prompt 27), jetzt
  mit Hinweis-Header versehen, der auf neuere Reviews verweist
  (`.agentic/DECISIONS.md`/`OPEN_ISSUES.md`).
- **`FINAL_REVIEW_REPORT.md`** (Root) – historischer Meilenstein-Bericht
  (Prompt 45, 17.08.2026), mit Hinweis-Header versehen.
- **`HANDOFF_PROMPT36_37_WINDOWS.md`** (Root) – einmaliges
  Handoff-Dokument, sein Zweck ist erfüllt (CLAUDE.md übernimmt diese
  Rolle seitdem), mit Hinweis-Header versehen.
- Innerhalb von `ARCHITECTURE.md` selbst: **§57, §60, §63** tragen
  jetzt jeweils eine eigene „> ÜBERHOLT"/„> TEILWEISE ÜBERHOLT"-Markierung
  direkt am Abschnittsanfang (siehe Abschnitt B, „verworfene
  Architekturvarianten").

### NOCH ZU BEREINIGEN (nicht dringend, nicht produktkritisch):

- `PROMPT38_ANALYSIS.md` (Root) – dokumentiert eine abgeschlossene
  ANALYSE zu „Multi-Kanzlei-Profile + Cross-Tenant-Tests", explizit als
  „Implementierung noch NICHT begonnen" markiert. Bereits mit einem
  Verweis auf `OPEN_ISSUES.md` versehen. Ob dieses Thema noch relevant
  ist, ist eine Produktentscheidung (siehe Abschnitt L).
- Weitere Root-Dateien (`PILOT_CHECKLIST.md`, `PILOT_PLAYBOOK.md`,
  `RELEASE_NOTES.md`, `FUTURE_ROADMAP.md`, `TODO.md`) wurden gesichtet,
  aber NICHT verändert – sie wirken wie weiterhin legitime, nicht
  offensichtlich widersprüchliche Dokumente (Pilotbetriebs-Playbook,
  Versions-Changelog, Roadmap, Phasenplan). **NICHT VERIFIZIERT im
  Detail**, ob sie an einzelnen Stellen ebenfalls veraltete Aussagen
  enthalten – bei Bedarf gezielt gegenprüfen, bevor man sich auf einen
  Einzelpunkt daraus verlässt.

**Wichtiger Grundsatz für den neuen Chat:** Eine ältere Markdown-Datei
hat NIEMALS automatisch Vorrang vor dem aktuellsten, als verbindlich
markierten Stand (`ARCHITECTURE.md`-Kopfblock,
`.agentic/PROJECT_STATE.md`, dieses Übergabedokument). Bei Widersprüchen
gilt: neuester, explizit als verbindlich markierter Stand gewinnt.

---

## L. Vollständige Liste offener Anpassungen

### ERLEDIGT (zur Einordnung, nicht erneut angehen):

- Chat als zentrale Startseite
- Dokument-Upload/Drag & Drop/Büroklammer-Icon im Chat
- Dokument-Workspace mit Pseudonymisierungs-Highlighting (textbasiert)
- KI-Ladezustand (Thinking-Indicator)
- Eigene Fenster-Titelleiste (frameless), Schließen-Button real bestätigt
- Doppelte Logo-Darstellung (Titelleiste+Sidebar) entfernt
- Vierfarbige Quick-Action-Badges (unabhängig von der Primärfarben-Frage)
- Globale Sidebar-Statusanzeige (Lokale KI/Cloud-KI)
- Branding vollständig auf Lexono umgestellt
- „Strg+K"-Badge entfernt, „Presidio" aus normaler UI entfernt
- Agentenorganisation (`agents/`, `skills/`, `.agentic/`) aufgebaut und
  tatsächlich genutzt (u. a. ein Security-Review-Subagent)
- Unabhängige Security-Review der neuen Chat-/Dokument-Workspace-/
  Fenster-API-Codeteile (keine Funde)
- Model-Evaluation um `gemma2:2b` erweitert (negatives, aber werthaltiges
  Ergebnis – aktuelles Modell bestätigt)
- Dokumentationskonsolidierung + verbindlicher Architekturstand in
  `ARCHITECTURE.md`
- Screenshot-Technik für natives Fenster entdeckt und dokumentiert

### SOURCE ERLEDIGT / BUILD NOCH NICHT VERIFIZIERT:

| Aufgabe | Priorität | Abhängigkeit | Nächster Schritt |
|---|---|---|---|
| Icon-Clipping-Fix der Titelleiste | Hoch | – | Neuinstallation des neuesten Installers, dann visuell prüfen |
| Vierfarbige Quick-Action-Badges | Mittel | – | Neuinstallation, dann visuell prüfen |

### OFFEN:

| Aufgabe | Priorität | Abhängigkeit | Nächster Schritt |
|---|---|---|---|
| Echtes PDF-Seiten-Rendering im Dokument-Workspace | Mittel | Keine harte, aber substanzieller Aufwand (PDF.js-artige Bibliothek + Koordinaten-Mapping) | Aufwand/Nutzen bewerten, ggf. als eigenen Workstream planen |
| llama.cpp-Runtime-Integration für Model Evaluation | Niedrig-Mittel | Architektur bereits vorbereitet (`LocalLLMProvider`-Protocol, `Settings.local_ai_runtime`) | `LlamaCppLocalLLMProvider`-Klasse schreiben, Dispatch in `build_local_llm_provider` ergänzen, real benchmarken |
| Weitere Modellfamilien (Mistral) real testen | Niedrig | Keine | `ollama pull mistral` + Benchmark analog zu gemma2:2b |
| Systematischer Visual-QA-Loop über mehrere Seiten/Zustände + Auflösungen | Mittel | Screenshot-Technik existiert, aber nur für Einzelzustände genutzt | Navigation + wiederholtes Screenshotten für 1366×768 und 1920×1080 systematisch durchführen |
| Tiefere Integration weiterer Tools direkt aus dem Chat heraus | Mittel | Chat/Dokument-Workspace-Grundlage vorhanden | Konkrete nächste Tools identifizieren (z. B. Fristen-Check direkt aus Chat) |
| Root-Markdown-Dateien (`PILOT_CHECKLIST.md` etc.) im Detail auf veraltete Aussagen prüfen | Niedrig | Keine | Gezielt gegenlesen, nicht pauschal |
| Feedback→Priorisierung→Release-Pipeline vertiefen | Niedrig (für Pilotphase als ausreichend bewertet) | – | Erst bei Bedarf während der Pilotphase |

### BLOCKIERT:

| Aufgabe | Grund | Nächster Schritt |
|---|---|---|
| Installation des neuesten Installers (Icon-Fix + Badges) | Silent-Install stockt wiederholt, vermutlich Windows-Defender-Scan-Kontention | Erneuter Versuch zu anderem Zeitpunkt, ODER Defender-Ausnahme nach Nutzerzustimmung |
| Visual QA mit Browser-Tool für HTTP-Seiten | Kein Browser-Automatisierungstool verfügbar (vom Nutzer abgelehnt) | Nur relevant, falls der Nutzer die Chrome-Erweiterung doch noch aktivieren möchte |

### BENÖTIGT BENUTZERENTSCHEIDUNG:

| Aufgabe | Beschreibung | Optionen |
|---|---|---|
| **Logo-/Primärfarbe grün vs. Navy** | `#101828` ist per Pixelmessung aus dem echten offiziellen Logo verifiziert (Navy), aber ein neuerer Auftrag verlangt „grünes Logo" | (a) Neue/andere offizielle Logo-Datei bereitstellen, dann pixelgenau wie beim vorherigen Mal umsetzen; (b) klären, ob Navy tatsächlich korrekt ist und die „grün"-Erwartung fallengelassen werden soll |
| **Windows-Defender-Ausnahme für den Installer-Ordner** | Würde den Silent-Install-Stall vermutlich beheben, ist aber eine sicherheitsrelevante Systemänderung | Nur nach ausdrücklicher Zustimmung einrichten |
| **Multi-Kanzlei-Profile / Cross-Tenant-Unterstützung** (`PROMPT38_ANALYSIS.md`) | Analyse abgeschlossen, Implementierung nie begonnen – ist das noch relevant? | Falls ja: eigener Planungs-/Umsetzungs-Workstream |

---

## M. Arbeitsweise für zukünftige Claude-Prompts

**Grundprinzip:** Analyse → Planung → Implementierung → Test →
Fehleranalyse → Korrektur → Test → Commit.

Größere, klar abgegrenzte Aufgaben sollen möglichst autonom bearbeitet
werden (Claude Code kann eigenständig Dateien lesen, Code ändern, Tests
ausführen, Builds erstellen und lokale Commits setzen, ohne bei jedem
kleinen Schritt nachzufragen).

**Aber, unbedingt einzuhalten:**

- Keine unnötigen Architekturumbauten – bestehende, funktionierende
  Logik nicht ohne triftigen Grund anfassen.
- Keine bereits gelösten Probleme erneut lösen (siehe Abschnitt H) –
  erst den bestehenden Code und `.agentic/`-Dokumentation prüfen.
- Keine alten Architekturentscheidungen aus historischen
  Markdown-Abschnitten wieder als aktuell einführen (siehe Abschnitt K).
- Keine destruktiven Änderungen (git reset --hard, force push, Löschen
  von Daten/Dateien) ohne vorherige Absicherung.
- Keine sicherheitsrelevanten Windows-Systemänderungen ohne
  ausdrückliche Zustimmung – **insbesondere Windows Defender NICHT
  eigenständig deaktivieren oder Ausnahmen einrichten**.
- **Kein `git push`** ohne ausdrückliche Anweisung.
- Bei Unsicherheit über den aktuellen Zustand: erst den tatsächlichen
  Code/Git-Stand prüfen, nicht raten oder aus einer möglicherweise
  veralteten Dokumentation übernehmen.
- Nur synthetische Testdaten verwenden, niemals echte Mandantendaten.
- Volle Testsuite nach Änderungen laufen lassen (nicht nur geänderte
  Dateien isoliert – es gab bereits einen echten
  Testisolationsvorfall, siehe Abschnitt H).

---

## N. Aktueller nächster Schritt (konkret)

Der sinnvollste unmittelbare nächste Schritt ist **NICHT** eine neue
Funktion, sondern:

**Den bereits fertig gebauten, neuesten Installer
(`dist/installer/Lexono_Setup.exe`, enthält Icon-Clipping-Fix +
Quick-Action-Badges) zu einem späteren Zeitpunkt erfolgreich real
installieren und per Screenshot visuell verifizieren** – der Build
selbst ist nachweislich in Ordnung (fehlerfreier Compile), nur der
lokale Silent-Install-Vorgang stockte wiederholt. Ein einfacher erneuter
Versuch zu einem anderen Zeitpunkt (ggf. nachdem andere
Hintergrundprozesse abgeklungen sind) ist der naheliegendste erste
Versuch, BEVOR eine Defender-Ausnahme in Erwägung gezogen wird.

Danach, in ungefährer Prioritätsreihenfolge:

1. Klärung der Logo-/Primärfarben-Frage (benötigt Nutzerentscheidung)
2. Bei Bedarf: weitere, tiefere Chat-Integration bestehender Tools
3. Bei Bedarf: llama.cpp-Runtime-Integration für die Model-Evaluation

---

## O. LEXONO – CURRENT STATE (Kompaktzusammenfassung)

- **Architektur:** Lokale KI (Ollama, `qwen2.5:1.5b`) zwingend vor
  Claude; lokale Pseudonymisierung (Presidio) zwingend; Cloud-Zugriff in
  Produktion über den selbst betriebenen Lexono-Gateway; natives
  Windows-Fenster (pywebview + WebView2) mit eigener, nicht-nativer
  Titelleiste; FastAPI/Uvicorn-Backend, SQLite-Datenbank.
- **Aktueller Build (Quellcode/HEAD):** Commit `da70170` auf `main`,
  Working Tree sauber, 31 Commits vor `origin/main`, kein Push.
- **Aktueller Installer-Zustand:** neuester Installer
  (`dist/installer/Lexono_Setup.exe`, ~525MB, kompiliert 09:39 Uhr)
  fertig gebaut, aber NICHT erfolgreich installiert (vier
  Silent-Install-Stalls, vermutlich Windows-Defender-bedingt, nicht
  eigenständig behoben).
- **Aktuell installierter/laufender Stand:** vorheriger (dritter) Build
  (07:53 Uhr) – enthält Titelleisten-Fix, Logo-Entfernung aus der
  Titelleiste, KI-Ladezustand; Schließen-Button vom Nutzer real
  bestätigt funktionierend.
- **Zuletzt implementierte Änderungen (im Quellcode, noch nicht im
  installierten Build):** Icon-Clipping-Fix der Titelleiste (20px
  statt 6px rechter Innenabstand), vierfarbige Quick-Action-Badges.
- **Noch nicht verifizierte Änderungen:** beide oben genannten Punkte –
  erst nach erfolgreicher Neuinstallation visuell zu bestätigen.
- **Offene Aufgaben:** siehe vollständige Tabellen in Abschnitt L.
- **Blocker:** Silent-Install-Stall (vermutlich Windows Defender,
  Ausnahme nur nach Nutzerzustimmung).
- **Benötigt Nutzerentscheidung:** Logo-/Primärfarbe grün vs. verifiziertes
  Navy `#101828`; ggf. Windows-Defender-Ausnahme; Relevanz von
  Multi-Kanzlei-Profilen.
- **Nächster Schritt:** neuesten Installer zu anderem Zeitpunkt real
  installieren und visuell verifizieren.
- **Tests:** 1487 passed, 1 skipped, 0 failed (aktuell gültig für HEAD).
