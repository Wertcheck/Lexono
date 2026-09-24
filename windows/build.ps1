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
# Inno Setup 6 laesst sich maschinenweit ODER nur fuer den aktuellen
# Benutzer installieren. Die Benutzerinstallation landet unter
# %LOCALAPPDATA%\Programs und stand bisher NICHT in dieser Liste - auf einer
# so eingerichteten Maschine lief der PyInstaller-Schritt (Minuten!) sauber
# durch und der Build brach erst danach mit "Die Benennung 'iscc' wurde
# nicht ... erkannt" ab (real aufgetreten, 14.09.).
$IsccCandidates = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
    "${env:LOCALAPPDATA}\Programs\Inno Setup 6\ISCC.exe"
)
$Iscc = $IsccCandidates | Where-Object { Test-Path $_ -ErrorAction SilentlyContinue } | Select-Object -First 1
if (-not $Iscc) {
    # Letzter Versuch: im PATH. Vorher pruefen statt blind aufzurufen -
    # sonst ist die Fehlermeldung ein PowerShell-CommandNotFound-Stacktrace
    # statt eines verwertbaren Hinweises.
    if (Get-Command "iscc" -ErrorAction SilentlyContinue) {
        $Iscc = "iscc"
    }
    else {
        throw (
            "ISCC.exe (Inno Setup 6) nicht gefunden. Gesucht in:`n  - " +
            ($IsccCandidates -join "`n  - ") +
            "`n  - PATH`nInno Setup 6 installieren (https://jrsoftware.org/isinfo.php) " +
            "oder ISCC.exe in den PATH aufnehmen."
        )
    }
}
Write-Host "   ISCC: $Iscc"

& $Iscc "windows\installer.iss"
if ($LASTEXITCODE -ne 0) {
    throw "Inno-Setup-Compiler fehlgeschlagen (Exit-Code $LASTEXITCODE) - ist Inno Setup 6 installiert?"
}

Write-Host "Fertig. Installer liegt unter dist\installer\."
