"""Auswertung des Zusammenfassungs-A/B: objektive Metriken, blindes Urteil, Laufzeit, Kontext, Signifikanz.

Liest e2e_ab/metrics.jsonl (scripts/e2e_summary_ab.py) und optional e2e_ab/judge.jsonl (scripts/e2e_summary_judge.py).
Vergleich je Variante gegen "none" und v1 gegen v0: Mittelwert, Standardabweichung, Permutationstest (zweiseitig, 20000
Ziehungen) - mit n = 8 je Zelle ist nur ein GROSSER Unterschied nachweisbar.
Aufruf:  python scripts/e2e_summary_report.py
"""

from __future__ import annotations

import json
import random
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "e2e_ab"


def load(name: str) -> list[dict]:
    path = OUT / name
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def perm_p(a: list[float], b: list[float], rounds: int = 20000) -> float:
    if len(a) < 2 or len(b) < 2:
        return float("nan")
    observed = abs(st.mean(a) - st.mean(b))
    pool = a + b
    rng = random.Random(1)
    hits = 0
    for _ in range(rounds):
        rng.shuffle(pool)
        hits += abs(st.mean(pool[: len(a)]) - st.mean(pool[len(a):])) >= observed - 1e-12
    return hits / rounds


def cell(values: list[float]) -> str:
    return f"{st.mean(values):6.2f}±{(st.stdev(values) if len(values) > 1 else 0):4.2f}" if values else "   -   "


def main() -> None:
    metrics = load("metrics.jsonl")
    judge = [r for r in load("judge.jsonl") if not r["repeat"]]
    by = defaultdict(list)
    for row in metrics:
        by[(row["case"], row["variant"])].append(row)
    cases = sorted({r["case"] for r in metrics})
    variants = ["none", "v0", "v1"]

    def fact_rate(row: dict, group: str | None = None) -> float:
        groups = [group] if group else ["anfang", "mitte", "ende"]
        have = sum(row["fakten"][g][0] for g in groups)
        total = sum(row["fakten"][g][1] for g in groups)
        return have / total if total else 0.0

    print("Metrik je Fall und Variante (Mittelwert±SD; n = gueltige/gesamte Laeufe)")
    print(f"{'Fall':4s} {'Var':4s} {'n':>5s} {'Blocks':>6s} {'Fakten gesamt':>14s} {'Mitte':>10s} {'erfunden':>9s} {'Widerspr.':>9s} {'Zusammenf. s':>13s} {'gesamt s':>10s} {'Payload':>9s}")
    for case in cases:
        for variant in variants:
            rows = by[(case, variant)]
            if not rows:
                continue
            valid = [r for r in rows if r["ok"]]
            conflicts = [sum(r["widersprueche_benannt"]) / len(r["widersprueche_benannt"]) for r in valid]
            print(
                f"{case:4s} {variant:4s} {len(valid):2d}/{len(rows):<2d} {len(rows) - len(valid):6d} {cell([100 * fact_rate(r) for r in valid]):>14s} "
                f"{cell([100 * fact_rate(r, 'mitte') for r in valid]):>10s} {cell([len(r['erfunden']) for r in valid]):>9s} "
                f"{cell([100 * c for c in conflicts]):>9s} {cell([r['summary_s'] for r in rows]):>13s} {cell([r['total_s'] for r in rows]):>10s} "
                f"{cell([r['payload_chars'] for r in rows]):>9s}"
            )

    print("\nVergleiche (alle Faelle gepoolt, nur gueltige Entwuerfe): Differenz Mittelwert und Permutations-p")
    pooled = defaultdict(list)
    for row in metrics:
        pooled[row["variant"]].append(row)
    for a, b in (("v0", "none"), ("v1", "none"), ("v1", "v0")):
        for label, fn in (
            ("Fakten gesamt %", lambda r: 100 * fact_rate(r)),
            ("Fakten Mitte %", lambda r: 100 * fact_rate(r, "mitte")),
            ("erfundene Werte", lambda r: float(len(r["erfunden"]))),
            ("Gesamtlaufzeit s", lambda r: r["total_s"]),
        ):
            xa = [fn(r) for r in pooled[a] if r["ok"] or label.startswith("Gesamt")]
            xb = [fn(r) for r in pooled[b] if r["ok"] or label.startswith("Gesamt")]
            if xa and xb:
                print(f"  {a:3s} vs {b:4s} {label:18s} {st.mean(xa):7.2f} vs {st.mean(xb):7.2f}  Δ={st.mean(xa) - st.mean(xb):+7.2f}  p={perm_p(xa, xb):.3f}  (n={len(xa)}/{len(xb)})")
        block_a = sum(1 for r in pooled[a] if not r["ok"]); block_b = sum(1 for r in pooled[b] if not r["ok"])
        print(f"  {a:3s} vs {b:4s} Blocks              {block_a}/{len(pooled[a])} vs {block_b}/{len(pooled[b])}")

    if judge:
        print("\nBlindes Urteil (Claude Opus, Skala 1-5, nur gueltige Entwuerfe; Mittelwert±SD, pooled)")
        criteria = ["tatsachen", "auslassungen", "erfindungen", "widersprueche", "schriftsatz"]
        grouped = defaultdict(list)
        for row in judge:
            grouped[row["variant"]].append(row)
        print(f"{'Var':4s} {'n':>3s} " + " ".join(f"{c:>15s}" for c in criteria))
        for variant in variants:
            rows = grouped[variant]
            if rows:
                print(f"{variant:4s} {len(rows):3d} " + " ".join(f"{cell([r[c] for r in rows]):>15s}" for c in criteria))
        for a, b in (("v0", "none"), ("v1", "none"), ("v1", "v0")):
            parts = []
            for c in criteria:
                xa = [r[c] for r in grouped[a]]; xb = [r[c] for r in grouped[b]]
                if xa and xb:
                    parts.append(f"{c} Δ={st.mean(xa) - st.mean(xb):+.2f} p={perm_p(xa, xb):.3f}")
            print(f"  {a} vs {b}: " + "; ".join(parts))
        repeats = [r for r in load("judge.jsonl") if r["repeat"]]
        firsts = {r["file"]: r for r in load("judge.jsonl") if not r["repeat"]}
        diffs = [abs(r[c] - firsts[r["file"]][c]) for r in repeats if r["file"] in firsts for c in criteria]
        if diffs:
            print(f"  Konsistenz des Urteilenden (Wiederholung, {len(repeats)} Entwuerfe): mittlere Abweichung {st.mean(diffs):.2f} Punkte, identisch in {100 * sum(1 for d in diffs if d == 0) / len(diffs):.0f} %")


if __name__ == "__main__":
    sys.exit(main())
