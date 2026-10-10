"""Lokaler Teil des Zusammenfassungs-A/B (OHNE Claude): wie gut ist die lokale Zusammenfassung SELBST? (12.10.2026)

Misst fuer die Prompts v0 (bisher) und v1 (kompakt) mit echtem Ollama auf den synthetischen Faellen k1/k2/k3
(scripts/e2e_summary_ab.py): Laufzeit, Laenge, Kontextumfang (Payload-Zeichen mit Zusammenfassung), Erhalt der Testfakten
IN der Zusammenfassung (Betraege, beschreibende Schluesselwoerter), erfundene Betraege, erfundene Platzhalter und ob beide
Seiten eines Widerspruchs (zwei Preise) genannt werden. Sagt NICHTS ueber die Qualitaet des daraus entstehenden Schriftsatzes
(dafuer scripts/e2e_summary_ab.py mit Claude).

Aufruf:  python scripts/e2e_summary_local.py --cases k1,k2,k3 --runs 6
"""

from __future__ import annotations

import argparse
import json
import re
import statistics as st
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import e2e_completeness as e  # noqa: E402
import e2e_summary_ab as ab  # noqa: E402

TOKEN = re.compile(r"\[[A-Za-zÄÖÜäöüß_]+_\d{2}\]")


def run_one(variant: str, case_name: str) -> dict:
    from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider
    from app.privacy.gateway import ClaudePrivacyGateway

    case = ab.make_case(case_name)
    db, matter = e.build_session(case)
    prep = RuleBasedLocalAIProvider().prepare_draft_context(matter.id, db)
    gateway = ClaudePrivacyGateway().prepare_request(
        purpose="formulate_draft", sachverhalt=prep.sachverhalt, argumentationspunkte=[], quellenverweise=[], stil=None,
        vorlage=None, anwaltliche_anmerkungen=case.task, known_entities=prep.known_entities, gespraechsverlauf=[],
        skip_general_knowledge_pseudonymization=False,
    )
    payload = gateway.payload
    provider = ab.build_provider(variant)
    t = time.perf_counter()
    summary = provider.process(payload).text
    seconds = time.perf_counter() - t
    norm = lambda s: re.sub(r"\s+", " ", s)  # noqa: E731
    S = norm(summary)
    amounts = [f for f in case.facts if f.kind in ("betrag", "rechtsfolge", "frist") and not f.is_date]
    descriptive = [f for f in case.facts if f.keywords]
    price_pairs = []
    for index in sorted({f.doc for f in case.facts}):
        a = next(f for f in case.facts if f.doc == index and f.key.endswith(":preis"))
        b = next(f for f in case.facts if f.doc == index and f.key.endswith(":preis2"))
        price_pairs.append(norm(a.probe) in S and norm(b.probe) in S)
    known_tokens = set(TOKEN.findall(payload.anonymisierter_sachverhalt))
    return {
        "variant": variant, "case": case_name, "seconds": round(seconds, 1), "chars": len(summary),
        "context_chars": len(payload.anonymisierter_sachverhalt) + len(summary),
        "numbers": sum(norm(f.probe) in S for f in amounts) / len(amounts),
        "descriptive": sum(any(k in S for k in f.keywords) for f in descriptive) / len(descriptive),
        "price_conflict_kept": sum(price_pairs) / len(price_pairs),
        "invented_amounts": sorted({a for a in ab._AMOUNT.findall(summary) if norm(a) not in norm(" ".join(case.docs))}),
        "invented_placeholders": sorted(t for t in set(TOKEN.findall(summary)) if t not in known_tokens),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", default="v0,v1")
    ap.add_argument("--cases", default="k1,k2,k3")
    ap.add_argument("--runs", type=int, default=6)
    args = ap.parse_args()
    out = ab.OUT
    out.mkdir(exist_ok=True)
    rows: list[dict] = []
    for run in range(args.runs):
        for case in args.cases.split(","):
            for variant in args.variants.split(","):
                row = run_one(variant, case)
                rows.append(row)
                with (out / "summary_local.jsonl").open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
                print(f"{variant} {case} #{run + 1}: {row['seconds']}s {row['chars']} Zeichen, Zahlen {row['numbers']:.0%}, "
                      f"beschreibend {row['descriptive']:.0%}, Preiswiderspruch {row['price_conflict_kept']:.0%}, "
                      f"erfunden {row['invented_amounts'] or '-'}/{row['invented_placeholders'] or '-'}", flush=True)
    print("\nMittelwert je Fall/Variante:")
    for case in args.cases.split(","):
        for variant in args.variants.split(","):
            sel = [r for r in rows if r["case"] == case and r["variant"] == variant]
            if sel:
                print(f"{case} {variant}: Zeit {st.mean(r['seconds'] for r in sel):5.1f}s Zeichen {st.mean(r['chars'] for r in sel):5.0f} "
                      f"Kontext {st.mean(r['context_chars'] for r in sel):6.0f} Zahlen {st.mean(r['numbers'] for r in sel):.0%} "
                      f"beschreibend {st.mean(r['descriptive'] for r in sel):.0%} Widerspruch {st.mean(r['price_conflict_kept'] for r in sel):.0%} "
                      f"erfundene Betraege {sum(bool(r['invented_amounts']) for r in sel)}/{len(sel)} erfundene Platzhalter {sum(bool(r['invented_placeholders']) for r in sel)}/{len(sel)}")


if __name__ == "__main__":
    main()
