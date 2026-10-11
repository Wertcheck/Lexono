"""Auswertung der datensparsamen Datenschutz-Lern-Statistik (12.10.2026, PRIVACY_REVIEW_ARCHITECTURE.md M6).

Zeigt je HMAC-Begriffsschluessel die Anzahl der Entscheidungen ("personal" / "not_personal" / "unsure"), Widersprueche
und grobe Merkmale (Rollen, Probing-Score) - OHNE Klartext. Mit `--term <Begriff>` laesst sich lokal pruefen, ob ein
bestimmter (z. B. synthetisch nachgestellter) Begriff in der Statistik vorkommt.

Zweck: wiederkehrende Fehlklassifikationen erkennen und daraus SYNTHETISCHE Regressionstests und geprufte
Regelverbesserungen ableiten. Dieses Skript aendert keine Regel, aktiviert nichts und trainiert kein Modell.

Aufruf:  python scripts/privacy_feedback_report.py [--min-count 2] [--term Begriff]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal  # noqa: E402
from app.models.privacy_decision import PrivacyFeedbackStat  # noqa: E402
from app.privacy.decisions import term_hmac  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-count", type=int, default=2, help="nur Begriffe mit mindestens so vielen Entscheidungen")
    ap.add_argument("--term", default=None, help="einen konkreten Begriff lokal nachschlagen (wird nicht gespeichert)")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        rows = db.query(PrivacyFeedbackStat).all()
    finally:
        db.close()
    by_key: dict[str, dict[str, int]] = defaultdict(dict)
    features: dict[str, str] = {}
    for row in rows:
        by_key[row.term_hmac][row.decision] = row.count
        if row.features_json:
            features[row.term_hmac] = row.features_json
    if args.term:
        key = term_hmac(args.term)
        print(f"Begriff -> Schluessel {key[:12]}: {by_key.get(key, 'keine Eintraege')}")
        return
    print(f"{'Schluessel':14s} {'personal':>8s} {'kein PB':>8s} {'unsicher':>8s}  Hinweis  Merkmale")
    for key, counts in sorted(by_key.items(), key=lambda kv: -sum(kv[1].values())):
        total = sum(counts.values())
        if total < args.min_count:
            continue
        personal, not_personal, unsure = counts.get("personal", 0), counts.get("not_personal", 0), counts.get("unsure", 0)
        hint = "WIDERSPRUCH" if (personal or unsure) and not_personal else ("Kandidat: kein Name" if not_personal >= 3 and not personal else "")
        feats = json.loads(features.get(key, "{}"))
        print(f"{key[:12]:14s} {personal:8d} {not_personal:8d} {unsure:8d}  {hint:20s} {feats}")
    print("Hinweis: Einzelne oder widerspruechliche Entscheidungen aendern keine Regel. Eine Verbesserung wird erst nach "
          "synthetischen Positiv-/Negativtests und Pruefung aktiviert.")


if __name__ == "__main__":
    main()
