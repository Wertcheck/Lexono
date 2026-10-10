"""Blindes Urteil der Entwuerfe aus scripts/e2e_summary_ab.py durch ein ANDERES Modell (Qualitaetslauf 12.10.2026).

Der Schreiber ist Claude Sonnet; geurteilt wird von Claude Opus. Jeder Entwurf wird EINZELN nach vorab festgelegten
Kriterien bewertet (absolute Skala 1-5, keine Paarvergleiche), in zufaelliger Reihenfolge und unter anonymer ID; Variante
und Lauf sind dem Urteilenden nicht bekannt. Ein Teil der Entwuerfe wird zweimal bewertet (Konsistenzpruefung).
Kriterien (vorab): tatsachen (relevante Tatsachen, Betraege, Fristen, Rechtsfolgen erhalten), auslassungen (5 = keine
wesentlichen Auslassungen), erfindungen (5 = keine erfundenen Angaben), widersprueche (5 = alle Widersprueche zwischen den
Angaben benannt, nicht vereinheitlicht), schriftsatz (juristische Qualitaet/Brauchbarkeit des Schreibens).

Aufruf:  python scripts/e2e_summary_judge.py [--repeat-share 0.2]
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import anthropic  # noqa: E402

import e2e_summary_ab as ab  # noqa: E402

OUT = ROOT / "e2e_ab"
JUDGE_MODEL = "claude-opus-5-5"
PROMPT = """Du bist ein erfahrener Rechtsanwalt und bewertest EINEN Schriftsatz-Entwurf. Alle Namen/Daten sind synthetisch.

AUFGABE AN DEN SCHREIBER:
{task}

QUELLDOKUMENTE (vollständig):
{docs}

ZU BEWERTENDER ENTWURF:
{draft}

Bewerte streng anhand der Quelldokumente, Skala 1 (schlecht) bis 5 (sehr gut):
- tatsachen: relevante Tatsachen, Beträge, Fristen, Rechtsfolgen aus allen Dokumenten korrekt wiedergegeben
- auslassungen: 5 = keine wesentlichen Auslassungen, 1 = viel Wesentliches fehlt
- erfindungen: 5 = keine erfundenen oder falschen Angaben, 1 = mehrere erfundene/falsche Angaben
- widersprueche: 5 = alle Widersprüche zwischen den Angaben (Liefertermine, Preise) benannt statt vereinheitlicht
- schriftsatz: juristische Qualität und Brauchbarkeit als Schreiben
Antworte AUSSCHLIESSLICH als JSON: {{"tatsachen":n,"auslassungen":n,"erfindungen":n,"widersprueche":n,"schriftsatz":n}}"""


def _key() -> str:
    for line in open(r"C:\ProgramData\Lexono\.env", encoding="utf-8", errors="ignore"):
        m = re.match(r'\s*ANTHROPIC_API_KEY\s*=\s*"?([^"\r\n]+)', line)
        if m:
            return m.group(1)
    raise SystemExit("kein API-Schluessel")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat-share", type=float, default=0.2)
    args = ap.parse_args()
    client = anthropic.Anthropic(api_key=_key())
    rng = random.Random(42)
    files = sorted(p for p in OUT.glob("*_k?_*.txt") if p.read_text(encoding="utf-8") != "(blockiert)")
    jobs = [(p, False) for p in files] + [(p, True) for p in rng.sample(files, int(len(files) * args.repeat_share))]
    rng.shuffle(jobs)
    result_path = OUT / "judge.jsonl"
    done = {(r["file"], r["repeat"]) for r in map(json.loads, result_path.read_text(encoding="utf-8").splitlines())} if result_path.exists() else set()
    for index, (path, repeat) in enumerate(jobs):
        if (path.name, repeat) in done:
            continue
        variant, case, run = path.stem.split("_")
        spec = ab.make_case(case)
        docs = "\n\n".join(f"--- Dokument {i + 1} ---\n{d}" for i, d in enumerate(spec.docs))
        message = client.messages.create(
            model=JUDGE_MODEL, max_tokens=4000,
            messages=[{"role": "user", "content": PROMPT.format(task=spec.task, docs=docs, draft=path.read_text(encoding="utf-8"))}],
        )
        text = next(b.text for b in message.content if getattr(b, "type", "") == "text")
        scores = json.loads(text[text.index("{"): text.rindex("}") + 1])
        row = {"file": path.name, "variant": variant, "case": case, "run": int(run), "repeat": repeat, **scores}
        with result_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        print(f"{index + 1}/{len(jobs)} {path.name} {'(Wiederholung)' if repeat else ''} {scores}", flush=True)


if __name__ == "__main__":
    main()
