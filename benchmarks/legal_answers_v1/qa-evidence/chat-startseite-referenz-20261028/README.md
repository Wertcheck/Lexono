# Chat-Startseite nach Referenzbild (28.10.2026)

**Referenz:** `referenz-chat-startseite.png` (vom Owner nachgereicht).

**Aufnahmen:** Alle Bilder stammen aus der laufenden Anwendung (`run.py serve --no-window`):

- Isolierte Instanz, toter Cloud-Endpunkt.
- Ausschließlich synthetische Daten: ein Wegwerf-Admin und drei Akten `QA-SYNTH …` für „Akte öffnen“.
- Headless Edge per CDP mit DPR 1 und **sichtbaren** Bildlaufleisten.
- Die Uhrzeit ist im Browser auf 20 Uhr gesetzt, damit „Guten Abend.“ erscheint.

**Stände und Skript:**

- **Vorher:** sauberer Worktree auf `02553d0`. **Nachher:** Arbeitsstand. Gleiche Daten und Schritte.
- Skript: `chatstart_shots.py`; Messwerte in `*/messwerte_*.json`.

## Bestandsaufnahme (vorher)

- Die vier Karten waren **hoch und schmal**: 183×177 px bei 1920×1080, 155×195 bei 960×600. Das Icon stand oben, der
  Titel brach um.
- Die Inhalte waren auf die 820-px-Chatspalte begrenzt.
- Die vierte Karte war „Weitere Prompts“, ein zweiter Link zur Prompt-Verwaltung, statt „Akte öffnen“.
- Der Button oben rechts hieß „Standard-Prompts bearbeiten“, mit Drucker-Icon.
- Die Begrüßung war klein (28 px).
- Die Eingabe lag unter einer Trennlinie und zeigte im Fokus einen dunklen Rahmen.
- **Fehler bei 960×600:** Der schwebende Datenschutz-Button lag **über dem Senden-Button** (Messwert
  `eckbutton_ueberlappt_senden: true`).

## Umsetzung

**Geänderte Dateien:**

- `chat.html`: Startbereich, Klasse `chat-panel--start`, Handler.
- `app.css`: neuer Block, nur unter `.chat-panel--start`.
- Neuer Test.

Laufende Unterhaltungen, der Dokument-Workspace und die kompakten Schnellaktionen bleiben unverändert.

**Begrüßung:** „Guten Abend.“ groß und kräftig (bis 50 px). Die Unterzeile „Wie kann ich Sie heute unterstützen?“ ist
groß und hellgrau. Die bestehende tageszeitabhängige Begrüßung bleibt erhalten.

**„Standard-Prompts verwalten“:** grün umrandete Pille mit Plus, rechts oberhalb der Karten. Sie führt zur
**bestehenden** Verwaltung `/dashboard/library/prompts` (bearbeiten, anlegen, löschen).

**Karten:**

- Kompakt und waagerecht: Icon im Kreis links, Titel und kurze Beschreibung rechts. 280×98 px bei 1920×1080, statt
  183×177.
- Dezente Pastellflächen mit feiner Kontur in Grün, Blau, Violett und Orange.
- Icons: Dokument, Stift, Liste, Ordner.
- Die ganze Karte ist die Aktion, mit Hover (Kontur und leichter Schatten) und sichtbarem Tastaturfokus.
- Keine Pfeile und kein Drei-Punkte-Menü. Das Menü der Referenz hätte keine echte Funktion, deshalb wurde es bewusst
  weggelassen.

**Was die Karten auslösen:**

| Karte | Aktion |
|---|---|
| „Dokument analysieren“, „Schriftsatz erstellen“, „Zusammenfassen“ | Unverändert: Prompt ins Eingabefeld, bei Bedarf Dateiauswahl |
| **„Akte öffnen“** | Öffnet eine kompakte Aktenauswahl. „Öffnen“ nutzt den **bestehenden** Einstieg `/dashboard/chat?new=1&matter=<id>` („Mit dieser Akte im Chat arbeiten“): Die Akte wird für die erste Nachricht vorgemerkt. Ohne Akten führt die Karte zur Aktenliste. Escape oder ein Klick außerhalb schließt die Auswahl. |

**Eingabe:**

- Breit (bis 1260 px), weiß, mit 18-px-Rundung, feiner Kontur und weichem Schatten, ohne Trennlinie.
- Links ein runder grauer Plus-Button für Anhänge, rechts das Mikrofon und ein runder grüner Senden-Button mit Pfeil
  nach oben.
- Platzhalter „Nachricht an Lexono …“.
- Upload, Spracheingabe, Senden, „Namen schützen“ und Briefkopfauswahl sind unverändert, ebenso alle IDs und Handler.
- Bei Fensterbreiten bis 1100 px gibt es rechts Platz für den Datenschutz-Button: Die Überdeckung bei 960×600 ist
  behoben.

**Hintergrund:** sehr dezente radiale Verläufe in Grün und Blau.

**Responsiv:**

- unter 1360 px Breite: Karten 2×2;
- unter 760 px Höhe: kompaktere Karten und Eingabe;
- unter 720 px Breite: eine Spalte.

**Dunkelmodus:** getönte dunkle Karten mit hellen Icons, die Eingabe auf dunkler Fläche. Dokumentvorschau und Editor
sind nicht betroffen und bleiben weiß.

## Ergebnisse (`nachher/messwerte_nachher.json`)

| Größe | Karte (B×H) | Eingabe sichtbar | Überlappung Karten/Eingabe | Datenschutz-Button über Senden | Querscroll | JS-Fehler |
|---|---|---|---|---|---|---|
| 1920×1080 hell/dunkel | 280×98 (4 in einer Reihe) | ja | nein | nein | nein | keine |
| 1280×720 hell/dunkel | 373×69 (2×2) | ja | nein | nein | nein | keine |
| 960×600 hell/dunkel | 325×88 (2×2) | ja | nein | **nein** (vorher ja) | nein | keine |

**Weitere Aufnahmen:**

- Hover und Fokus (`02`);
- Aktenauswahl „Akte öffnen“ (`03`);
- übernommener Prompt (`04`, Eingabefeld enthält „Bitte erstelle einen Schriftsatz-Entwurf …“).

## Tests

- **Neu, `tests/test_web_chat_start_reference.py`:**
  - Reihenfolge und Titel der vier Karten;
  - Begrüßung;
  - Link „Standard-Prompts verwalten“ zur bestehenden Verwaltung;
  - kein Drei-Punkte-Menü, keine Pfeile;
  - „Akte öffnen“ nutzt den bestehenden Einstieg (Akte wird vorgemerkt), ohne Akten Link zur Aktenliste;
  - der Prompt-Handler bindet nur Karten mit `data-prefill`;
  - CSS nur unter `.chat-panel--start`;
  - runde Eingabe-Buttons, Platz für den Datenschutz-Button;
  - Startlayout nur ohne aktive Unterhaltung.
- **Web-, Chat-, Pop-up-, Design- und Dokumentvorschau-Tests** (54 Dateien): **1380 bestanden**, 1 xfailed,
  **4 fehlgeschlagen**. Die 4 Fehler sind ausschließlich die bekannten Logo-Tests in `test_design_refresh.py`: Die
  Logos sind im Arbeitsbaum fremd gelöscht und wurden bewusst nicht wiederhergestellt.

## Bekannte Abweichungen von der Referenz

- **Kopfzeile und Sidebar sind unverändert.** Ihre Größen sind global und gelten auf allen Seiten.
  - Die Referenz zeigt eine etwas höhere Kopfzeile und eine breitere Sidebar mit größerer Schrift.
  - Die Icons rechts in der Kopfzeile (Posteingang, Benachrichtigungen, Theme, Abmelden) gehören zur bestehenden
    Oberfläche und bleiben.
  - Eine globale Anpassung würde alle Seiten betreffen und ist nicht Teil dieses Auftrags.
- **Fehlendes Drei-Punkte-Menü:** Das Menü der Referenz neben den Karten ist **nicht** umgesetzt, weil es keine echte
  Funktion hätte.
- **„Namen schützen“** steht weiterhin klein unter der Eingabe. Es ist eine bestehende Datenschutzfunktion, die in der
  Referenz nicht zu sehen ist.
- **Sidebar-Inhalte:** Die Referenz zeigt „Cloud-KI (aktiv)“ und einen Zähler bei „Aufgaben & Fristen“. In der
  Testinstanz ist die Cloud-KI bewusst nicht konfiguriert, und es gibt keine Aufgaben. Diese Anzeigen kommen aus echten
  Daten.
- **Nicht geprüft:** Die installierte EXE enthält diese Änderung nicht. Die native Prüfung im Desktopfenster steht aus;
  dafür sind Neubau und Installation nötig.
