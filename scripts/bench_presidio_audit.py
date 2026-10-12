"""Presidio-Audit: Vergleich A/B/C auf der eingefrorenen Testbasis scripts/presidio_audit_corpus.json.

A  Presidio wie in Lexono konfiguriert (presidio_ner._get_analyzer_engine: spaCy de_core_news_lg, nur PERSON/LOCATION/
   ORGANIZATION, Schwelle 0.5) - ohne jede Lexono-Regel.
B  Presidio mit allen fuer "de" registrierten Standard-Recognizern PLUS den 13 offiziellen deutschen Recognizern
   (DeTaxId, DeVatId, DeSocialSecurity, DeHealthInsurance, DeIdCard, DePassport, DeKfz, DeHandelsregister, DePlz, ...),
   alle Entitaeten, gleiche Schwelle 0.5, Kontextverstaerkung (LemmaContextAwareEnhancer) aktiv - ohne Lexono-Regeln.
C  Vollstaendige Lexono-Pipeline (ClaudePrivacyGateway.prepare_request, Modus "mask"; Rueckfragen aus Modus "ask").

Alle Varianten bekommen denselben Text im Dokumentpfad ("Akte: ...\n[Schreiben] " + _normalize_document_text(text)).
False Negative = PII-Wert steht im ausgehenden Text; False Positive = keep-Wert fehlt (maskiert) oder Anfrage blockiert.
Aufruf: python scripts/bench_presidio_audit.py [A,B,C,...] [ergebnis.json]
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai_providers.local_ai_provider import _normalize_document_text  # noqa: E402

CORPUS = Path(__file__).with_name("presidio_audit_corpus.json")
GERMAN_RECOGNIZERS = (
    "DeTaxIdRecognizer", "DeTaxNumberRecognizer", "DeVatIdRecognizer", "DeSocialSecurityRecognizer",
    "DeHealthInsuranceRecognizer", "DeIdCardRecognizer", "DePassportRecognizer", "DeFuehrerscheinRecognizer",
    "DeKfzRecognizer", "DeHandelsregisterRecognizer", "DePlzRecognizer", "DeLanrRecognizer", "DeBsnrRecognizer",
)


def doc_text(text: str) -> str:
    return "Akte: Beispielakte\n[Schreiben] " + _normalize_document_text(text)


def mask(text: str, results) -> str:
    spans = sorted(((r.start, r.end, r.entity_type) for r in results), key=lambda s: (s[0], -(s[1] - s[0])))
    merged: list[list] = []
    for start, end, entity in spans:
        if merged and start < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
            continue
        merged.append([start, end, entity])
    out, pos = [], 0
    for start, end, entity in merged:
        out.append(text[pos:start])
        out.append(f"[{entity}]")
        pos = end
    out.append(text[pos:])
    return "".join(out)


def variant_a():
    from app.privacy import presidio_ner as pn

    engine = pn._get_analyzer_engine()

    def run(text):
        results = engine.analyze(text=text, language="de", entities=list(pn._REQUESTED_ENTITIES), score_threshold=pn._MIN_SCORE)
        return {"out": mask(text, results), "allowed": True, "ask": None}

    return run


def variant_b():
    import presidio_analyzer.predefined_recognizers as predefined
    from presidio_analyzer import AnalyzerEngine, RecognizerRegistry

    from app.privacy import presidio_ner as pn

    base = pn._get_analyzer_engine()
    registry = RecognizerRegistry(supported_languages=["de"])
    registry.load_predefined_recognizers(languages=["de"], nlp_engine=base.nlp_engine)
    for name in GERMAN_RECOGNIZERS:
        registry.add_recognizer(getattr(predefined, name)())
    engine = AnalyzerEngine(nlp_engine=base.nlp_engine, registry=registry, supported_languages=["de"])

    def run(text):
        results = engine.analyze(text=text, language="de", score_threshold=pn._MIN_SCORE)
        return {"out": mask(text, results), "allowed": True, "ask": None}

    return run


def variant_c():
    from app.privacy.gateway import ClaudePrivacyGateway

    gateway = ClaudePrivacyGateway()

    def run(text):
        kwargs = {"purpose": "chat_response", "sachverhalt": text, "anwaltliche_anmerkungen": "Fasse das Dokument zusammen."}
        masked = gateway.prepare_request(review_mode="mask", **kwargs)
        asked = gateway.prepare_request(review_mode="ask", **kwargs)
        return {
            "out": masked.payload.anonymisierter_sachverhalt if masked.allowed else "",
            "allowed": masked.allowed,
            "ask": bool(asked.review_required),
        }

    return run


VARIANTS = {"A": variant_a, "B": variant_b, "C": variant_c}


def evaluate(names: list[str]) -> dict:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    # Pruefsumme ueber den INHALT (nicht die Bytes): unabhaengig von Zeilenenden beim Auschecken (CRLF/LF).
    canonical = json.dumps(corpus, ensure_ascii=False, sort_keys=True).encode("utf-8")
    report = {"corpus_sha256": hashlib.sha256(canonical).hexdigest(), "variants": {}}
    for name in names:
        run = VARIANTS[name]()
        run(doc_text("Aufwaermlauf ohne Wertung."))
        rows, started = [], time.perf_counter()
        for c in corpus["cases"]:
            t0 = time.perf_counter()
            res = run(doc_text(c["text"]))
            secs = time.perf_counter() - t0
            out = res["out"].split("[Schreiben] ", 1)[-1]
            fn = [v for v in c["pii"] if res["allowed"] and v in out]
            fp = [v for v in c["keep"] if not res["allowed"] or v not in out]
            rows.append({"id": c["id"], "group": c["group"], "fn": fn, "fp": fp, "allowed": res["allowed"], "ask": res["ask"],
                         "secs": round(secs, 3), "out": out[:300]})
        total = time.perf_counter() - started
        groups: dict[str, dict] = {}
        for c, row in zip(corpus["cases"], rows):
            g = groups.setdefault(c["group"], {"pii": 0, "fn": 0, "keep": 0, "fp": 0})
            g["pii"] += len(c["pii"])
            g["fn"] += len(row["fn"])
            g["keep"] += len(c["keep"])
            g["fp"] += len(row["fp"])
        report["variants"][name] = {
            "fn": sum(len(r["fn"]) for r in rows), "pii": sum(len(c["pii"]) for c in corpus["cases"]),
            "fp": sum(len(r["fp"]) for r in rows), "keep": sum(len(c["keep"]) for c in corpus["cases"]),
            "blocked": sum(not r["allowed"] for r in rows), "asks": sum(bool(r["ask"]) for r in rows),
            "secs_total": round(total, 2), "secs_per_case": round(total / len(rows), 3), "groups": groups, "rows": rows,
        }
    return report


def main() -> None:
    names = (sys.argv[1] if len(sys.argv) > 1 else "A,B,C").split(",")
    report = evaluate(names)
    print("Testbasis sha256:", report["corpus_sha256"][:16])
    for name, v in report["variants"].items():
        print(f"{name}: FN {v['fn']}/{v['pii']}  FP {v['fp']}/{v['keep']}  Blockaden {v['blocked']}  Rueckfragen {v['asks']}  "
              f"Laufzeit {v['secs_per_case']} s/Fall")
        for group, g in v["groups"].items():
            print(f"     {group:22s} FN {g['fn']:>2}/{g['pii']:<2}  FP {g['fp']:>2}/{g['keep']}")
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
