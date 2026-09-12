# Baut die Windows-Installation Ende-zu-Ende (Prompt 36): PyInstaller-Bündel
# + Inno-Setup-Installer. Reine Build-Orchestrierung, keine Anwendungslogik.
#
# Voraussetzungen:
# - Python-venv mit Build-Abhängigkeiten: pip install -e ".[build]"
# - Inno Setup 6 installiert (https://jrsoftware.org/isinfo.php),
#   ISCC.exe im Standardpfad oder im PATH.
#
# Nutzung (aus dem Projekt-Root):
#   powershell -ExecutionPolicy Bypass -File windows\build.ps1

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

Write-Host "== 1/2: PyInstaller-Build (dist\Lexono\) =="
pyinstaller windows\lexono.spec --distpath dist --workpath build --noconfirm
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller-Build fehlgeschlagen (Exit-Code $LASTEXITCODE)."
}

Write-Host "== 2/2: Inno-Setup-Installer (dist\installer\) =="
$IsccCandidates = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
    "iscc"
)
$Iscc = $IsccCandidates | Where-Object { Test-Path $_ -ErrorAction SilentlyContinue } | Select-Object -First 1
if (-not $Iscc) {
    $Iscc = "iscc"  # letzter Versuch: im PATH
}

& $Iscc "windows\installer.iss"
if ($LASTEXITCODE -ne 0) {
    throw "Inno-Setup-Compiler fehlgeschlagen (Exit-Code $LASTEXITCODE) - ist Inno Setup 6 installiert?"
}

Write-Host "Fertig. Installer liegt unter dist\installer\."
