"""Benchmark (26.10.2026): Nachname nach Artikel ("Der König hat angerufen") - Lecks vs. Fehlmaskierung gewoehnlicher
Substantive. Echtes Gateway, ausschliesslich synthetische Saetze.

Drei Gruppen, je dieselben Woerter:
  P (Name)       - der Satz ist nur als Person lesbar (Akte nennt die Beteiligten, Handlung einer Prozesspartei):
                   Leck = Wort im Klartext im erlaubten Payload (Modus "mask"); ungefragt = weder maskiert noch gefragt ("ask").
  G (Gattungswort) - das Wort ist eindeutig ein gewoehnliches Substantiv ("Der Winter war streng", "Der Hahn kraeht"):
                   Fehlalarm = maskiert ("mask") oder gefragt ("ask").
  M (mehrdeutig) - Rolle oder Name grammatisch gleichwertig ("Der Bauer hat am 12.03.2026 angerufen"): nur gezaehlt
                   (Rueckfrage ist dort die gewollte Reaktion, keine stille Maskierung erwartet).
Zusaetzlich Uebermaskierung: Platzhalter ueber anderen Woertern als dem Zielwort in G.

Aufruf:  python scripts/bench_article_surname_nouns.py [--out results.json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.privacy.gateway import ClaudePrivacyGateway  # noqa: E402

SACH = "Akte: Beispielakte\n[Vertrag] "
WORDS = ["König", "Kaiser", "Graf", "Bauer", "Sommer", "Winter", "Vogel", "Hahn", "Horn", "Schuster", "Wagner",
         "Koch", "Fischer", "Schneider", "Fuchs", "Wolf", "Engel", "Jäger", "Ritter", "Zimmermann"]
PERSON_FRAMES = [
    "Der {N} hat am 12.03.2026 angerufen und um Rückruf gebeten.",
    "Der {N} hat den Mietvertrag am 01.02.2025 unterschrieben.",
    "Der {N} hat mit Schreiben vom 16.03.2024 widersprochen.",
    "Der {N} hat die Kaution in Höhe von 1.200,00 EUR gezahlt.",
]
AMBIGUOUS_FRAMES = [
    "Der {N} hat uns die Unterlagen übergeben.",
]
COMMON = {
    "König": "Der König regierte das Land über dreißig Jahre.",
    "Kaiser": "Der Kaiser ließ im Mittelalter Burgen errichten.",
    "Graf": "Der Graf besaß im Mittelalter mehrere Burgen.",
    "Bauer": "Der Bauer bestellt im Frühjahr das Feld.",
    "Sommer": "Der Sommer war in diesem Jahr ungewöhnlich heiß.",
    "Winter": "Der Winter war streng und lang.",
    "Vogel": "Der Vogel sitzt auf dem Dach des Nachbarhauses.",
    "Hahn": "Der Hahn kräht jeden Morgen um fünf Uhr.",
    "Horn": "Der Horn des Nashorns ist beschädigt.",
    "Schuster": "Der Schuster besohlt die Schuhe in seiner Werkstatt.",
    "Wagner": "Der Wagner fertigte früher Räder und Wagen.",
    "Koch": "Der Koch hat das Essen für die Gäste zubereitet.",
    "Fischer": "Der Fischer hat am Morgen die Netze eingeholt.",
    "Schneider": "Der Schneider hat die Hose gekürzt.",
    "Fuchs": "Der Fuchs hat in der Nacht ein Huhn gerissen.",
    "Wolf": "Der Wolf wurde in Brandenburg mehrfach gesichtet.",
    "Engel": "Der Engel auf dem Gemälde ist vergoldet.",
    "Jäger": "Der Jäger hat im Revier einen Hochsitz gebaut.",
    "Ritter": "Der Ritter trug eine schwere Rüstung.",
    "Zimmermann": "Der Zimmermann hat den Dachstuhl repariert.",
}
# R (Rolle + Parteihandlung): das Gattungswort handelt wie eine Partei - die KOSTEN der engen Regel (Rueckfrage bzw.
# vorsichtige Maskierung eines Nicht-Namens). Ehrlich mitgezaehlt, nicht weggelassen.
ROLE_ACTS = {
    "König": "Der König hat den Friedensvertrag 1648 unterzeichnet.",
    "Kaiser": "Der Kaiser hat das Gesetz im Jahr 1871 unterschrieben.",
    "Bauer": "Der Bauer hat die Pacht für das Feld gezahlt.",
    "Koch": "Der Koch hat die Rechnung des Großhändlers bezahlt.",
    "Jäger": "Der Jäger hat die Jagdpacht zum Jahresende gekündigt.",
    "Schuster": "Der Schuster hat die Miete für die Werkstatt überwiesen.",
    "Wirt": "Der Wirt hat der Mieterhöhung widersprochen.",
    "Fischer": "Der Fischer hat den Pachtvertrag für den See unterschrieben.",
}
PLACEHOLDER = re.compile(r"\[[A-Z]+_\d{2,}\]")


def _run(gateway, text, mode):
    return gateway.prepare_request(purpose="formulate_draft", sachverhalt=SACH + text,
                                   anwaltliche_anmerkungen="Erstelle ein Schreiben.", review_mode=mode)


def _word_in(text: str, word: str) -> bool:
    return re.search(rf"(?<![\wÄÖÜäöüß]){re.escape(word)}(?![\wÄÖÜäöüß])", text) is not None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    gateway = ClaudePrivacyGateway()
    started = time.perf_counter()
    p_leak, p_unasked, p_total = [], [], 0
    for word in WORDS:
        for frame in PERSON_FRAMES:
            text = frame.format(N=word)
            p_total += 1
            mask = _run(gateway, text, "mask")
            body = mask.payload.anonymisierter_sachverhalt if mask.allowed and mask.payload else ""
            if mask.allowed and _word_in(body, word):
                p_leak.append(text)
            ask = _run(gateway, text, "ask")
            asked = ask.review_required and word in [c.term for c in ask.review_candidates]
            masked = any(word in m.original_value for m in ask.mappings)
            if not asked and not masked:
                p_unasked.append(text)
    g_false, g_over = [], []
    for word, text in COMMON.items():
        mask = _run(gateway, text, "mask")
        body = mask.payload.anonymisierter_sachverhalt if mask.allowed and mask.payload else ""
        ask = _run(gateway, text, "ask")
        asked = ask.review_required and word in [c.term for c in ask.review_candidates]
        if (mask.allowed and not _word_in(body, word)) or asked:
            g_false.append(text)
        others = [m.original_value for m in mask.mappings if word not in m.original_value]
        if others:
            g_over.append({"satz": text, "maskiert": others})
    r_hit = []
    for word, text in ROLE_ACTS.items():
        mask = _run(gateway, text, "mask")
        body = mask.payload.anonymisierter_sachverhalt if mask.allowed and mask.payload else ""
        ask = _run(gateway, text, "ask")
        if (mask.allowed and not _word_in(body, word)) or (ask.review_required and word in [c.term for c in ask.review_candidates]):
            r_hit.append(text)
    m_asked, m_total = 0, 0
    for word in WORDS:
        for frame in AMBIGUOUS_FRAMES:
            m_total += 1
            ask = _run(gateway, frame.format(N=word), "ask")
            if ask.review_required and word in [c.term for c in ask.review_candidates]:
                m_asked += 1
    result = {
        "P_faelle": p_total, "P_lecks_mask": len(p_leak), "P_ungefragt_ask": len(p_unasked),
        "G_faelle": len(COMMON), "G_fehlalarme": len(g_false), "G_uebermaskierung_andere_woerter": len(g_over),
        "M_faelle": m_total, "M_rueckfragen": m_asked,
        "R_faelle": len(ROLE_ACTS), "R_gefragt_oder_maskiert": len(r_hit), "R_saetze": r_hit,
        "P_lecks_beispiele": p_leak[:20], "G_fehlalarm_saetze": g_false, "G_uebermaskierung": g_over,
        "laufzeit_s": round(time.perf_counter() - started, 1),
    }
    print(json.dumps(result, ensure_ascii=False, indent=1))
    if args.out:
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
