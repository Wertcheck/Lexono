# STT-Evaluation (15.09.) - reine Evaluation, keine Implementierung

Reproduzierbare Mess-/Vergleichsskripte fuer die lokale
Spracherkennungs-Evaluation (Nutzerauftrag, Feld B). Audiodateien selbst
(WAV) NICHT hier abgelegt (Binaerdaten) - `generate_audio.ps1` erzeugt sie
per Windows-SAPI in Sekunden neu, `audio/reference_texts.json` traegt die
Referenztexte.

- `STT_EVALUATION_MEMO.md` - der eigentliche Befund, inkl. klarer
  Trennung "gemessen" (Vosk, faster-whisper, real getestet) vs.
  "dokumentiert" (openai-whisper ausgeschlossen, whisper.cpp nicht
  getestet).
- `test_vosk.py` / `test_faster_whisper.py` - liefen gegen eine isolierte
  venv (NICHT das Projekt-.venv), brauchen `pip install vosk` bzw.
  `pip install faster-whisper` dort.
- `vosk_results.json` / `faster_whisper_results.json` - die tatsaechlichen
  Rohergebnisse dieses Laufs.

Ausdruecklich: KEIN Produktcode, keine Anbindung an app/. Naechster
Schritt vor einer Implementierungsentscheidung: Test auf der echten
Referenzhardware + mit echter menschlicher Stimme (siehe Memo).
