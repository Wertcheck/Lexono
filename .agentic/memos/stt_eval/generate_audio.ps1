# Erzeugt echte deutsche Test-Audiodateien via Windows SAPI (kein
# Mikrofon vorhanden/benoetigt) - realistische kanzleitypische Diktate
# fuer die STT-Evaluation (Vosk vs. faster-whisper).

Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.SelectVoice("Microsoft Hedda Desktop")
$synth.Rate = 0

$outDir = "C:\Users\Bonit\AppData\Local\Temp\claude\C--Users-Bonit-Lexono\633aff23-1b47-4b2b-bef6-5a74784937ca\scratchpad\stt_eval\audio"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

$samples = @{
  "01_kurz_hallo" = "Hallo, wie kann ich Ihnen helfen?"
  "02_name_aktenzeichen" = "Fuegen Sie hinzu, dass Frau Erika Mustermann unter dem Aktenzeichen zwei null zwei fuenf Schraegstrich null sechs neun drei Bindestrich E S T widerspricht."
  "03_paragraph_frist" = "Der Einspruch ist gemaess Paragraph dreihundertfuenfundfuenfzig der Abgabenordnung innerhalb eines Monats nach Bekanntgabe des Bescheids einzulegen."
  "04_betrag_datum" = "Die festgesetzte Einkommensteuer betraegt zwoelftausenddreihundertfuenfzig Euro, faellig zum funfzehnten Maerz zweitausendsiebenundzwanzig."
  "05_langes_diktat" = "Sehr geehrte Damen und Herren, namens und in Vollmacht unseres Mandanten legen wir gegen den Bescheid fuer zweitausendvierundzwanzig ueber Einkommensteuer und Solidaritaetszuschlag Einspruch ein. Die Werbungskosten wurden lediglich mit dem Pauschbetrag von eintausendzweihundertdreissig Euro beruecksichtigt."
}

foreach ($key in $samples.Keys) {
  $path = Join-Path $outDir "$key.wav"
  $synth.SetOutputToWaveFile($path, (New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)))
  $synth.Speak($samples[$key])
  $synth.SetOutputToNull()
  $size = (Get-Item $path).Length
  Write-Host "$key -> $path ($size B)"
}

# Referenztexte fuer den spaeteren Genauigkeitsvergleich als JSON sichern.
$samples | ConvertTo-Json | Set-Content -Path (Join-Path $outDir "reference_texts.json") -Encoding utf8
Write-Host "Fertig: $($samples.Count) Audiodateien in $outDir"
