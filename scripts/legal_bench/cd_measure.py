"""P2.2-Messung auf dem Korpus `benchmarks/classification_deadlines_v1` (Dokumentklassifikation + Fristvorschlaege).

Trennung von Vorhersage und Bewertung: `score()` bewertet Vorhersagen JEDER Quelle (Lexono-Verarbeitungspfad, spaeter
Cloud-Modelle) nach denselben Kriterien. Rein lokal fuer die Lexono-Quelle: kein Modellaufruf, kein Netzwerk.

Aufruf: python -m scripts.legal_bench.cd_measure --split dev|test|alle --out <datei.json>

Vorhersage je Dokument: {"typ": str, "frist": [TT.MM.JJJJ], "termin": [...], "fristbeginn": [...], "fristdauern": int}
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

CORPUS = Path(__file__).resolve().parents[2] / "benchmarks" / "classification_deadlines_v1"
KINDS = ("frist", "termin", "fristbeginn")
UNKNOWN = {"Unbekannt", "Sonstiges"}
ALLOWED_TYPES = {
    "Rechnung", "Vollmacht", "Kündigungsschreiben", "Mahnung", "Klage/Schriftsatz", "Gerichtliches Schreiben", "Vertrag",
    "Steuerbescheid", "Einspruch", "Prüfungsanordnung", "Steuererklärung", "Urteil/Beschluss", "Bescheid", "Gutachten",
    "Protokoll", "Anlage", "Sonstiges", "Unbekannt",
}
_DATE = re.compile(r"\b\d{2}\.\d{2}\.\d{4}\b")


def split_file(split: str) -> Path:
    return CORPUS / "cases" / f"{split}.json"


def load(split: str) -> dict:
    return json.loads(split_file(split).read_text(encoding="utf-8"))


def sha256(split: str) -> str:
    return hashlib.sha256(split_file(split).read_bytes()).hexdigest()


def text_of(document: dict) -> str:
    table = document.get("table")
    rows = [" | ".join(r) for r in [table["header"], *table["rows"]]] if table else []
    return "\n".join([*document["paragraphs"], *rows])


def validate(data: dict) -> list[str]:
    problems, ids = [], set()
    for d in data["documents"]:
        if d["id"] in ids:
            problems.append(f"{d['id']}: doppelt")
        ids.add(d["id"])
        if d["format"] not in ("txt", "docx", "pdf", "scan", "scan_degraded"):
            problems.append(f"{d['id']}: Format {d['format']}")
        label = d["label"]
        if label["typ"] not in ALLOWED_TYPES or not set(label["zulaessig"]) <= ALLOWED_TYPES or label["typ"] not in label["zulaessig"]:
            problems.append(f"{d['id']}: Label {label}")
        text = text_of(d)
        for key in (*KINDS, "keine_frist", "nicht_bewertet"):
            for date in d["fristen"].get(key, []):
                if date not in text:
                    problems.append(f"{d['id']}: {key} {date} nicht im Text")
    return problems


# --- Bewertung (fuer alle Quellen identisch) -----------------------------------------------------------------------

def score(data: dict, predictions: dict[str, dict]) -> dict:
    docs = data["documents"]
    confusion: dict[str, Counter] = defaultdict(Counter)
    pred_count: Counter = Counter()
    pred_correct: Counter = Counter()
    gold_count: Counter = Counter()
    gold_correct: Counter = Counter()
    class_errors = []
    kind_stats = {k: Counter() for k in KINDS}
    deadline_errors = []
    durations = Counter()
    for d in docs:
        p = predictions.get(d["id"]) or {"typ": "Unbekannt", "frist": [], "termin": [], "fristbeginn": [], "fristdauern": 0,
                                         "fehler": "keine Vorhersage"}
        gold, accept, predicted = d["label"]["typ"], set(d["label"]["zulaessig"]), p["typ"]
        correct = predicted in accept
        confusion[gold][predicted] += 1
        gold_count[gold] += 1
        gold_correct[gold] += correct
        pred_count[predicted] += 1
        pred_correct[predicted] += correct
        if not correct:
            class_errors.append({"doc": d["id"], "gold": gold, "zulaessig": sorted(accept), "vorhersage": predicted,
                                 "art": "unbekannt_statt_typ" if predicted in UNKNOWN else "falscher_typ",
                                 "tags": d["tags"]})
        f = d["fristen"]
        ignored = set(f.get("nicht_bewertet", []))
        expected = {k: set(f.get(k, [])) - ignored for k in KINDS}
        not_deadline = set(f.get("keine_frist", [])) - ignored
        for kind in KINDS:
            predicted_dates = set(p.get(kind, [])) - ignored
            tp = predicted_dates & expected[kind]
            fn = expected[kind] - predicted_dates
            fp = predicted_dates - expected[kind]
            kind_stats[kind]["erwartet"] += len(expected[kind])
            kind_stats[kind]["richtig"] += len(tp)
            kind_stats[kind]["uebersehen"] += len(fn)
            kind_stats[kind]["falsch"] += len(fp)
            for date in sorted(fn):
                other = next((k for k in KINDS if k != kind and date in p.get(k, [])), None)
                deadline_errors.append({"doc": d["id"], "art": f"{kind}_uebersehen", "datum": date,
                                        "stattdessen": other, "tags": d["tags"]})
            for date in sorted(fp):
                if any(date in expected[k] for k in KINDS if k != kind):
                    reason = "andere_art"
                elif date in not_deadline:
                    reason = "keine_frist_als_" + kind
                else:
                    reason = "ungeklaert"
                kind_stats[kind]["falsch_" + reason] += 1
                deadline_errors.append({"doc": d["id"], "art": f"{kind}_falsch", "grund": reason, "datum": date,
                                        "tags": d["tags"]})
        exp_dur, pred_dur = f.get("fristdauern", 0), int(p.get("fristdauern", 0))
        durations["erwartet"] += exp_dur
        durations["erkannt"] += min(exp_dur, pred_dur)
        durations["zusaetzlich"] += max(0, pred_dur - exp_dur)
    # kritisch: keine Frist (Briefdatum, Ereignis, Gueltigkeit, Geburtsdatum) als FRIST vorgeschlagen; Frist uebersehen
    critical = [e for e in deadline_errors if e["art"] in ("frist_uebersehen",)
                or (e["art"] == "frist_falsch" and e.get("grund") == "keine_frist_als_frist")]
    per_class = {
        label: {"praezision": f"{pred_correct[label]}/{pred_count[label]}" if pred_count[label] else "-",
                "recall": f"{gold_correct[label]}/{gold_count[label]}" if gold_count[label] else "-"}
        for label in sorted(set(gold_count) | set(pred_count))
    }
    kern = [d for d in docs if d["label"]["art"] == "kern"]
    return {
        "dokumente": len(docs),
        "klassifikation": {
            "richtig": f"{sum(gold_correct.values())}/{len(docs)}",
            "kerntypen_richtig": f"{sum(1 for d in kern if predictions.get(d['id'], {}).get('typ') in d['label']['zulaessig'])}/{len(kern)}",
            "unbekannt_quote": f"{sum(1 for d in docs if predictions.get(d['id'], {}).get('typ', 'Unbekannt') in UNKNOWN)}/{len(docs)}",
            "unbekannt_bei_kerntypen": f"{sum(1 for d in kern if predictions.get(d['id'], {}).get('typ', 'Unbekannt') in UNKNOWN)}/{len(kern)}",
            "je_klasse": per_class,
            "verwechslungen": {gold: dict(c) for gold, c in sorted(confusion.items())
                               if any(pred not in set().union(*(set(d["label"]["zulaessig"]) for d in docs if d["label"]["typ"] == gold)) for pred in c)},
            "fehler": class_errors,
        },
        "fristen": {kind: dict(stats) for kind, stats in kind_stats.items()},
        "fristdauern": dict(durations),
        "kritisch": critical,
        "fristfehler": deadline_errors,
    }


# --- Vorhersage-Quelle: Lexono (echter Verarbeitungspfad) ----------------------------------------------------------

def _render(document: dict, outdir: Path) -> Path:
    from scripts.legal_bench.render import render_degraded_scan, render_document

    spec = {**document, "format": "scan" if document["format"] == "scan_degraded" else document["format"]}
    if document["format"] == "scan_degraded":
        outdir.mkdir(parents=True, exist_ok=True)
        path = outdir / f"{Path(document['filename']).stem}.pdf"
        render_degraded_scan(spec, path)
        return path
    return render_document(spec, outdir)


def _kind_of(deadline) -> str:  # noqa: ANN001
    reasoning = deadline.reasoning or ""
    return next((k for k in ("Termin", "Fristbeginn", "Fristdauer") if f"Art: {k}" in reasoning), "Frist")


def lexono_predictions(data: dict, workdir: Path) -> tuple[dict, dict]:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.documents.service import DocumentProcessingService
    from app.ingestion.intake import IntakeService
    from app.models import Client, Deadline, Matter
    from app.models.base import Base
    from scripts.legal_bench.e2e import _tesseract

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    tesseract = _tesseract()
    processing = DocumentProcessingService(ocr_enabled=tesseract is not None, tesseract_cmd=tesseract)
    intake = IntakeService(workdir / "intake")
    client = Client(name="P22 Messung")
    matter = Matter(client=client, title=f"P22 {data['split']}")
    db.add_all([client, matter])
    db.commit()
    predictions, meta = {}, {"ocr": tesseract is not None, "sekunden": {}, "texte": {}}
    for d in data["documents"]:
        t0 = time.perf_counter()
        path = _render(d, workdir / "rendered" / d["id"])
        document = intake.ingest_file(path, db, stability_timeout_seconds=5.0)
        document.matter_id = matter.id
        db.commit()
        processing.process_document(document, db, actor="p22")
        db.refresh(document)
        meta["sekunden"][d["id"]] = round(time.perf_counter() - t0, 2)
        # Eingabe fuer den Modellvergleich: derselbe extrahierte Text (inkl. OCR-Fehler), den Lexono verarbeitet hat
        meta["texte"][d["id"]] = document.extracted_text or ""
        pred = {"typ": document.classified_type or "Unbekannt", "frist": [], "termin": [], "fristbeginn": [], "fristdauern": 0}
        for deadline in db.query(Deadline).filter(Deadline.document_id == document.id):
            kind = _kind_of(deadline)
            raw = (deadline.source_text or "").split(" :: ", 1)[0]
            if kind == "Fristdauer":
                pred["fristdauern"] += 1
            elif kind == "Fristbeginn":
                pred["fristbeginn"] += _DATE.findall(raw)[:1]
            elif deadline.due_date:
                pred["termin" if kind == "Termin" else "frist"].append(deadline.due_date.strftime("%d.%m.%Y"))
        predictions[d["id"]] = pred
    return predictions, meta


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", default="alle", choices=["dev", "dev_v2", "test", "test_v2", "alle"])
    parser.add_argument("--out", required=True)
    parser.add_argument("--texts-out", help="extrahierte Texte je Dokument (Eingabe fuer den Modellvergleich)")
    args = parser.parse_args()
    texts: dict = {}
    splits = ["dev", "dev_v2", "test", "test_v2"] if args.split == "alle" else [args.split]
    result: dict = {"quelle": "lexono", "splits": {}}
    for split in splits:
        if not split_file(split).exists():
            print(f"[{split}] fehlt - uebersprungen")
            continue
        data = load(split)
        problems = validate(data)
        if problems:
            raise SystemExit(f"[{split}] Korpus ungueltig: {problems}")
        with tempfile.TemporaryDirectory() as tmp:
            t0 = time.perf_counter()
            predictions, meta = lexono_predictions(data, Path(tmp))
            seconds = round(time.perf_counter() - t0, 2)
        texts[split] = meta["texte"]
        scored = score(data, predictions)
        result["splits"][split] = {"sha256": sha256(split), "ocr": meta["ocr"], "laufzeit_sekunden": seconds,
                                   "sekunden_je_dokument": meta["sekunden"], "bewertung": scored,
                                   "vorhersagen": predictions}
        k, f = scored["klassifikation"], scored["fristen"]
        print(f"[{split}] n={scored['dokumente']} Klassifikation richtig {k['richtig']}, Kerntypen {k['kerntypen_richtig']}, "
              f"Unbekannt {k['unbekannt_quote']}, Fehler {len(k['fehler'])}")
        print(f"[{split}] Fristen {f['frist']} | Termine {f['termin']} | Fristbeginn {f['fristbeginn']} | "
              f"Dauern {scored['fristdauern']} | kritisch {len(scored['kritisch'])} | {seconds} s")
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.texts_out:
        Path(args.texts_out).write_text(json.dumps(texts, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
