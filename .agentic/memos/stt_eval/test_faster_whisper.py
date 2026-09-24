"""Echte STT-Messung mit faster-whisper (CTranslate2-Backend, INT8, CPU)
gegen dieselben per Windows-SAPI erzeugten Testdiktate wie test_vosk.py -
direkter, fairer Vergleich auf identischem Audiomaterial."""
from __future__ import annotations

import json
import time
from pathlib import Path

from faster_whisper import WhisperModel

HERE = Path(__file__).parent
AUDIO_DIR = HERE / "audio"
references = json.loads((AUDIO_DIR / "reference_texts.json").read_text(encoding="utf-8-sig"))

MODEL_SIZE = "base"  # realistische CPU-Groesse fuer die Referenzhardware

print(f"=== Modell '{MODEL_SIZE}' laden (COLD, int8, CPU) ===")
t0 = time.perf_counter()
model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")
cold_load = time.perf_counter() - t0
print(f"Ladezeit (cold, inkl. evtl. Download/Konvertierung): {cold_load:.2f}s")

print("\n=== Modell erneut laden (WARM, gleicher Prozess, bereits im HF-Cache) ===")
t0 = time.perf_counter()
model2 = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")
warm_load = time.perf_counter() - t0
print(f"Ladezeit (warm): {warm_load:.2f}s")

results = []
print("\n=== Transkription je Sample ===")
for key in sorted(references.keys()):
    wav_path = AUDIO_DIR / f"{key}.wav"
    t0 = time.perf_counter()
    segments, info = model.transcribe(str(wav_path), language="de", beam_size=5)
    text = " ".join(seg.text.strip() for seg in segments)
    elapsed = time.perf_counter() - t0
    ref = references[key]
    print(f"\n--- {key} ({elapsed:.2f}s, erkannte Sprache: {info.language}, p={info.language_probability:.2f})")
    print(f"  REFERENZ : {ref}")
    print(f"  ERKANNT  : {text}")
    results.append({"key": key, "reference": ref, "recognized": text, "seconds": elapsed})

out = HERE / "faster_whisper_results.json"
out.write_text(
    json.dumps(
        {"model_size": MODEL_SIZE, "cold_load_s": cold_load, "warm_load_s": warm_load, "results": results},
        indent=2, ensure_ascii=False,
    ),
    encoding="utf-8",
)
print(f"\nErgebnisse gespeichert: {out}")
