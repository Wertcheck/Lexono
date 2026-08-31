# Laedt den offiziellen Microsoft-Edge-WebView2-"Evergreen Bootstrapper"
# herunter und legt ihn unter windows\vendor\webview2\ ab - genau der Ort,
# den windows\installer.iss anschliessend in den Inno-Setup-Installer
# aufnimmt (siehe dort). NICHT im Repository versioniert (windows/vendor/
# ist in .gitignore) - wie das Tesseract-Bundle ein bei Bedarf neu
# erzeugbares Build-Artefakt, kein Quellcode.
#
# Hintergrund (ARCHITECTURE.md §70/Pilot-Readiness-Review, offener Punkt
# "WebView2 muss auf einem normalen Kanzleilaptop nicht manuell
# nachinstalliert werden"): bisher pruefte run.py._is_webview2_runtime_available()
# nur per Registry, ob die Runtime vorhanden ist, und zeigte bei Fehlen
# lediglich einen manuellen Download-Link - keine automatische Installation.
#
# Warum der kleine "Evergreen Bootstrapper" (~1,8 MB) statt des grossen
# "Evergreen Standalone Installer" (~130-180 MB, vollstaendig offline):
# der Bootstrapper laedt die eigentliche Runtime bei der Installation live
# von Microsofts CDN nach - deutlich kleinerer Installer-Download fuer die
# Kanzlei, akzeptabler Kompromiss, da die Anwendung ohnehin Internetzugang
# fuer die Cloud-KI-Anbindung (Lexono-Gateway, siehe ARCHITECTURE.md §70)
# braucht. Der Standalone-Installer waere die robustere Wahl fuer komplett
# offline durchgefuehrte Installationen - bei Bedarf spaeter nachruestbar,
# ohne installer.iss strukturell zu aendern (nur die Quelldatei und der
# Aufrufparameter waeren zu tauschen).
#
# Lizenz: Microsoft erlaubt die Weiterverteilung des Bootstrappers
# ausdruecklich als Teil einer Drittanwendungs-Installation (siehe
# Microsoft Edge WebView2 Distribution-Bedingungen, THIRD_PARTY_NOTICES.md).
#
# Nutzung (aus dem Projekt-Root):
#   powershell -ExecutionPolicy Bypass -File windows\fetch_webview2.ps1
#
# Voraussetzung: Internetzugang. Kein Admin-Recht noetig (der Bootstrapper
# selbst fordert bei der spaeteren AUSFUEHRUNG ggf. eine UAC-Bestaetigung
# an, nicht dieses Download-Skript).

$ErrorActionPreference = "Stop"

$VendorDir = Join-Path $PSScriptRoot "vendor\webview2"
$TargetExe = Join-Path $VendorDir "MicrosoftEdgeWebview2Setup.exe"

# Offizieller, permanenter Microsoft-Kurzlink fuer den Evergreen
# Bootstrapper (dokumentiert von Microsoft fuer genau diesen
# Redistribution-Anwendungsfall).
$DownloadUrl = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"

New-Item -ItemType Directory -Force -Path $VendorDir | Out-Null

Write-Host "Lade WebView2-Evergreen-Bootstrapper herunter..."
Invoke-WebRequest -Uri $DownloadUrl -OutFile $TargetExe -UseBasicParsing

if (-not (Test-Path $TargetExe)) {
    throw "Download fehlgeschlagen: $TargetExe wurde nicht erzeugt."
}

$SizeKb = [math]::Round((Get-Item $TargetExe).Length / 1KB, 1)
Write-Host "OK: $TargetExe ($SizeKb KB)"
