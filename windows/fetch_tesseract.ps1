# Laedt ein eigenstaendiges, portables Tesseract-OCR (inkl. aller
# Laufzeit-DLLs + deutscher/englischer Sprachdaten) herunter und legt es
# unter windows\vendor\tesseract\ ab - genau der Ort, den
# windows\lexono.spec anschliessend in den PyInstaller-Build aufnimmt
# (siehe dort). NICHT im Repository versioniert (windows/vendor/ ist in
# .gitignore) - wie das spaCy-/Embedding-Modell ein bei Bedarf neu
# erzeugbares Build-Artefakt, kein Quellcode.
#
# Hintergrund (Pilot-Finding, siehe FUTURE_ROADMAP.md/RELEASE_NOTES.md):
# Tesseract ist eine externe C++-Anwendung, kein Python-Paket - ohne dieses
# Buendel muesste jede Kanzlei es manuell separat installieren, sonst
# schlaegt jede OCR-Anfrage mit TesseractNotFoundError fehl (Pilot-Finding).
#
# Warum ueber conda-forge statt des offiziellen UB-Mannheim-NSIS-Installers:
# der NSIS-Installer verlangt zur Ausfuehrung (auch mit /S) administrative
# Rechte (UAC) und laesst sich nicht zerlegen, ohne selbst ausgefuehrt zu
# werden. conda-forge liefert dieselben, aus identischem Tesseract-Quellcode
# gebauten Binaerdateien als reines Archiv (kein Installer, keine Rechte
# noetig) inkl. korrekt aufgeloester Abhaengigkeiten (leptonica, libcurl,
# libarchive, ...). Lizenz: Tesseract selbst ist Apache-2.0, alle
# mitgelieferten Laufzeitbibliotheken sind BSD/MIT/Apache-lizenziert -
# `libarchive` wird bewusst in der "lgpl"-Buildvariante bezogen (nicht
# "gpl"), um jede GPL-Beruehrung auszuschliessen, obwohl beide laut
# conda-forge-Metadaten technisch BSD-2-Clause sind (der Namensunterschied
# bezieht sich auf optionale, mitkompilierte Codec-Faehigkeiten, nicht auf
# die Lizenz von libarchive.dll selbst) - siehe THIRD_PARTY_NOTICES.md im
# Zielordner.
#
# Sprachdaten: "tessdata_fast" (kleine, fuer Fliesstext ausreichend genaue
# Modelle, ~16 MB fuer deu+eng+osd) statt "tessdata"/"tessdata_best"
# (deutlich groesser) - Groessenkompromiss, jederzeit durch Austausch der
# Dateien in windows\vendor\tesseract\tessdata\ nachtraeglich aenderbar.
#
# Nutzung (aus dem Projekt-Root):
#   powershell -ExecutionPolicy Bypass -File windows\fetch_tesseract.ps1
#
# Voraussetzung: Internetzugang. Kein Admin-Recht noetig.

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VendorDir = Join-Path $ProjectRoot "windows\vendor\tesseract"
$WorkDir = Join-Path $ProjectRoot "build_tools"
$MicromambaExe = Join-Path $WorkDir "micromamba.exe"
$MambaRoot = Join-Path $WorkDir "mamba_root"
$EnvDir = Join-Path $WorkDir "tess_env"

# Feste, geprueft funktionierende micromamba-Version (kein Installer, kein
# Admin-Recht, ein einzelnes statisch gelinktes .exe) - bewusst NICHT
# "latest" (beim Aufbau dieses Skripts real reproduzierter Absturz einer
# "latest"-Bootstrap-Variante auf mind. einer Testmaschine; diese exakte
# Version wurde funktionierend verifiziert).
$MicromambaVersion = "2.0.5-0"
$MicromambaUrl = "https://github.com/mamba-org/micromamba-releases/releases/download/$MicromambaVersion/micromamba-win-64"

# Sprachdaten-Quelle (offizielles Tesseract-Projekt, Apache-2.0).
$TessdataBaseUrl = "https://github.com/tesseract-ocr/tessdata_fast/raw/main"
$Languages = @("eng", "deu", "osd")

# Von tesseract.exe/tesseract55.dll tatsaechlich benoetigte Laufzeit-DLLs
# (per rekursiver PE-Import-Analyse ermittelt - siehe Kommentar am Ende
# dieses Skripts fuer die Nachvollziehbarkeit/Neuermittlung bei einem
# Versions-Update). Bewusst NICHT der komplette conda-Bin-Ordner (~120 MB,
# enthaelt viele fuer Tesseract irrelevante Bibliotheken).
$RequiredDlls = @(
    "Lerc.dll", "archive.dll", "charset.dll", "deflate.dll", "gif-7.dll",
    "iconv.dll", "icudt78.dll", "icuuc78.dll", "jpeg8.dll",
    "leptonica-1.87.0.dll", "libbz2.dll", "libcrypto-3-x64.dll",
    "libcurl.dll", "liblzma.dll", "libpng16.dll", "libsharpyuv.dll",
    "libssh2.dll", "libwebp.dll", "libwebpmux.dll", "libxml2.dll",
    "lz4.dll", "msvcp140.dll", "openjp2.dll", "psl-5.dll",
    "tesseract55.dll", "tiff.dll", "vcruntime140.dll",
    "vcruntime140_1.dll", "zlib.dll", "zstd.dll"
)

New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null

if (-not (Test-Path $MicromambaExe)) {
    Write-Host "== 1/4: micromamba herunterladen =="
    Invoke-WebRequest -Uri $MicromambaUrl -OutFile $MicromambaExe -UseBasicParsing
}
& $MicromambaExe --version | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "micromamba.exe laesst sich nicht ausfuehren (Exit-Code $LASTEXITCODE)."
}

Write-Host "== 2/4: Conda-Umgebung mit Tesseract + Abhaengigkeiten aufloesen =="
if (Test-Path $EnvDir) {
    Remove-Item -Recurse -Force $EnvDir
}
$env:MAMBA_ROOT_PREFIX = $MambaRoot
& $MicromambaExe create -y -p $EnvDir -c conda-forge "tesseract" "libarchive=3.8.9=lgpl*"
if ($LASTEXITCODE -ne 0) {
    throw "micromamba create fehlgeschlagen (Exit-Code $LASTEXITCODE)."
}

Write-Host "== 3/4: benoetigte Binaerdateien nach windows\vendor\tesseract kopieren =="
if (Test-Path $VendorDir) {
    Remove-Item -Recurse -Force $VendorDir
}
New-Item -ItemType Directory -Force -Path "$VendorDir\bin" | Out-Null
New-Item -ItemType Directory -Force -Path "$VendorDir\tessdata" | Out-Null

$BinSrc = Join-Path $EnvDir "Library\bin"
Copy-Item (Join-Path $BinSrc "tesseract.exe") "$VendorDir\bin\" -Force
foreach ($dll in $RequiredDlls) {
    $src = Join-Path $BinSrc $dll
    if (-not (Test-Path $src)) {
        throw "Erwartete DLL fehlt im conda-Bin-Ordner: $dll (Versions-/Abhaengigkeitswechsel? RequiredDlls in diesem Skript pruefen/neu ermitteln.)"
    }
    Copy-Item $src "$VendorDir\bin\" -Force
}

Write-Host "== 4/4: Sprachdaten (deu/eng/osd, tessdata_fast) herunterladen =="
foreach ($lang in $Languages) {
    $dest = Join-Path "$VendorDir\tessdata" "$lang.traineddata"
    Invoke-WebRequest -Uri "$TessdataBaseUrl/$lang.traineddata" -OutFile $dest -UseBasicParsing
}

@"
Dieses Verzeichnis enthaelt Binaerdateien Dritter, gebuendelt mit Lexono:

- Tesseract OCR (tesseract.exe, tesseract55.dll) - Apache License 2.0
  https://github.com/tesseract-ocr/tesseract
- Sprachdaten (tessdata_fast: eng/deu/osd) - Apache License 2.0
  https://github.com/tesseract-ocr/tessdata_fast
- Laufzeitbibliotheken (leptonica, libcurl, libarchive [lgpl-Variante],
  libpng, libtiff, libwebp, libjpeg-turbo, openjpeg, zlib, zstd, lz4, bzip2,
  liblzma, libxml2, libssh2, ICU, Microsoft Visual C++ Runtime) - jeweils
  BSD-/MIT-/Apache-/LGPL-lizenziert, bezogen ueber den conda-forge-Kanal
  (https://conda-forge.org). Vollstaendige Lizenztexte der einzelnen
  Pakete: https://github.com/conda-forge/<paketname>-feedstock

Neu erzeugt durch windows\fetch_tesseract.ps1 - dieses Verzeichnis ist
KEIN Teil des versionierten Quellcodes (siehe .gitignore).
"@ | Out-File -FilePath "$VendorDir\THIRD_PARTY_NOTICES.md" -Encoding utf8

Write-Host "Fertig. $VendorDir ist bereit fuer windows\build.ps1 / windows\lexono.spec."
