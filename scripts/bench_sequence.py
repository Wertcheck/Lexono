"""Realistischer Ablauf-Benchmark der lokalen KI im Schriftsatz-Ablauf (Performance-Run 10.10.).

Bildet die Reihenfolge der echten Pipeline nach: Vorabanalyse (process) -> Wartezeit auf Claude
(simuliert, `--claude-wait`) -> semantische Pruefung. Jeder Lauf nutzt einen EINDEUTIGEN Text, damit der
Prompt-Cache von Ollama nicht kuenstlich Treffer liefert; nur die feste Anweisung bleibt gleich (wie im
Betrieb). Ausschliesslich synthetische Platzhaltertexte, keine Ausgabe von Prompts/Antworten.

  --legacy   nutzt Schema/Prompt des Ausgangsstands (git HEAD) statt der aktuellen Dateien
  --prefill  startet waehrend der simulierten Claude-Wartezeit das Vorwaermen (wie DraftingService)
"""

from __future__ import annotations

import argparse
import importlib.util
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import bench_local_ai as b  # noqa: E402
from app.ai_providers.ollama_provider import OllamaLocalLLMProvider  # noqa: E402
from app.privacy.gateway_schema import ClaudeRequestPayload  # noqa: E402


def _legacy_validation_module():
    src = subprocess.run(
        ["git", "show", "HEAD:app/drafting/response_validation.py"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout
    tmp = ROOT / "scripts" / "_legacy_response_validation.py"
    tmp.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("_legacy_rv", tmp)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    tmp.unlink()
    return mod


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3:4b")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--cases", default="kurz,mittel,komplex")
    ap.add_argument("--legacy", action="store_true")
    ap.add_argument("--prefill", action="store_true")
    ap.add_argument("--claude-wait", type=float, default=15.0)
    ap.add_argument("--cold", action="store_true", help="vor jedem Lauf Modell entladen")
    args = ap.parse_args()

    if args.legacy:
        rv = _legacy_validation_module()
        build_prompt = rv._build_semantic_check_prompt
        schema = rv._RESPONSE_CHECK_SCHEMA
        prefix_fn = None
    else:
        from app.drafting import response_validation as rv

        build_prompt = rv._build_semantic_check_prompt
        schema = rv._RESPONSE_CHECK_SCHEMA
        prefix_fn = getattr(rv, "build_semantic_check_prefix", None)

    cap = b._Capture()
    import logging

    logging.getLogger("lexono.perf").addHandler(cap)
    logging.getLogger("lexono.perf").setLevel(logging.INFO)
    prov = OllamaLocalLLMProvider(base_url="http://127.0.0.1:11434", model=args.model, timeout_seconds=240)

    mode = ("LEGACY" if args.legacy else "NEU") + (" +prefill" if args.prefill else "") + (" KALT" if args.cold else " warm")
    print(f"Sequenz-Benchmark [{mode}], Claude-Wartezeit {args.claude_wait:.0f}s simuliert, {args.runs} Läufe/Fall")
    print(f"{'Fall':8s} {'summary':>8s} {'validate':>9s} {'lokal_ges':>9s} {'ges+claude':>10s}   (Median, p95 der lokalen Gesamtzeit)")
    for case in args.cases.split(","):
        sums, vals, tots = [], [], []
        for run in range(args.runs):
            uniq = f" Aktenvermerk Nr. {1000 + run * 7} vom Tag {run + 11}."
            sach = b._text(b.CASES[case]) + uniq
            draft = b._text(b.DRAFT_WORDS[case]) + uniq
            payload = ClaudeRequestPayload(schreibauftrag="schriftsatz", anonymisierter_sachverhalt=sach)
            if args.cold:
                b._unload("http://127.0.0.1:11434", args.model)
            t0 = time.perf_counter()
            try:
                prov.process(payload)
            except Exception as exc:  # noqa: BLE001 - Zeitueberschreitung = im Betrieb Fail-Closed-Abbruch
                print(f"  {case} Lauf {run + 1}: Vorabanalyse FEHLGESCHLAGEN nach {time.perf_counter() - t0:.0f}s ({type(exc).__name__})")
                continue
            t1 = time.perf_counter()
            if args.prefill and prefix_fn is not None:
                prefix = prefix_fn(sachverhalt=sach, has_mappings=True)
                threading.Thread(target=prov.prefill, args=(prefix,), daemon=True).start()
            time.sleep(args.claude_wait)  # Claude schreibt
            t2 = time.perf_counter()
            try:
                prov.generate_structured(build_prompt(sachverhalt=sach, text=draft, has_mappings=True), schema)
            except Exception as exc:  # noqa: BLE001
                print(f"  {case} Lauf {run + 1}: Prüfung FEHLGESCHLAGEN nach {time.perf_counter() - t2:.0f}s ({type(exc).__name__})")
                continue
            t3 = time.perf_counter()
            sums.append(t1 - t0)
            vals.append(t3 - t2)
            tots.append((t1 - t0) + (t3 - t2))
        if not tots:
            print(f"{case:8s} alle Läufe fehlgeschlagen")
            continue
        print(
            f"{case:8s} {statistics.median(sums):8.1f} {statistics.median(vals):9.1f} "
            f"{statistics.median(tots):9.1f} {statistics.median(tots) + args.claude_wait:10.1f}   p95={b._p95(tots):.1f}"
        )


if __name__ == "__main__":
    main()
