$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot\..\..\..\..
$root = Join-Path (Get-Location) "dist\Lexono\_internal"
$files = git ls-files app/web/templates app/web/static migrations
$same = 0; $diff = @(); $missing = @()
foreach ($f in $files) {
    $p = Join-Path $root ($f -replace '/', '\')
    if (-not (Test-Path -LiteralPath $p)) { $missing += $f; continue }
    $h = git hash-object -- "$p"
    $want = git rev-parse "HEAD:$f"
    if ($h -eq $want) { $same++ } else { $diff += $f }
}
"Daten: {0} Dateien, {1} identisch, {2} abweichend, {3} fehlend" -f $files.Count, $same, $diff.Count, $missing.Count
$diff | ForEach-Object { "ABWEICHEND: $_" }
$missing | ForEach-Object { "FEHLT: $_" }
foreach ($k in "app\web\static\js\prompt_manager.js","app\web\templates\partials\prompt_manager.html","migrations\versions\schritt3_039_prompt_templates_start_page.py","app\web\static\css\app.css") { "{0}: {1}" -f $k, (Test-Path (Join-Path $root $k)) }
