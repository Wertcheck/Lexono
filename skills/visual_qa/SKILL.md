# Skill: Visual QA Loop

## Zweck

UI-Änderungen tatsächlich visuell verifizieren statt nur den Code zu
lesen.

## Voraussetzungen

Lexono ist eine native pywebview/WebView2-Windows-App, kein Browser-Tab -
ein Browser-Automatisierungstool (Claude-in-Chrome) greift hier NICHT.
Stattdessen (seit 01.09. real erprobt, siehe `.agentic/VISUAL_QA.md` für
den vollständigen Fund-Verlauf): PowerShell + .NET P/Invoke kann das
native Fenster sowohl fotografieren ALS AUCH tatsächlich bedienen
(Klicks, Tastatureingaben) - kein Browser-Tool nötig.

## Vorgehensweise

1. Anwendung starten (dev via `python run.py serve` für sofortige
   Source-Änderungen ohne Rebuild, ODER installierter Build für reale
   Verifikation).
2. Laufendes Fenster verifizieren: `Get-Process | Where-Object {
   $_.MainWindowTitle -eq "Lexono" }` - NIEMALS die PID von
   `Start-Process` blind verwenden (siehe Fallstrick unten).
3. Screenshot per `GetWindowRect` + `CopyFromScreen`
   (vollständiges Codebeispiel: `.agentic/VISUAL_QA.md`).
4. Für Zustände hinter einem Login/Klick: `SetForegroundWindow` +
   `[System.Windows.Forms.Cursor]::Position` + `mouse_event`
   (P/Invoke, `MOUSEEVENTF_LEFTDOWN`/`_LEFTUP`) für Klicks,
   `[System.Windows.Forms.SendKeys]::SendWait("...")` für Texteingabe -
   Koordinaten relativ zum per `GetWindowRect` ermittelten
   Fenster-Ursprung. Nach jedem Klick kurz warten (300-800ms) und bei
   Bedarf zwischendurch screenshotten, um die nächste Klickposition zu
   verifizieren, statt blind mehrere Klicks hintereinander zu setzen.
5. Screenshot bei 1366×768 UND 1920×1080 erzeugen, sofern die
   tatsächliche Bildschirmauflösung dies zulässt - VORHER mit
   `[System.Windows.Forms.Screen]::AllScreens` prüfen. In dieser
   konkreten Entwicklungsumgebung ist der Bildschirm nur 1024×768 groß,
   beide Zielauflösungen sind hier NICHT erreichbar (kein
   Anwendungsfehler, reine Umgebungsgrenze - siehe VISUAL_QA.md).
6. Mit Referenzbild/Vorgabe vergleichen.
7. Abweichungen konkret benennen (nicht nur „sieht komisch aus“).
8. Korrigieren, erneut prüfen – nicht beim ersten akzeptablen Ergebnis
   stoppen.

## Bekannte Fallstricke (siehe `.agentic/VISUAL_QA.md` für Details)

- Falsche PID (von `Start-Process`) → Garbage-Screenshot eines fremden
  Fensters. Immer über `MainWindowTitle` verifizieren.
- Erzwungenes `ShowWindow`/`SetForegroundWindow` unmittelbar vor einem
  Screenshot kann mit der `pywebviewready`-Aktivierung der eigenen
  Titelleiste kollidieren (leere Titelleiste im Screenshot trotz real
  funktionierendem Build) - ein einzelner solcher Screenshot ist KEIN
  Bugbeweis, nur ein wiederholter, reproduzierbarer Befund zählt.
- `MoveWindow` auf eine Zielgröße, die größer als der tatsächliche
  Bildschirm ist, wird nicht wie angefordert ausgeführt (kein Fehler,
  einfach physisch nicht möglich) - vorher immer die reale
  Bildschirmauflösung prüfen, sonst werden die Ergebnisse fälschlich
  als "seltsames App-Verhalten" fehlinterpretiert.

## Fallback

Wenn auch diese Technik nicht greift (z. B. kein GUI-Zugriff in der
Umgebung überhaupt): Status als NV dokumentieren (siehe
`.agentic/VISUAL_QA.md`), keine unbelegte „sieht gut aus“-Behauptung
aufstellen.

## Relevante Dateien

`.agentic/VISUAL_QA.md`
