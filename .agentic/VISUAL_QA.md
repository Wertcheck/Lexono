# VISUAL_QA – Screenshot-/Referenzabgleich

Status: **TEILWEISE VERFÜGBAR (seit 01.09., echte Kapazitätserweiterung)**
– kein Browser-Automatisierungstool verfügbar (Nutzer hat die Chrome-
Erweiterung für diese Session abgelehnt), ABER: für das native Windows-
Fenster der Desktop-App existiert eine echte, funktionierende
Screenshot-Technik (unten) - kein Browser-Tool nötig, da es sich um ein
natives Fenster handelt, kein Browser-Tab.

## Funktionierende Technik: natives Fenster fotografieren (01.09. entdeckt)

PowerShell + .NET `System.Drawing` kann den Bildschirminhalt eines
bestimmten Fensterbereichs als PNG sichern, das anschließend mit dem
`Read`-Tool tatsächlich VISUELL angesehen werden kann (das Tool liest
auch Bilddateien). Damit ist echte Visual QA für das native Fenster
möglich, ohne Browser-Tool.

```powershell
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class WinCap {
    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
    [DllImport("user32.dll")]
    public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);
    public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
}
"@

# WICHTIG: die korrekte PID ermitteln - NICHT die von Start-Process
# zurueckgegebene PID blind verwenden (das kann ein Launcher-/
# Wrapper-Prozess sein, dessen MainWindowHandle 0/ungueltig ist, was zu
# GARBAGE-Koordinaten fuehrt - real passiert am 01.09., siehe unten).
# Immer zuerst per MainWindowTitle verifizieren:
Get-Process | Where-Object { $_.ProcessName -match "kanzlei" } | Select-Object ProcessName, Id, MainWindowTitle

$proc = Get-Process -Id <VERIFIZIERTE_PID>
$handle = $proc.MainWindowHandle
[WinCap]::ShowWindow($handle, 9) | Out-Null       # SW_RESTORE, falls minimiert
[WinCap]::SetForegroundWindow($handle) | Out-Null
Start-Sleep -Milliseconds 800

$rect = New-Object WinCap+RECT
[WinCap]::GetWindowRect($handle, [ref]$rect) | Out-Null
$width = $rect.Right - $rect.Left
$height = $rect.Bottom - $rect.Top

$bitmap = New-Object System.Drawing.Bitmap $width, $height
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
# NUR den Fensterbereich erfassen (Left/Top/Width/Height), NIEMALS den
# gesamten Desktop - vermeidet, unbeteiligte sichtbare Inhalte auf dem
# Bildschirm des Nutzers zu erfassen.
$graphics.CopyFromScreen($rect.Left, $rect.Top, 0, 0, (New-Object System.Drawing.Size $width, $height))
$bitmap.Save("<pfad>.png", [System.Drawing.Imaging.ImageFormat]::Png)
```

Danach: `Read`-Tool auf die gespeicherte PNG-Datei anwenden - das Bild
wird tatsächlich angezeigt und kann inhaltlich beurteilt werden.

**Für Detailprüfung (z. B. Icon-Rendering in einer Fensterecke)**: einen
kleinen Ausschnitt croppen und mit `InterpolationMode.NearestNeighbor`
hochskalieren (z. B. 3-4x), bevor gespeichert wird - macht kleine
UI-Elemente (28×28px-Buttons, 14px-Icons) am Bildschirm klar erkennbar.

### Bekannte Fallstricke (real erlebt, 01.09.)

1. **Falsche PID → Garbage-Screenshot**: `Start-Process` gibt die PID des
   gestarteten Prozesses zurück - bei `python run.py serve` ist das
   NICHT zwingend derselbe Prozess, der später das native Fenster
   besitzt (pywebview kann intern weitere Prozesse involvieren). Ohne
   Verifikation über `MainWindowTitle` kann `GetWindowRect` auf einem
   Fenster mit Handle `0` oder einem völlig anderen, zufällig an
   denselben Koordinaten liegenden Fenster (z. B. der IDE/dem Terminal
   des Nutzers) aufgerufen werden - das Ergebnis sieht dann komplett
   anders aus (z. B. wurde einmal ein natives dunkles 3-Button-Fenster
   fotografiert, das mit ziemlicher Sicherheit gar nicht Lexono war).
   **Immer** `MainWindowTitle -eq "Lexono"` vor dem Screenshot prüfen.
2. **Dev-Modus-Start via `Start-Process` kann inkonsistentes Timing
   zeigen**: bei einem so gestarteten Dev-Fenster aktivierte sich die
   eigene Titelleiste (JS-Feature-Detection auf `window.pywebview.api`)
   in einem Testlauf gar nicht (leere weiße Leiste, kein Button
   sichtbar), obwohl dieselbe Logik am ECHTEN installierten Build zuvor
   nachweislich funktionierte. Ursache nicht abschließend geklärt -
   könnte am Start-Mechanismus liegen (nicht derselbe Pfad wie ein
   normaler Doppelklick-Start). **Für belastbare Aussagen immer den
   echten Installer-Build testen, nicht nur einen ad-hoc
   PowerShell-gestarteten Dev-Prozess.**

## Bereits real durchgeführte Prüfungen

- **01.09.**: Screenshot der laufenden installierten App (Login-Seite)
  zeigte einen ECHTEN, vorher unbekannten Bug: das Schließen-Icon der
  eigenen Titelleiste wurde am rechten Fensterrand sichtbar
  angeschnitten (zu knapper rechter Innenabstand, 6px). Per Zoom-Crop
  bestätigt (auch außerhalb des gemeldeten Fensterrands weiterhin
  abgeschnitten, also kein Screenshot-Artefakt). Behoben (Commit
  `e10c04e`, Abstand auf 20px erhöht) - erneute Verifikation über einen
  frischen Installer-Build steht aus (siehe PROJECT_STATE.md).

## Wichtige Einschränkung der Technik (01.09., später)

Bei wiederholten Screenshot-Versuchen desselben, vom Nutzer bereits als
funktionierend bestätigten Builds zeigte sich INKONSISTENTES Verhalten:
manchmal war die Titelleiste (Minimieren/Schließen) sichtbar, manchmal
komplett leer (kein Icon, weder Minimieren noch Schließen). Da der Nutzer
den Schließen-Button an genau diesem Build bereits real als
funktionierend bestätigt hat ("x button closes the app"), ist die
plausibelste Erklärung, dass `ShowWindow`/`SetForegroundWindow` direkt
vor dem Screenshot-Aufruf mit dem `pywebviewready`-Timing der Titelleisten-
Aktivierung (`app_titlebar.js`) kollidiert - NICHT, dass die Titelleiste
tatsächlich unzuverlässig ist. Bei einem normalen Programmstart (Nutzer
startet die App reibungslos einmal) tritt dieses Timing-Problem
vermutlich nicht auf.
**Konsequenz**: ein per dieser Technik aufgenommener Screenshot, der die
Titelleiste leer zeigt, ist NICHT als Beweis für einen echten Bug zu
werten - im Zweifel mehrfach neu aufnehmen oder, besser, den Nutzer direkt
fragen/warten lassen, statt aus einem einzelnen automatisierten Screenshot
eine falsche Fehlermeldung abzuleiten. Ein WIEDERHOLT (mehrfach in Folge)
angeschnittenes Element (wie der Schließen-Icon-Clipping-Fund, der auch
außerhalb des Fensterrands per Zoom bestätigt wurde) ist dagegen ein
belastbarer Befund.

## Neuer, UNBESTÄTIGTER Befund (01.09., später) – nicht als Bug werten

Bei einem systematischen Test der bislang fehlenden 1366×768/1920×1080-
Vergleiche (siehe unten) wurde `MoveWindow` (P/Invoke) genutzt, um das
laufende Fenster extern auf diese Zielgrößen zu setzen. Ergebnis
unerwartet: `GetWindowRect` nach dem Aufruf zeigte KEINE der angeforderten
Zielgrößen (angefordert 1920×1080, gemessen ~1044×788) - vermutlich
DPI-Virtualisierung zwischen einem DPI-unaware WinForms-Fenster und dem
aufrufenden PowerShell-Prozess, kein Befund über echtes App-Verhalten.

Zusätzlich zeigten ALLE Screenshots dieser Testreihe (inkl. eines
komplett frischen Programmstarts ohne jede externe Fenstermanipulation,
mehrfach wiederholt inkl. 18s Wartezeit) eine leere, titelleistenlose
Fensteroberkante UND einen auffälligen, konsistent positionierten
schwarzen Trennstrich mit vollständig leerem, weißem Bereich darunter
(ca. unteres Achtel des Fensters). Ein Versuch, dies wie beim
Icon-Clipping-Fund über eine erweiterte Aufnahme (150px über den
gemeldeten unteren Fensterrand hinaus) zu verifizieren, war NICHT
schlüssig - der erweiterte Bereich sah identisch aus wie der Bereich
knapp über dem gemeldeten Rand (durchgehend weiß), was sowohl "das ist
tatsächlich noch Fensterinhalt" als auch "das ist bereits ein
weißer/heller Desktop-Hintergrund hinter dem Fenster" erklären könnte -
im Gegensatz zum Icon-Clipping-Fall gibt es hier keinen Kontrastwechsel,
der die beiden Fälle unterscheidbar macht.

**Bewusst NICHT als bestätigter Bug gemeldet**, weil: (1) der Nutzer
denselben installierten Build bereits direkt und hands-on bestätigt hat
("x button closes the app"), was im Widerspruch zu einer echt fehlenden
Titelleiste steht; (2) die bekannte `pywebviewready`-Timing-Unschärfe
dieser Technik bereits dokumentiert ist; (3) der Erweiterungs-Test hier
technisch nicht schlüssig war (weiß auf weiß). Empfehlung: bei
Gelegenheit einmal echt mit eigenen Augen pruefen, ob die Titelleiste und
der komplette untere Fensterbereich normal aussehen - nicht ungeprüft als
Regression in OPEN_ISSUES.md übernehmen, nur als offener
Beobachtungspunkt vermerkt (siehe dort, Kategorie LOW).

## Neue Erkenntnis: echte UI-Automatisierung möglich (01.09., später)

Über PowerShell + `[System.Windows.Forms.Cursor]::Position` +
`mouse_event` (P/Invoke) + `SendKeys::SendWait` lässt sich das native
Fenster tatsächlich BEDIENEN (Login, Klicks auf echte UI-Elemente),
nicht nur fotografieren. Damit war es möglich, den Chat einzuloggen,
zu einem Dokument-Workspace zu navigieren und dessen echten,
gerenderten Zustand zu fotografieren - nicht nur die Login-Seite.
Voraussetzung: `SetForegroundWindow` vor jedem Klick, feste
Pixel-Koordinaten relativ zum zuvor per `GetWindowRect` ermittelten
Fenster-Ursprung (Layout ändert sich nicht zwischen Sessions, solange
Fenstergröße gleich bleibt).

## WICHTIGE KORREKTUR zur bisherigen 1366×768/1920×1080-Einschränkung

Die tatsächliche Bildschirmauflösung dieser Entwicklungsumgebung ist
NUR **1024×768** (`[System.Windows.Forms.Screen]::AllScreens`,
01.09. verifiziert). Das erklärt rückwirkend, warum `MoveWindow`-Aufrufe
auf 1366×768/1920×1080 nie die angeforderte Breite lieferten (siehe
oben, "unbestätigter Befund") - das war KEINE DPI-Virtualisierungs-
Anomalie, sondern schlicht eine physische Bildschirmgrenze: das Fenster
kann in dieser Umgebung gar nicht breiter als ~1024-1044px werden.
**Konsequenz**: 1366×768 und 1920×1080 sind in dieser konkreten
Sandbox/VM NICHT testbar - jeder Versuch, dies zu simulieren, liefert
falsche/irreführende Ergebnisse. Für echte Tests bei diesen Auflösungen
wäre eine Umgebung mit entsprechend größerem (virtuellem) Bildschirm
nötig. Dies ist eine Umgebungseinschränkung, kein Anwendungsfehler.

## Echter, bestätigter Layout-Bug gefunden UND behoben (01.09., später)

Bei der Navigation in den Dokument-Workspace (per echter UI-Automatisierung,
nicht nur HTTP) zeigte sich bei der tatsächlichen Fensterbreite dieser
Umgebung (~1024-1028px) ein reproduzierbarer Layout-Fehler: `.chat-panel`
(Nachrichten-Thread + Composer, bleibt im Dokument-Workspace bewusst
sichtbar, siehe `chat-shell--document-view`) hatte kein `min-width`,
während `.chat-document-pane` (40%) und `.chat-context-pane` (260px)
feste/prozentuale Breiten beanspruchten - der Chat-Thread wurde auf
einen schmalen Streifen (~150-200px) zusammengedrückt, Nachrichtentext
brach auf ein Wort pro Zeile um (Screenshot:
`lexono_document_workspace_click.png`).
**Behoben** (`app.css`, Commit `b92e1cb`): `.chat-panel` bekommt
`min-width: 240px`, `.chat-context-pane` von `260px` auf `220px`
reduziert. Vorher/Nachher per echtem Login+Klick in einer Dev-Instanz
verifiziert (`lexono_dev_docworkspace_fixed2.png` zeigt normalen,
mehrwortigen Zeilenumbruch statt Ein-Wort-pro-Zeile). Volle Testsuite
weiterhin grün (1487/1/0).

## Bestätigung: sechster Rebuild, echte Titelleiste UND Layout-Fix verifiziert (01.09., später)

Nach dem sechsten Installer-Rebuild (enthält den `.chat-panel`-Layout-Fix,
Commit `b92e1cb`) wurde per echter UI-Automatisierung erneut eingeloggt
und in den Dokument-Workspace navigiert. Der resultierende Screenshot
(`lexono_rebuild6_docworkspace.png`) zeigt in EINER Aufnahme:

- Titelleiste vollständig und korrekt gerendert (Minimieren- UND
  Schließen-Icon beide sichtbar, kein Anschnitt) - löst die zuvor als
  "unbestätigt" eingestufte Beobachtung (leere Titelleiste bei
  automatisierten Screenshots) endgültig auf: es war tatsächlich ein
  login-seiten-/timing-spezifisches Artefakt, kein echter Bug.
- Dokument-Workspace-Layout korrekt: Chat-Spalte mit normalem,
  mehrwortigem Zeilenumbruch (nicht mehr Ein-Wort-pro-Zeile), rechte
  Kontextleiste vollständig sichtbar (Schnellaktionen, Erkannte
  Mandantendaten mit Zähler, Dokumentstatus) - der `.chat-panel`-Fix
  wirkt auch im echten installierten Build, nicht nur im Dev-Vergleich.

Damit ist sowohl die frühere unbestätigte Beobachtung aufgeklärt als
auch der neue Layout-Fix im tatsächlich ausgelieferten Installer
bestätigt.

## Was weiterhin fehlt

- Kein Browser-Tool für Chat-UI-Seiten mit dynamischem Inhalt über HTTP
  (die native-Fenster-Technik oben funktioniert nur für das, was gerade
  sichtbar im Fenster gerendert ist - für einen systematischen Loop über
  viele Seiten/Zustände bräuchte es weiterhin Navigation + wiederholtes
  Screenshotten, technisch möglich, aber manuell pro Zustand).
- Kein Vergleich bei 1366×768 vs. 1920×1080 (Fenstergröße müsste dafür
  gezielt per `window.resize()`/`_NativeApi.resize_window_by` gesetzt
  werden, bisher nicht systematisch durchgeführt).
