"""Echte STT-Messung mit Vosk (vosk-model-small-de-0.15) gegen die per
Windows-SAPI erzeugten Testdiktate. Kein Mikrofon noetig - echte Audiodatei,
echte Bibliothek, echte Transkription."""
from __future__ import annotations

import json
import time
import wave
from pathlib import Path

from vosk import KaldiRecognizer, Model, SetLogLevel

SetLogLevel(-1)  # Bibliotheks-Logging unterdruecken, nicht die Ergebnisse

HERE = Path(__file__).parent
AUDIO_DIR = HERE / "audio"
MODEL_DIR = HERE / "models" / "vosk-model-small-de-0.15"

references = json.loads((AUDIO_DIR / "reference_texts.json").read_text(encoding="utf-8-sig"))


def transcribe(model: Model, wav_path: Path) -> str:
    wf = wave.open(str(wav_path), "rb")
    rec = KaldiRecognizer(model, wf.getframerate())
    rec.SetWords(True)
    text_parts = []
    while True:
        data = wf.readframes(4000)
        if len(data) == 0:
            break
        if rec.AcceptWaveform(data):
            text_parts.append(json.loads(rec.Result()).get("text", ""))
    text_parts.append(json.loads(rec.FinalResult()).get("text", ""))
    wf.close()
    return " ".join(p for p in text_parts if p).strip()


print("=== Modell laden (COLD) ===")
t0 = time.perf_counter()
model = Model(str(MODEL_DIR))
cold_load = time.perf_counter() - t0
print(f"Ladezeit (cold): {cold_load:.2f}s")

print("\n=== Modell erneut laden (WARM, gleicher Prozess) ===")
t0 = time.perf_counter()
model2 = Model(str(MODEL_DIR))
warm_load = time.perf_counter() - t0
print(f"Ladezeit (warm, 2. Instanz): {warm_load:.2f}s")

results = []
print("\n=== Transkription je Sample ===")
for key in sorted(references.keys()):
    wav_path = AUDIO_DIR / f"{key}.wav"
    t0 = time.perf_counter()
    text = transcribe(model, wav_path)
    elapsed = time.perf_counter() - t0
    ref = references[key]
    print(f"\n--- {key} ({elapsed:.2f}s)")
    print(f"  REFERENZ : {ref}")
    print(f"  ERKANNT  : {text}")
    results.append({"key": key, "reference": ref, "recognized": text, "seconds": elapsed})

out = HERE / "vosk_results.json"
out.write_text(
    json.dumps({"cold_load_s": cold_load, "warm_load_s": warm_load, "results": results}, indent=2, ensure_ascii=False),
    encoding="utf-8",
)
print(f"\nErgebnisse gespeichert: {out}")
