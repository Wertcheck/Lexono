"""Messung der Pseudonymisierung ganzer synthetischer Dokumente (§43.10, Korpus `benchmarks/pii_full_documents_v1`).

Echter Pfad: Rendern (inkl. Scan/schlechter Scan) -> Import -> Text/OCR (`DocumentProcessingService`) -> bestehender
`ClaudePrivacyGateway.prepare_request(purpose=SECOND_OPINION_PURPOSE)`. Kein Netz, kein Cloud-Aufruf.

Zwei Sichten je Dokument, je mit und ohne `known_entities` (was die Akte vorab kennt):
- **Versand** (`review_mode="ask"`, wie die Zweitmeinung): Status (gesendet/Rueckfrage/blockiert) und Klartexttreffer im
  tatsaechlich sendefertigen Text (`anonymisierter_sachverhalt`). Blockiert = nichts gesendet.
- **Erkennung** (`review_mode="mask"`): Klartexttreffer im pseudonymisierten Text unabhaengig von einer Blockade -
  misst die Erkennungsleistung je Kategorie (bei Blockade durch den Restscan: Pseudonymizer allein mit demselben
  NER-Filter, ohne Rueckfrage-Maskierung - konservative Untergrenze).

Gold sind die vorab festgelegten `pii`-Werte. Ein Wert, den die OCR veraendert hat, wird ueber die aehnlichste Stelle
im extrahierten Text (Aehnlichkeit >= 0,8) gesucht. "Teil-Leck": ein Namensbestandteil (>= 3 Buchstaben) einer Person
steht weiter als eigenes Wort im Text. `keep`-Werte, die verschwinden, zaehlen als Uebermaskierung.

Aufruf: python -m scripts.legal_bench.pii_measure --out <ergebnis.json> [--texts <texte.json>]"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
import time
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

CORPUS = Path(__file__).resolve().parents[2] / "benchmarks" / "pii_full_documents_v1"
SPLIT_FILE = CORPUS / "cases" / "test.json"


def load(path: Path = SPLIT_FILE) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha256(path: Path = SPLIT_FILE) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _occurrences(value: str, text: str) -> int:
    pattern = r"(?<![\wÄÖÜäöüß])" + re.escape(value) + r"(?![\wÄÖÜäöüß])"
    return len(re.findall(pattern, text))


def _ocr_variant(value: str, text: str) -> str | None:
    """Aehnlichste Stelle gleicher Laenge (+-2) im Text, wenn der Wert dort nicht woertlich steht (OCR-Fehler)."""
    if len(value) < 5:
        return None
    best, best_ratio = None, 0.0
    for width in (len(value) - 1, len(value), len(value) + 1):
        for start in range(0, max(0, len(text) - width) + 1):
            window = text[start:start + width]
            if window[:1].lower() != value[:1].lower() and window[-1:].lower() != value[-1:].lower():
                continue
            ratio = SequenceMatcher(None, value.lower(), window.lower()).ratio()
            if ratio > best_ratio:
                best, best_ratio = window, ratio
    return best if best_ratio >= 0.8 else None


def _name_tokens(value: str) -> list[str]:
    return [t for t in re.split(r"[\s,]+", value) if len(t) >= 3 and t[0].isupper() and not t.endswith(".")]


def _pseudonymizer_only(gateway, text: str, known) -> str:  # noqa: ANN001
    """Fuer vom Restscan blockierte Dokumente (ohne Payload): derselbe Pseudonymizer und NER-Filter wie im Gateway,
    aber ohne die zusaetzliche Rueckfrage-Maskierung - eine konservative Untergrenze der Erkennung."""
    from app.privacy.presidio_ner import drop_sentence_initial_imperatives

    pseudonymized, _ = gateway.pseudonymizer.pseudonymize(text, known_entities=known,
                                                          ner_span_filter=drop_sentence_initial_imperatives)
    return pseudonymized


def leaks(document: dict, extracted: str, output: str) -> dict:
    """Vergleicht Gold-PII mit dem Text nach der Pseudonymisierung."""
    per_value = []
    for item in document["pii"]:
        value, category = item["value"], item["category"]
        present = _occurrences(value, extracted)
        form = value
        if present == 0:
            variant = _ocr_variant(value, extracted)
            if variant:
                form, present = variant, max(1, extracted.count(variant))
        remaining = _occurrences(form, output) if present else 0
        partial = []
        if category == "person" and present and not remaining:
            partial = [t for t in _name_tokens(form) if _occurrences(t, output)]
        per_value.append({"value": value, "category": category, "im_text": present, "verbleibend": remaining,
                          "ocr_form": form if form != value else None, "teil_leck": partial})
    keep_lost = [k for k in document.get("keep", []) if _occurrences(k, extracted) and not _occurrences(k, output)]
    # §43.11: gewoehnliche Verwendung eines Wortes, das zugleich ein Name ist ("im Winter 2025") - Uebermaskierung
    ordinary_lost = [k for k in document.get("keep_gewoehnlich", [])
                     if _occurrences(k, extracted) and not _occurrences(k, output)]
    return {"werte": per_value, "keep_verloren": keep_lost, "gewoehnlich_verloren": ordinary_lost}


def _summarise(results: dict, view: str) -> dict:
    by_cat: dict[str, Counter] = defaultdict(Counter)
    for doc in results.values():
        for item in doc[view]["werte"]:
            c = by_cat[item["category"]]
            c["im_text"] += item["im_text"]
            c["verbleibend"] += item["verbleibend"]
            c["werte_mit_leck"] += 1 if item["verbleibend"] else 0
            c["werte_mit_teil_leck"] += 1 if item["teil_leck"] else 0
            c["werte"] += 1 if item["im_text"] else 0
    return {cat: {**c, "erkannt_quote": f"{c['im_text'] - c['verbleibend']}/{c['im_text']}"}
            for cat, c in sorted(by_cat.items())}


def extract_texts(data: dict, workdir: Path) -> tuple[dict, bool]:
    from scripts.legal_bench.cd_measure import lexono_predictions

    _, meta = lexono_predictions({"split": "pii", "documents": data["documents"]}, workdir)
    return meta["texte"], meta["ocr"]


def run(data: dict, texts: dict[str, str]) -> dict:
    from app.privacy.gateway import ClaudePrivacyGateway
    from app.privacy.gateway_schema import SECOND_OPINION_PURPOSE

    gateway = ClaudePrivacyGateway()
    out: dict = {}
    for document in data["documents"]:
        text = texts[document["id"]]
        entry: dict = {"zeichen": len(text)}
        for label, known in (("ohne_akte", None), ("mit_akte", document.get("known_entities") or None)):
            ask = gateway.prepare_request(purpose=SECOND_OPINION_PURPOSE, sachverhalt=text, known_entities=known,
                                          review_mode="ask")
            if ask.allowed:
                status, sent = "gesendet", ask.payload.anonymisierter_sachverhalt
            else:
                status, sent = ("rueckfrage" if ask.review_required else "blockiert"), ""
            mask = gateway.prepare_request(purpose=SECOND_OPINION_PURPOSE, sachverhalt=text, known_entities=known,
                                           review_mode="mask")
            masked = (mask.payload.anonymisierter_sachverhalt if mask.allowed
                      else _pseudonymizer_only(gateway, text, known))
            entry[label] = {
                "versand_status": status,
                "rueckfrage_begriffe": len(ask.review_candidates),
                "versand": (leaks(document, text, sent) if sent
                            else {"werte": [], "keep_verloren": [], "gewoehnlich_verloren": []}),
                "erkennung_status": "frei" if mask.allowed else "blockiert",
                "erkennung": leaks(document, text, masked),
            }
        out[document["id"]] = entry
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--cases", default=str(SPLIT_FILE), help="Korpusdatei (Standard: v1 test.json)")
    parser.add_argument("--texts", help="bereits extrahierte Texte wiederverwenden (JSON id -> Text)")
    parser.add_argument("--texts-out", help="extrahierte Texte zusaetzlich speichern")
    args = parser.parse_args()
    data = load(Path(args.cases))
    started = time.perf_counter()
    if args.texts:
        raw = Path(args.texts).read_bytes()
        texts = json.loads(raw.decode("utf-8"))
        ocr = f"aus gespeicherten Texten {Path(args.texts).name} (sha256 {hashlib.sha256(raw).hexdigest()[:16]})"
    else:
        with tempfile.TemporaryDirectory(prefix="pii_measure_") as tmp:
            texts, ocr = extract_texts(data, Path(tmp))
        if args.texts_out:
            Path(args.texts_out).write_text(json.dumps(texts, ensure_ascii=False, indent=1), encoding="utf-8")
    results = run(data, texts)
    corpus_name = Path(args.cases).as_posix().split("benchmarks/")[-1]
    report = {"korpus": corpus_name, "korpus_sha256": sha256(Path(args.cases)), "dokumente": len(data["documents"]),
              "ocr": ocr, "sekunden": round(time.perf_counter() - started, 1), "zusammenfassung": {}, "dokumente_ergebnis": results}
    for label in ("ohne_akte", "mit_akte"):
        flat = {k: v[label] for k, v in results.items()}
        report["zusammenfassung"][label] = {
            "versand_status": dict(Counter(v["versand_status"] for v in flat.values())),
            "versand_leck_werte": sum(1 for v in flat.values() for w in v["versand"]["werte"] if w["verbleibend"]),
            "versand_teil_leck_werte": sum(1 for v in flat.values() for w in v["versand"]["werte"] if w["teil_leck"]),
            "erkennung_je_kategorie": _summarise(flat, "erkennung"),
            "erkennung_dokumente_mit_leck": sorted(k for k, v in flat.items()
                                                   if any(w["verbleibend"] for w in v["erkennung"]["werte"])),
            "keep_verloren": {k: v["erkennung"]["keep_verloren"] for k, v in flat.items()
                              if v["erkennung"]["keep_verloren"]},
            "gewoehnlich_verloren": {k: v["erkennung"].get("gewoehnlich_verloren", []) for k, v in flat.items()
                                     if v["erkennung"].get("gewoehnlich_verloren")},
        }
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8",
                              newline="\n")
    print(json.dumps(report["zusammenfassung"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
