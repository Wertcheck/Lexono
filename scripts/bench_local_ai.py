"""Reproduzierbarer Benchmark der lokalen KI-Schritte im Schriftsatz-Ablauf (Performance-Run 10.10.).

Misst gegen das ECHTE lokale Ollama mit dem ECHTEN Provider-Code (`OllamaLocalLLMProvider`) die
beiden lokalen Schritte, die den Schriftsatz-Ablauf dominieren:

- Vorabanalyse (`process`)           - Zusammenfassung des pseudonymisierten Sachverhalts
- semantische Antwortpruefung (`_build_semantic_check_prompt` + `generate_structured`)

Ausschliesslich SYNTHETISCHE, platzhalterbasierte Texte (keine Mandantendaten). Ausgabe: je Fall
Gesamtzeit sowie die von Ollama selbst gemeldete Aufteilung (Laden / Prompt lesen / Erzeugen).
Keine Prompts oder Antworten werden ausgegeben.

Aufruf:  python scripts/bench_local_ai.py [--model qwen3:4b] [--runs 3] [--cold] [--env KEY=VAL ...]
"""

from __future__ import annotations

import argparse
import logging
import re
import statistics
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai_providers.ollama_provider import OllamaLocalLLMProvider  # noqa: E402
from app.drafting.response_validation import (  # noqa: E402
    _RESPONSE_CHECK_SCHEMA,
    _build_semantic_check_prompt,
)
from app.privacy.gateway_schema import ClaudeRequestPayload  # noqa: E402

_SENTENCES = [
    "Die [PERSON_01] hat mit der [ORGANISATION_01] am [DATUM_01] einen Kaufvertrag über eine Maschine geschlossen.",
    "Der vereinbarte Kaufpreis betrug 48.500,00 EUR und war nach Lieferung binnen vierzehn Tagen fällig.",
    "Die Lieferung erfolgte am [DATUM_02] an die Anschrift [ADRESSE_01]; die Abnahme wurde nicht erklärt.",
    "Mit Schreiben vom [DATUM_03] rügte die [PERSON_01] erhebliche Mängel und setzte eine Frist zur Nacherfüllung.",
    "Die [ORGANISATION_01] bestritt die Mängel und verwies auf die Allgemeinen Geschäftsbedingungen, Ziffer 9.",
    "Zur Höhe des Minderungsbetrags von 7.250,00 EUR wurde ein Sachverständigengutachten in Aussicht gestellt.",
    "Die Parteien korrespondierten über das Aktenzeichen [AKTENZEICHEN_01] und tauschten mehrfach Lichtbilder aus.",
    "Eine Zahlung ist bislang nicht erfolgt; die Mahnung vom [DATUM_04] blieb ohne Reaktion.",
]


def _text(words: int) -> str:
    out: list[str] = []
    n = 0
    i = 0
    while n < words:
        s = _SENTENCES[i % len(_SENTENCES)]
        out.append(s)
        n += len(s.split())
        i += 1
    return " ".join(out)


CASES = {"kurz": 80, "mittel": 400, "komplex": 1400}
DRAFT_WORDS = {"kurz": 150, "mittel": 450, "komplex": 900}


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[dict] = []

    def emit(self, record: logging.LogRecord) -> None:
        m = re.search(
            r"PERF_OLLAMA task=(\S+) total_s=([\d.]+) load_s=([\d.]+) prompt_tokens=(\S+) "
            r"prompt_eval_s=([\d.]+) eval_tokens=(\S+) eval_s=([\d.]+)",
            record.getMessage(),
        )
        if m:
            self.records.append(
                {
                    "task": m.group(1),
                    "total": float(m.group(2)),
                    "load": float(m.group(3)),
                    "ptok": int(m.group(4)),
                    "peval": float(m.group(5)),
                    "etok": int(m.group(6)),
                    "eval": float(m.group(7)),
                }
            )


def _unload(base: str, model: str) -> None:
    httpx.post(f"{base}/api/generate", json={"model": model, "keep_alive": 0}, timeout=60)
    time.sleep(1.0)


def _p95(v: list[float]) -> float:
    v = sorted(v)
    return v[min(len(v) - 1, int(round(0.95 * (len(v) - 1))))]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3:4b")
    ap.add_argument("--base", default="http://127.0.0.1:11434")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--cold", action="store_true", help="vor JEDEM Lauf Modell entladen")
    ap.add_argument("--cases", default="kurz,mittel,komplex")
    args = ap.parse_args()

    cap = _Capture()
    logging.getLogger("lexono.perf").addHandler(cap)
    logging.getLogger("lexono.perf").setLevel(logging.INFO)
    prov = OllamaLocalLLMProvider(base_url=args.base, model=args.model, timeout_seconds=600)

    print(f"Modell {args.model}, {args.runs} Läufe/Fall, {'KALT' if args.cold else 'WARM'}")
    print(f"{'Fall/Schritt':18s} {'n':>2s} {'wall_med':>8s} {'wall_p95':>8s} {'load':>6s} {'p_tok':>6s} {'p_eval':>7s} {'e_tok':>6s} {'eval':>7s}")
    for case in args.cases.split(","):
        sach = _text(CASES[case])
        draft = _text(DRAFT_WORDS[case])
        payload = ClaudeRequestPayload(
            schreibauftrag="schriftsatz",
            anonymisierter_sachverhalt=sach,
        )
        for step in ("summary", "validation"):
            walls: list[float] = []
            cap.records.clear()
            if not args.cold:
                # einmal aufwaermen, damit alle gemessenen Laeufe warm sind
                prov.generate_structured("Antworte mit passed true.", _RESPONSE_CHECK_SCHEMA)
                cap.records.clear()
            for _ in range(args.runs):
                if args.cold:
                    _unload(args.base, args.model)
                t0 = time.perf_counter()
                if step == "summary":
                    prov.process(payload)
                else:
                    prov.generate_structured(
                        _build_semantic_check_prompt(sachverhalt=sach, text=draft, has_mappings=True),
                        _RESPONSE_CHECK_SCHEMA,
                    )
                walls.append(time.perf_counter() - t0)
            r = cap.records
            med = lambda k: statistics.median(x[k] for x in r) if r else 0  # noqa: E731
            print(
                f"{case + '/' + step:18s} {len(walls):2d} {statistics.median(walls):8.1f} {_p95(walls):8.1f} "
                f"{med('load'):6.1f} {med('ptok'):6.0f} {med('peval'):7.1f} {med('etok'):6.0f} {med('eval'):7.1f}"
            )


if __name__ == "__main__":
    main()
